"""ComfyUI simulado para testar o Wan Studio sem GPU.

Imita o que importa do ComfyUI real (conferido contra a versao 0.39): fila de um job por vez,
validacao de arquivos de modelo, progresso por websocket so para o clientId do job, historico
com as mesmas chaves e saidas reais (um mp4 e um png de verdade).

Varios simuladores (um por "GPU") podem rodar ao mesmo tempo, cada um na sua porta. Como os
ComfyUI de verdade, eles dividem a pasta de modelos: com FAKE_SHARED apontando para o mesmo
arquivo, o que /_install instala num aparece em todos.
"""
import asyncio
import json
import os
import sys
import subprocess
import tempfile
import uuid
from pathlib import Path

from aiohttp import web

ASSETS = Path(os.environ.get("FAKE_ASSETS", Path(__file__).resolve().parent))
SHARED = os.environ.get("FAKE_SHARED")  # arquivo com os conjuntos instalados em tempo de execucao
STEP = float(os.environ.get("FAKE_STEP", "0.35"))
GPU = os.environ.get("FAKE_GPU", "Tesla T4")  # a placa que o simulador diz ter, e a memoria dela
VRAM = int(float(os.environ.get("FAKE_VRAM_GB", "15")) * 2**30)
PORT = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8188

FIELDS = {"UNETLoader": "unet_name", "UnetLoaderGGUF": "unet_name", "LoraLoaderModelOnly": "lora_name",
          "VAELoader": "vae_name", "CLIPLoader": "clip_name", "CLIPVisionLoader": "clip_name",
          "OnnxDetectionModelLoader": "vitpose_model", "UpscaleModelLoader": "model_name",
          "FrameInterpolationModelLoader": "model_name", "WanStudioForegroundLoader": "model"}
INSTALLED = {
    "UNETLoader": ["wan2.2_ti2v_5B_fp16.safetensors"],
    "UnetLoaderGGUF": [],
    "LoraLoaderModelOnly": ["Wan2_2_5B_FastWanFullAttn_lora_rank_128_bf16.safetensors"],
    "VAELoader": ["wan2.2_vae.safetensors", "pixel_space"],
    "CLIPLoader": ["umt5_xxl_fp8_e4m3fn_scaled.safetensors"],
    "CLIPVisionLoader": [],
    "OnnxDetectionModelLoader": [],
    "UpscaleModelLoader": [],
    "FrameInterpolationModelLoader": [],
    "WanStudioForegroundLoader": [],
}
# Nos de pacotes extras: existem desde o inicio (sao instalados antes de o ComfyUI subir);
# o que chega depois sao os arquivos de modelo.
EXTRA_NODES = {"WanAnimateToVideo", "PoseAndFaceDetection", "DrawViTPose", "DownloadAndLoadSAM2Model",
               "WanStudioPersonMask", "WanStudioFitReference", "WanStudioWindow", "WanStudioPaste",
               "WanStudioWholePerson", "WanStudioHeadWindow", "WanStudioFacePaste", "RepeatImageBatch", "ImageFromBatch"}
# Quanto cada no "demora", para o teste enxergar as etapas.
SLOW_NODES = {"PoseAndFaceDetection": 2.5, "WanStudioPersonMask": 2.5}
uploaded = {}  # nome -> conteudo dos arquivos enviados
EXTRA_SETS = {
    "animate": {
        "UnetLoaderGGUF": ["Wan2.2-Animate-14B-Q4_K_S.gguf"],
        "UNETLoader": ["seedvr2_3b_fp8_e4m3fn.safetensors"],
        "LoraLoaderModelOnly": ["lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors",
                                "WanAnimate_relight_lora_fp16.safetensors"],
        "VAELoader": ["wan_2.1_vae.safetensors", "seedvr2_ema_vae_fp16.safetensors"],
        "CLIPVisionLoader": ["clip_vision_h.safetensors"],
        "OnnxDetectionModelLoader": ["vitpose-l-wholebody.onnx", "yolov10m.onnx"],
        "UpscaleModelLoader": ["RealESRGAN_x2plus.pth"],
        "FrameInterpolationModelLoader": ["rife_v4.25.safetensors"],
        "WanStudioForegroundLoader": ["BiRefNet-portrait"],
    },
    # O mesmo modelo sem compressao GGUF: o que o instalador baixa numa placa de 24 GB ou mais.
    "animate_fp8": {"UNETLoader": ["Wan2_2-Animate-14B_fp8_e4m3fn_scaled_KJ.safetensors"]},
    "14b_i2v": {
        "UnetLoaderGGUF": ["Wan2.2-I2V-A14B-HighNoise-Q4_K_M.gguf", "Wan2.2-I2V-A14B-LowNoise-Q4_K_M.gguf"],
        "LoraLoaderModelOnly": ["wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
                                "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors"],
        "VAELoader": ["wan_2.1_vae.safetensors"],
    },
    "14b_t2v": {
        "UnetLoaderGGUF": ["Wan2.2-T2V-A14B-HighNoise-Q4_K_M.gguf", "Wan2.2-T2V-A14B-LowNoise-Q4_K_M.gguf"],
        "LoraLoaderModelOnly": ["wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors",
                                "wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors"],
        "VAELoader": ["wan_2.1_vae.safetensors"],
    },
}


def add_set(name):
    for node, values in EXTRA_SETS.get(name.strip(), {}).items():
        INSTALLED[node] += [v for v in values if v not in INSTALLED[node]]


def refresh_sets():
    """Enxerga o que outro simulador instalou (a pasta de modelos e uma so)."""
    if SHARED and os.path.exists(SHARED):
        for name in Path(SHARED).read_text(encoding="utf-8").split():
            add_set(name)


for name in os.environ.get("FAKE_SETS", "").split(","):
    add_set(name)

queue = None
pending, running, history, sockets, graphs = [], {}, {}, {}, {}
state = {"interrupt": None}


def sampler_steps(node):
    inputs = node["inputs"]
    if node["class_type"] == "KSampler":
        return inputs["steps"]
    if node["class_type"] == "KSamplerAdvanced":
        return min(inputs["end_at_step"], inputs["steps"]) - inputs["start_at_step"]
    if node["class_type"] == "SamplerCustom":
        return 3
    return 0


def video_frames(name):
    """Quantos quadros tem um video enviado (o simulador nao decodifica nada: pergunta ao ffprobe)."""
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as handle:
        handle.write(uploaded[name])
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                              "stream=nb_read_frames", "-of", "csv=p=0", handle.name], capture_output=True, text=True).stdout
        return int(out.strip().split(",")[0] or 1)
    except ValueError:
        return 1
    finally:
        os.unlink(handle.name)


async def send(client_id, message):
    ws = sockets.get(client_id)
    if ws is not None and not ws.closed:
        await ws.send_json(message)


async def execute(pid, graph, client_id):
    interrupted = {"outputs": {}, "status": {"status_str": "error", "completed": False,
                                             "messages": [["execution_interrupted", {"prompt_id": pid}]]}}
    await asyncio.sleep(1.0)  # "carregando o modelo"
    for node_id, node in graph.items():
        await send(client_id, {"type": "executing", "data": {"node": node_id, "display_node": node_id, "prompt_id": pid}})
        await asyncio.sleep(SLOW_NODES.get(node["class_type"], 0))
        if state["interrupt"] == pid:  # como no ComfyUI: a interrupcao vale entre um no e outro
            return interrupted
        steps = sampler_steps(node)
        for step in range(1, steps + 1):
            await asyncio.sleep(STEP)
            if state["interrupt"] == pid:
                return interrupted
            await send(client_id, {"type": "progress", "data": {"value": step, "max": steps, "prompt_id": pid, "node": node_id}})
    if "FAIL" in json.dumps(graph):
        # Com o alocador padrao do ComfyUI (cudaMallocAsync) a falta de memoria vem assim, sem
        # as palavras "out of memory"; so o tipo da excecao a identifica.
        return {"outputs": {}, "status": {"status_str": "error", "completed": False, "messages": [
            ["execution_start", {"prompt_id": pid}],
            ["execution_error", {"prompt_id": pid, "node_type": "KSampler",
                                 "exception_type": "torch.OutOfMemoryError",
                                 "exception_message": "Allocation on device \n"}]]}}
    outputs = {}
    for node_id, node in graph.items():
        prefix = str(node["inputs"].get("filename_prefix", "x")).split("/")[-1]
        if node["class_type"] == "LoadVideo":
            # Como no ComfyUI real: o LoadVideo devolve o proprio video enviado como previa, e
            # ela aparece no historico antes do resultado. Quem le o historico nao pode
            # confundir os dois (foi um defeito de verdade na primeira troca de pessoa).
            outputs[node_id] = {"images": [{"filename": node["inputs"]["file"], "subfolder": "", "type": "input"}],
                                "animated": [True]}
        elif node["class_type"] == "SaveVideo":
            outputs[node_id] = {"images": [{"filename": f"{prefix}_00001_.mp4", "subfolder": "wanstudio", "type": "output"}],
                                "animated": [True]}
        elif node["class_type"] == "SaveImage":
            # Um arquivo por imagem do lote: um pedaco da troca de pessoa grava todos os quadros.
            count = max([other["inputs"]["length"] for other in graph.values()
                         if other["class_type"] == "WanAnimateToVideo"] or [0])
            # (o rosto em detalhe poe um segundo Animate no grafo, com o mesmo numero de quadros)
            # Uma imagem tirada do lote (a troca de rosto em foto): so ela e gravada.
            picked = [other["inputs"]["length"] for other in graph.values() if other["class_type"] == "ImageFromBatch"]
            if picked:
                count = picked[0]
            if not count:  # restauracao de um pedaco ja gerado: tantos quadros quantos o video enviado tem
                count = max([video_frames(other["inputs"]["file"]) for other in graph.values()
                             if other["class_type"] == "LoadVideo"] or [1])
            # A interpolacao poe `multiplier` - 1 quadros entre cada dois.
            for other in graph.values():
                if other["class_type"] == "FrameInterpolate":
                    count = (count - 1) * other["inputs"]["multiplier"] + 1
            outputs[node_id] = {"images": [{"filename": f"{prefix}_{number:05d}_.png", "subfolder": "wanstudio",
                                            "type": "output"} for number in range(1, count + 1)]}
    await asyncio.sleep(0.6)  # VAE + codificacao
    return {"outputs": outputs, "status": {"status_str": "success", "completed": True,
                                           "messages": [["execution_start", {}], ["execution_success", {}]]}}


async def worker():
    while True:
        pid, graph, client_id = await queue.get()
        if pid not in pending:  # removido da fila
            continue
        pending.remove(pid)
        running[pid] = graph
        try:
            history[pid] = await execute(pid, graph, client_id)
        finally:
            running.pop(pid, None)


async def post_prompt(request):
    body = await request.json()
    graph = body["prompt"]
    errors = {}
    refresh_sets()
    for node_id, node in graph.items():
        field = FIELDS.get(node["class_type"])
        if field and node["inputs"].get(field) not in INSTALLED[node["class_type"]]:
            errors[node_id] = {"class_type": node["class_type"], "errors": [
                {"type": "value_not_in_list", "message": "Value not in list",
                 "details": f"{field}: '{node['inputs'].get(field)}' not in list"}]}
        # Como no ComfyUI real: imagem e video precisam ter sido enviados antes.
        for key in {"LoadImage": ("image",), "LoadVideo": ("file",)}.get(node["class_type"], ()):
            if node["inputs"].get(key) not in uploaded:
                errors[node_id] = {"class_type": node["class_type"], "errors": [
                    {"type": "value_not_in_list", "message": "Value not in list",
                     "details": f"{key}: '{node['inputs'].get(key)}' not in list"}]}
        for value in node["inputs"].values():
            if isinstance(value, list) and len(value) == 2 and str(value[0]) not in graph:
                errors[node_id] = {"class_type": node["class_type"], "errors": [{"message": "link quebrado", "details": str(value)}]}
    if errors:
        return web.json_response({"error": {"type": "prompt_outputs_failed_validation",
                                            "message": "Prompt outputs failed validation"}, "node_errors": errors}, status=400)
    pid = str(uuid.uuid4())
    pending.append(pid)
    graphs[pid] = graph
    await queue.put((pid, graph, body.get("client_id")))
    return web.json_response({"prompt_id": pid, "number": len(graphs), "node_errors": {}})


async def ws_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    client_id = request.query.get("clientId", "")
    sockets[client_id] = ws
    try:
        async for _ in ws:
            pass
    finally:
        if sockets.get(client_id) is ws:
            del sockets[client_id]
    return ws


async def upload(request):
    data = await request.post()
    uploaded[data["image"].filename] = data["image"].file.read()
    return web.json_response({"name": data["image"].filename, "subfolder": "", "type": "input"})


async def object_info(request):
    refresh_sets()
    node = request.match_info["node"]
    if node in EXTRA_NODES:
        return web.json_response({node: {"input": {"required": {}}}})
    if node not in INSTALLED:
        return web.json_response({})
    return web.json_response({node: {"input": {"required": {FIELDS[node]: [INSTALLED[node], {}]}}}})


async def post_queue(request):
    body = await request.json()
    for pid in list(pending) if body.get("clear") else body.get("delete", []):
        if pid in pending:
            pending.remove(pid)
    return web.Response(status=200)


async def post_interrupt(request):
    try:
        body = await request.json()
    except json.JSONDecodeError:
        body = {}
    pid = body.get("prompt_id")
    if pid in running:
        state["interrupt"] = pid
    return web.Response(status=200)


async def view(request):
    name = request.query["filename"]
    if request.query.get("type") == "input":  # um arquivo enviado, devolvido como esta
        return web.Response(body=uploaded[name], content_type="video/mp4" if name.endswith(".mp4") else "image/png")
    sample = ASSETS / ("sample.png" if name.endswith(".png") else "sample.mp4")
    return web.FileResponse(sample)


async def free(_request):
    """Como no ComfyUI: solta os modelos. O simulador so anota quantos grafos tinha recebido."""
    state.setdefault("freed", []).append(len(graphs))
    return web.Response(status=200)


async def control(request):
    """So do simulador: instala um conjunto de modelos com o servidor rodando."""
    body = await request.json()
    add_set(body["set"])
    if SHARED:
        with open(SHARED, "a", encoding="utf-8") as handle:
            handle.write(body["set"] + "\n")
    return web.json_response(INSTALLED)


async def on_start(app):
    global queue
    queue = asyncio.Queue()
    app["worker"] = asyncio.create_task(worker())


app = web.Application(client_max_size=100 * 2**20)  # o teto de envio padrao do ComfyUI (100 MB)
app.on_startup.append(on_start)
app.add_routes([
    web.get("/object_info/{node}", object_info),
    web.get("/system_stats", lambda r: web.json_response(
        {"devices": [{"name": f"cuda:0 {GPU} : cudaMallocAsync", "type": "cuda", "vram_total": VRAM}]})),
    web.post("/upload/image", upload),
    web.post("/prompt", post_prompt),
    web.get("/queue", lambda r: web.json_response({"queue_running": [[0, p, {}] for p in running],
                                                   "queue_pending": [[0, p, {}] for p in pending]})),
    web.post("/queue", post_queue),
    web.post("/interrupt", post_interrupt),
    web.get("/history/{pid}", lambda r: web.json_response(
        {r.match_info["pid"]: history[r.match_info["pid"]]} if r.match_info["pid"] in history else {})),
    web.get("/view", view),
    web.get("/_graphs", lambda r: web.json_response(graphs)),
    web.get("/_state", lambda r: web.json_response({"freed": state.get("freed", [])})),
    web.post("/_install", control),
    web.post("/free", free),
    web.get("/_uploaded", lambda r: web.json_response(sorted(uploaded))),
    web.get("/ws", ws_handler),
])
web.run_app(app, host="127.0.0.1", port=PORT, print=None)
