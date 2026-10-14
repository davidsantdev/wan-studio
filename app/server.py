"""Wan Studio: API e fila de jobs em cima de um ou mais ComfyUI com Wan 2.2.

Serve a interface em "/" (mesma origem) e tambem aceita o site estatico hospedado em outro
endereco, que fala com esta API usando a senha (veja `cors`).

Cada ComfyUI (um por GPU) e um "worker". Um job vira uma ou mais tarefas; cada worker roda uma
tarefa por vez. A troca de pessoa e cortada em pedacos, um por tarefa, para que as GPUs gerem
ao mesmo tempo; no fim os pedacos sao emendados (stitch.py).
"""

import asyncio
import base64
import hmac
import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path

import aiohttp
from aiohttp import web

import presets
import stitch
import workflows
from enhancer import Enhancer

# Um endereco por GPU, separados por virgula. Todos compartilham a mesma instalacao, entao o
# primeiro responde pelo catalogo de modelos.
COMFY_URLS = [url.strip().rstrip("/") for url in os.environ.get(
    "COMFY_URLS", os.environ.get("COMFY_URL", "http://127.0.0.1:8188")).split(",") if url.strip()]
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8080"))
PASSWORD = os.environ.get("APP_PASSWORD", "")
IDLE_MINUTES = float(os.environ.get("IDLE_MINUTES", "0"))
# O launcher do notebook define isto: ha alguem vigiando para desligar tudo quando pedido.
MANAGED = os.environ.get("WAN_MANAGED") == "1"
# O worker cuja GPU e a mesma do melhorador de prompt (-1: nenhum). Os dois nao cabem juntos
# na memoria: quando esse worker entra em uso o melhorador sai, e volta depois.
ENHANCER_WORKER = int(os.environ.get("ENHANCER_WORKER", "-1"))
# A sessao so faz troca de pessoa (vm/setup.sh nao baixou os outros modelos nem o melhorador
# de prompt): o site mostra so esse modo.
ONLY_SWAP = bool(os.environ.get("SO_TROCA"))
# Cinco minutos: quem faz uma troca de pessoa atras da outra nao espera o melhorador recarregar
# (um minuto) e descarregar de novo a cada pedido.
ENHANCER_RETURN_AFTER = float(os.environ.get("ENHANCER_RETURN_AFTER", "300"))
# Aquecer cada GPU para a troca de pessoa assim que ela estiver instalada (veja `warm_up`).
WARM_UP = os.environ.get("WARM_UP", "1") == "1"
# Quanto esta maquina custa por hora ligada, em dolares (0: nada, como no Kaggle). Quem a liga
# informa (nuvem/sessao.py); serve para o site mostrar quanto cada video gasta do credito.
PRICE_HOUR = float(os.environ.get("WAN_PRICE_HOUR") or 0)

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
STATIC = BASE / "static"
DATA = Path(os.environ.get("DATA_DIR", ROOT / "data"))
OUTPUTS = DATA / "outputs"
JOBS_FILE = DATA / "jobs.json"
# vm/setup.sh escreve aqui, em uma linha, o que esta baixando em segundo plano.
EXTRAS_STATUS = Path(os.environ.get("EXTRAS_STATUS", ROOT / "logs" / "extras.status"))
# Registros que /api/log entrega: cada ComfyUI, este servidor e os downloads em segundo plano.
LOGS = Path(os.environ.get("LOG_DIR", ROOT / "logs"))
LOG_NAMES = ("studio", "extras", "comfyui") + tuple(f"comfyui{i}" for i in range(1, len(COMFY_URLS)))
# Quando um ComfyUI cai e e reiniciado, o launcher guarda o registro anterior com este sufixo.
LOG_NAMES += tuple(f"{name}.anterior" for name in LOG_NAMES if name.startswith("comfyui"))
MAX_LOG_BYTES = 200 * 1024

# O que um job precisa enquanto roda: o video recebido, o video preparado e os quadros gerados.
UPLOADS = DATA / "uploads"
# A pasta do ComfyUI, quando ele roda nesta maquina (o launcher e vm/start.sh informam): o
# servidor apaga de la o que ja buscou. Uma troca de 60 s grava uns 500 MB de quadros.
COMFY_DIR = Path(os.environ["COMFY_DIR"]) if os.environ.get("COMFY_DIR") else None

MAX_IMAGE_BYTES = 30 * 1024 * 1024
MAX_VIDEO_BYTES = 100 * 1024 * 1024  # tambem e o teto do tunel gratuito da Cloudflare
MAX_BATCH = 4
ENHANCE_TIMEOUT = 90
FFMPEG_TIMEOUT = 180
CATALOG_TTL = 5
NODES_TTL = 60
POLL_SECONDS = 1.5
WORKER_RETRY = 20        # segundos sem tentar de novo um worker que nao respondeu
# Teto para as consultas de fila de cada ComfyUI: um que parou de responder (aconteceu com o
# disco travado) nao pode segurar a entrega de tarefas ao outro.
QUEUE_TIMEOUT = aiohttp.ClientTimeout(total=20)
DOWNLOADS_AT_ONCE = 6
SUBMITTED = ("queued", "running")
ACTIVE = ("preparing",) + SUBMITTED
# Campos de trabalho do job: nao vao para a interface nem para o disco.
PRIVATE_FIELDS = ("tasks", "files", "inputs", "positive", "negative", "workdir", "plan", "generate")
EXTENSIONS = {"video": ".mp4", "image": ".png"}

# Onde o ComfyUI lista cada tipo de arquivo: chave -> (no, campo).
MODEL_LISTS = {
    "unet": ("UNETLoader", "unet_name"),
    "gguf": ("UnetLoaderGGUF", "unet_name"),
    "lora": ("LoraLoaderModelOnly", "lora_name"),
    "vae": ("VAELoader", "vae_name"),
    "clip": ("CLIPLoader", "clip_name"),
    "clip_vision": ("CLIPVisionLoader", "clip_name"),
    "detection": ("OnnxDetectionModelLoader", "vitpose_model"),
    "upscale": ("UpscaleModelLoader", "model_name"),
    "frame_interpolation": ("FrameInterpolationModelLoader", "model_name"),
    "foreground": ("WanStudioForegroundLoader", "model"),
}
# Nos de pacotes extras cuja presenca decide se um conjunto pode ser usado.
EXTRA_NODES = sorted({node for entry in workflows.CATALOG.values() for node in entry.get("nodes", ())})

# Opcoes do ComfyUI que /api/worker/N/restart aceita trocar (alem de numeros, valores delas).
RESTART_ARGS = {"--use-sage-attention", "--use-pytorch-cross-attention", "--fast", "fp16_accumulation",
                "cublas_ops", "autotune", "--highvram", "--normalvram", "--reserve-vram", "--cache-none"}

JOBS = {}
WORKERS = [{"index": index, "url": url, "client_id": uuid.uuid4().hex, "task": None,
            "idle_since": time.time(), "retry_at": 0.0, "uploaded": {}, "warm": False}
           for index, url in enumerate(COMFY_URLS)]
STATE = {"activity": time.time(), "shutdown": False, "restart": None, "catalog": None,
         "catalog_at": 0.0, "nodes": [], "nodes_at": 0.0, "vram": None}
enhancer = Enhancer()


def public(job):
    return {k: v for k, v in job.items() if k not in PRIVATE_FIELDS}


def save_jobs():
    tmp = JOBS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps([public(job) for job in JOBS.values()], ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(JOBS_FILE)


def load_jobs():
    if not JOBS_FILE.exists():
        return
    for job in json.loads(JOBS_FILE.read_text(encoding="utf-8")):
        if job["status"] in ACTIVE:  # as tarefas em andamento nao sobrevivem a um reinicio
            job.update(status="error", error="o servidor foi reiniciado no meio da geração")
        JOBS[job["id"]] = job


def touch():
    """Marca atividade do usuario; o desligamento por ociosidade conta a partir daqui."""
    STATE["activity"] = time.time()


def discard(job):
    """Apaga o que o job guardou para trabalhar e solta as tarefas dele: as que esperam somem,
    as que estao rodando sao interrompidas pelo `reconcile`."""
    for task in job.get("tasks", ()):
        if task["status"] == "pending":
            task["status"] = "cancelled"
        elif task["status"] == "running":
            task["status"] = "stopping"
    for worker in WORKERS:
        for key in [key for key in worker["uploaded"] if key[0] == job["id"]]:
            del worker["uploaded"][key]
    if job.get("workdir"):
        shutil.rmtree(job["workdir"], ignore_errors=True)
    job.pop("inputs", None)
    forget(job["id"])


def gone(job):
    """O job foi excluido ou ja terminou (com erro) enquanto algo dele ainda estava em curso?"""
    return JOBS.get(job["id"]) is not job or job["status"] not in SUBMITTED


def fail(job, message):
    job.update(status="error", error=message, finished=time.time())
    discard(job)
    touch()
    save_jobs()


def idle_info():
    return {
        "seconds": int(time.time() - STATE["activity"]),
        "limit": int(IDLE_MINUTES * 60),
        "busy": any(job["status"] in ACTIVE for job in JOBS.values()),
    }


def extras_note():
    try:
        return EXTRAS_STATUS.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


# ---------------------------------------------------------------- ComfyUI

async def comfy_json(app, method, path, worker=None, **kwargs):
    url = (worker or WORKERS[0])["url"]
    async with app["http"].request(method, url + path, **kwargs) as resp:
        body = await resp.json(content_type=None)
        return resp.status, body


def combo_options(spec):
    """Opcoes de um input COMBO; o formato mudou entre versoes do ComfyUI."""
    if spec and isinstance(spec[0], list):
        return spec[0]
    if len(spec) > 1 and isinstance(spec[1], dict):
        return spec[1].get("options", [])
    return []


async def list_options(app, node, field):
    _, info = await comfy_json(app, "GET", f"/object_info/{node}")
    try:
        return combo_options(info[node]["input"]["required"][field])
    except (KeyError, TypeError):  # no ausente (ex.: ComfyUI-GGUF nao instalado)
        return []


async def node_exists(app, node):
    _, info = await comfy_json(app, "GET", f"/object_info/{node}")
    return isinstance(info, dict) and node in info


async def extra_nodes(app):
    """Quais nos de pacotes extras existem. Isso so muda quando o ComfyUI reinicia."""
    if time.time() - STATE["nodes_at"] > NODES_TTL:
        present = await asyncio.gather(*(node_exists(app, node) for node in EXTRA_NODES))
        STATE["nodes"] = [node for node, found in zip(EXTRA_NODES, present) if found]
        STATE["nodes_at"] = time.time()
    return STATE["nodes"]


async def gpu_device(app):
    """A placa do primeiro ComfyUI, como ele a descreve: {"name", "vram_total", ...}. Guarda
    em STATE["vram"] a memoria dela em GB (None se o ComfyUI nao informa)."""
    _, stats = await comfy_json(app, "GET", "/system_stats")
    device = (stats.get("devices") or [{}])[0]
    STATE["vram"] = round(device.get("vram_total", 0) / 2**30, 1) or None
    return device


async def catalog(app):
    """Conjuntos de modelos instalados, com cache curto: arquivos novos aparecem sozinhos."""
    if time.time() - STATE["catalog_at"] > CATALOG_TTL:
        lists = await asyncio.gather(*(list_options(app, *spec) for spec in MODEL_LISTS.values()))
        await gpu_device(app)
        installed = dict(zip(MODEL_LISTS, lists), nodes=await extra_nodes(app), vram=STATE["vram"])
        STATE["catalog"] = workflows.available(installed)
        STATE["catalog_at"] = time.time()
    return STATE["catalog"]


async def upload_file(app, worker, name, data, content_type):
    """Envia uma imagem ou um video para a pasta de entrada do ComfyUI. A rota se chama
    /upload/image, mas grava qualquer arquivo; e dela que LoadImage e LoadVideo leem."""
    form = aiohttp.FormData()
    form.add_field("image", data, filename=name, content_type=content_type)
    form.add_field("overwrite", "true")
    async with app["http"].post(worker["url"] + "/upload/image", data=form) as resp:
        if resp.status != 200:
            raise RuntimeError(f"o ComfyUI recusou o arquivo ({resp.status})")
        body = await resp.json(content_type=None)
    return f"{body['subfolder']}/{body['name']}" if body.get("subfolder") else body["name"]


# ---------------------------------------------------------------- video de entrada

async def run_tool(*command):
    """Roda ffmpeg/ffprobe e devolve a saida. Erros viram RuntimeError legivel."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *command, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
    except FileNotFoundError:
        raise RuntimeError(f"{command[0]} não está instalado nesta máquina") from None
    try:
        out, err = await asyncio.wait_for(proc.communicate(), FFMPEG_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("o vídeo demorou demais para ser processado") from None
    if proc.returncode != 0:
        detail = err.decode(errors="replace").strip().splitlines()[-1:] or ["erro desconhecido"]
        raise ValueError(f"não consegui ler o vídeo ({detail[0][:160]})")
    return out.decode(errors="replace")


async def probe_video(path):
    """Largura e altura como o video e exibido (celulares gravam deitado com uma etiqueta
    de rotacao) e a duracao em segundos, ou 0 se o arquivo nao informar."""
    out = await run_tool(
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
        "stream=width,height,duration:stream_tags=rotate:stream_side_data=rotation:format=duration",
        "-of", "json", str(path))
    info = json.loads(out)
    streams = info.get("streams") or []
    if not streams:
        raise ValueError("o arquivo enviado não tem vídeo")
    stream = streams[0]
    rotation = stream.get("tags", {}).get("rotate", 0)
    for side in stream.get("side_data_list", []):
        rotation = side.get("rotation", rotation)
    width, height = int(stream["width"]), int(stream["height"])
    if abs(int(float(rotation))) % 180 == 90:
        width, height = height, width

    def seconds(value):
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return 0.0
    duration = seconds(info.get("format", {}).get("duration")) or seconds(stream.get("duration"))
    return width, height, duration


async def count_frames(path):
    out = await run_tool("ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                         "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(path))
    try:
        return int(out.strip().split(",")[0])
    except ValueError:
        return 0


async def prepare_video(source, target, spec):
    """Deixa o video do usuario do jeito que o Animate consome: trecho pedido, fps e tamanho de
    geracao (cortando as sobras, sem distorcer). Completa a especificacao do job com os
    tamanhos, a quantidade real de quadros e o plano de pedacos."""
    fps = spec["fps"]
    shown_width, shown_height, duration = await probe_video(source)
    size, out_size, aspect = workflows.swap_size(spec, shown_width, shown_height)
    seconds = min(spec["seconds"], duration - spec["start"]) if duration else spec["seconds"]
    if seconds * fps < workflows.SWAP_MIN_FRAMES <= seconds * workflows.SWAP_FPS:
        # Trecho curto demais para gerar so parte dos quadros: gera todos.
        fps = spec["fps"] = spec["out_fps"] = workflows.SWAP_FPS
    wanted = max(1, round(spec["seconds"] * fps))
    frames = min(wanted, int((duration - spec["start"]) * fps)) if duration else wanted

    # A duracao informada pelo arquivo pode estar errada; se sair menos quadros que o
    # previsto, refaz uma vez com a contagem real.
    for _ in range(2):
        if frames < workflows.SWAP_MIN_FRAMES:
            break
        await run_tool(
            "ffmpeg", "-nostdin", "-y", "-v", "error", "-ss", f"{spec['start']:.3f}",
            "-i", str(source), "-map", "0:v:0", "-map", "0:a:0?",
            "-vf", f"fps={fps},scale={size[0]}:{size[1]}:force_original_aspect_ratio=increase,"
                   f"crop={size[0]}:{size[1]},setsar=1",
            "-frames:v", str(frames), "-t", f"{frames / fps:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(target))
        real = min(frames, await count_frames(target))
        if real == frames:
            break
        frames = real
    # Os pedacos avancam do comeco do video. Partir de um pedaco no meio (para duas GPUs
    # trabalharem juntas) so por ajuste de teste: workflows.SWAP_MIDDLE explica.
    middle = spec["middle"] if spec["middle"] is not None else \
        workflows.SWAP_MIDDLE if len(WORKERS) > 1 else 0
    # Duas cadeias que avancam, uma em cada GPU (stitch.chains explica): o padrao so com mais de
    # uma GPU; um ajuste de teste pede as duas cadeias ate numa GPU so, para ver a emenda.
    chains = spec.get("chains") or (workflows.SWAP_CHAINS if len(WORKERS) > 1 else 1)
    if chains > 1 and not middle:
        pieces = stitch.chains(frames, spec["chunk"], spec["overlap"], workflows.SWAP_MIN_FRAMES,
                               anchor=spec.get("anchor", True))
    else:
        pieces = stitch.plan(frames, spec["chunk"], spec["overlap"], middle, workflows.SWAP_MIN_FRAMES)
    if not pieces:
        raise ValueError("o trecho do vídeo precisa ter pelo menos 1 segundo")
    total = pieces[-1]["start"] + pieces[-1]["length"]
    spec.update(width=out_size[0], height=out_size[1], generate=size, aspect=aspect, length=total,
                duration=round(total / fps, 1), chunks=len(pieces), chunks_done=0, plan=pieces)


# ---------------------------------------------------------------- resultado

def output_items(entry, prefix, suffix):
    """Os arquivos que uma tarefa gravou, entre os que o historico do ComfyUI lista.

    O historico nao traz so resultados: o LoadVideo, por exemplo, devolve como previa o
    proprio video enviado. Vale apenas o que esta na pasta de saida e foi gravado com o
    prefixo da tarefa (workflows grava em "wanstudio/<prefixo>_...").
    """
    found = []
    for node_output in entry.get("outputs", {}).values():
        for items in node_output.values():
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict) or item.get("type", "output") != "output":
                    continue
                name = str(item.get("filename", ""))
                if name.startswith(prefix + "_") and name.endswith(suffix):
                    found.append(item)
    return sorted(found, key=lambda item: item["filename"])


def out_of_memory(data):
    """A falta de memoria chega com textos diferentes conforme o alocador do PyTorch: "CUDA
    out of memory..." ou so "Allocation on device"; o tipo da excecao e o que nao muda."""
    text = str(data.get("exception_message", "")).lower()
    return ("outofmemory" in str(data.get("exception_type", "")).lower()
            or "out of memory" in text or "allocation on device" in text)


def error_message(entry, job):
    for event, data in entry.get("status", {}).get("messages", []):
        if event == "execution_interrupted":
            return "cancelado"
        if event == "execution_error":
            if out_of_memory(data):
                options = "a qualidade rápida" if job["mode"] == "swap" else \
                    "480p, 3 segundos ou o modelo 5B"
                return f"faltou memória na GPU: tente {options}"
            text = str(data.get("exception_message", "")).strip() or "erro no ComfyUI"
            return text[:400]
    return "erro no ComfyUI"


def rejection_text(body):
    """Mensagem legivel quando o ComfyUI rejeita o grafo na validacao."""
    if not isinstance(body, dict):
        return "o ComfyUI rejeitou o workflow"
    detail = body.get("error", {}).get("message", "")
    nodes = []
    for node in body.get("node_errors", {}).values():
        for error in node.get("errors", []):
            nodes.append(f"{node.get('class_type', '?')}: {error.get('details') or error.get('message')}")
    return f"o ComfyUI rejeitou o workflow: {detail} {'; '.join(nodes)}".strip()[:500]


def forget(prefix):
    """Apaga do ComfyUI os arquivos de uma tarefa: o que ela gravou e o que recebeu."""
    if COMFY_DIR is None:
        return
    for folder, pattern in (("output/wanstudio", f"{prefix}_*"), ("input", f"wanstudio_{prefix}*")):
        for path in (COMFY_DIR / folder).glob(pattern):
            try:
                path.unlink()
            except OSError:
                pass


def memory_gb():
    """Memoria RAM total e disponivel, em GB (so no Linux; None fora dele)."""
    try:
        fields = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
        return {key: round(int(fields[name].split()[0]) / 2**20, 1)
                for key, name in (("total", "MemTotal"), ("free", "MemAvailable"))}
    except (OSError, KeyError, ValueError):
        return None


async def download(app, worker, item, target):
    query = {"filename": item["filename"], "subfolder": item.get("subfolder", ""),
             "type": item.get("type", "output")}
    async with app["http"].get(worker["url"] + "/view", params=query) as resp:
        resp.raise_for_status()
        with target.open("wb") as fh:
            async for chunk in resp.content.iter_chunked(1 << 16):
                fh.write(chunk)


def complete(job, target):
    job.update(status="done", progress=1.0, stage="", url=f"/media/{target.name}",
               finished=time.time())
    if PRICE_HOUR and job.get("started"):  # o tempo de maquina deste video, pelo preco dela
        job["cost"] = round((job["finished"] - job["started"]) / 3600 * PRICE_HOUR, 3)
    discard(job)
    touch()
    save_jobs()


def frames_pattern(job, index, raw=False):
    """Onde ficam os quadros do pedaco `index`, na ordem do video. Num job com restauracao cada
    pedaco tem duas sequencias: a bruta (`raw`), como saiu da geracao, e a final."""
    name = f"bruto_{index:03d}" if raw and job.get("restore") else f"{index:03d}"
    return str(job["workdir"] / name / "%05d.png")


def raw_factor(job):
    """Quantos quadros a sequencia bruta tem por quadro gerado: com restauracao, os
    intermediarios so entram depois dela."""
    return 1 if job.get("restore") else job["factor"]


async def settle(seconds=30.0):
    """Espera a memoria RAM livre parar de mudar (ou `seconds`): o ComfyUI solta os modelos
    aos poucos, e ao solta-los chega a ocupar mais memoria por alguns segundos."""
    last, calm, deadline = None, 0, time.time() + seconds
    await asyncio.sleep(2.0)
    while time.time() < deadline and calm < 2:
        now = (memory_gb() or {}).get("free")
        if now is None:  # fora do Linux nao ha o que medir
            return
        calm = calm + 1 if last is not None and abs(now - last) < 0.3 else 0
        last = now
        await asyncio.sleep(1.0)


async def free_workers(app):
    """Pede a cada ComfyUI que solte os modelos carregados (vale ao fim do que estiver
    rodando), um de cada vez. Os dois ao mesmo tempo esgotaram a RAM da maquina do Kaggle
    (30 GB): o sistema matou um deles com SIGKILL, duas vezes em tres tentativas. Um por vez e
    o que ja acontece, sem problema, quando o melhorador de prompt recebe a GPU de volta."""
    async with app["freeing"]:
        for worker in WORKERS:
            try:
                async with app["http"].post(worker["url"] + "/free",
                                            json={"unload_models": True, "free_memory": True}):
                    pass
            except (aiohttp.ClientError, asyncio.TimeoutError):
                continue
            await settle()


async def begin_restore(app, job):
    """Todos os pedacos foram gerados. Antes de restaurar, cada ComfyUI solta os modelos da
    geracao: os dois conjuntos juntos nao cabem na memoria da maquina com duas GPUs
    trabalhando. Pelo mesmo motivo os da restauracao saem quando ela termina."""
    await free_workers(app)
    if not gone(job):
        job["restoring"] = True
        refresh(job)


async def finish_swap(app, job):
    """Todos os pedacos chegaram: emenda, devolve o audio e entrega o video."""
    job["stage"] = "juntando os pedaços"
    target = OUTPUTS / (job["id"] + ".mp4")
    prepared = job["workdir"] / "preparado.mp4"
    chunks = [{**piece, "pattern": frames_pattern(job, index)} for index, piece in enumerate(job["plan"])]
    try:
        await stitch.stitch(chunks, target, job["fps"], job["overlap"], (job["width"], job["height"]),
                            audio=prepared, out_fps=job["out_fps"], original=prepared,
                            factor=job["factor"])
    except stitch.StitchError as exc:
        target.unlink(missing_ok=True)
        if not gone(job):
            fail(job, f"não consegui montar o vídeo: {exc}")
        return
    if gone(job):  # excluido enquanto montava
        return target.unlink(missing_ok=True)
    complete(job, target)


async def finish_task(app, worker, job, task, entry):
    """A tarefa saiu da fila do ComfyUI: busca o que ela gravou."""
    if entry.get("status", {}).get("status_str") == "error":
        return fail(job, error_message(entry, job))
    swap = job["mode"] == "swap"
    items = output_items(entry, task["prefix"], ".png" if swap else EXTENSIONS[job["kind"]])
    try:
        if not swap:
            if not items:
                return fail(job, "o ComfyUI terminou sem gerar o arquivo")
            target = OUTPUTS / (job["id"] + EXTENSIONS[job["kind"]])
            await download(app, worker, items[0], target)
            return complete(job, target)
        # Com interpolacao, o pedaco traz tambem os quadros intermediarios (stitch.fine); o
        # pedaco bruto de um job com restauracao ainda nao os tem.
        raw = bool(job.get("restore")) and task.get("kind") != "restore"
        count = (task["length"] - 1) * (raw_factor(job) if raw else job["factor"]) + 1
        if len(items) != count:
            return fail(job, "o ComfyUI terminou sem gerar todos os quadros do pedaço")
        folder = Path(frames_pattern(job, task["index"], raw=raw)).parent
        folder.mkdir(parents=True, exist_ok=True)
        gate = asyncio.Semaphore(DOWNLOADS_AT_ONCE)

        async def fetch(number, item):
            if task["reverse"]:  # gerado de tras para a frente: guarda na ordem do video
                number = count - 1 - number
            async with gate:
                await download(app, worker, item, folder / f"{number:05d}.png")
        await asyncio.gather(*(fetch(number, item) for number, item in enumerate(items)))
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
        if gone(job):  # excluido no meio da descida: a pasta sumiu debaixo dos arquivos
            return discard(job)
        return fail(job, "não consegui buscar o resultado no ComfyUI")
    if gone(job):
        return discard(job)  # apaga tambem os quadros que acabaram de chegar
    task.update(status="done", progress=1.0, stage="")
    refresh(job)
    if all(other["status"] == "done" for other in job["tasks"]):
        if job.get("restore"):
            spawn(app, free_workers(app))
        spawn(app, finish_swap(app, job))
    elif (job.get("restore") and not job.get("restoring") and not job.get("freeing")
          and all(other["status"] == "done" for other in job["tasks"] if other.get("kind") != "restore")):
        job["freeing"] = True
        spawn(app, begin_restore(app, job))


# ---------------------------------------------------------------- tarefas e workers

def spawn(app, coroutine):
    task = asyncio.create_task(coroutine)
    app["tasks"].add(task)
    task.add_done_callback(app["tasks"].discard)


def refresh(job):
    """Progresso e etapa do job a partir das tarefas dele."""
    tasks = job.get("tasks") or []
    if job["status"] not in SUBMITTED or not tasks:
        return
    if len(tasks) == 1 and job["mode"] != "swap":
        job.update(progress=round(tasks[0]["progress"], 3), stage=tasks[0]["stage"])
        return
    made = [task for task in tasks if task.get("kind") != "restore"]
    fixed = [task for task in tasks if task.get("kind") == "restore"]
    progress = sum(task["progress"] for task in made) / len(made)
    if fixed:  # a restauracao e a parte curta: um quinto da barra
        progress = 0.8 * progress + 0.2 * sum(task["progress"] for task in fixed) / len(fixed)
    running = [task for task in tasks if task["status"] == "running"]
    job.update(
        progress=round(0.97 * progress, 3),  # o resto e a emenda
        chunks_done=sum(1 for task in made if task["status"] == "done"),
        # Ate o primeiro passo, vale a etapa do pedaco que comecou antes; depois, o percentual.
        stage="restaurando os detalhes" if job.get("restoring")
        else running[0]["stage"] if running and not progress else "")


def release(worker):
    worker.update(task=None, idle_since=time.time())


def waiting_tasks():
    """Tarefas a espera de um worker, na ordem em que devem rodar: job mais antigo primeiro."""
    jobs = sorted((job for job in JOBS.values() if job["status"] in SUBMITTED),
                  key=lambda job: job["created"])
    # Um pedaco da troca de pessoa so pode comecar quando aquele de que ele continua terminou
    # (ou, no comeco de uma segunda cadeia, aquele de que ele copia a aparencia: a ancora).
    # E a restauracao de um job so comeca com todos os pedacos gerados e a memoria liberada.
    def ready(job, task):
        return all(source is None or job["tasks"][source]["status"] == "done"
                   for source in (task["after"], task.get("anchor")))
    return [task for job in jobs for task in job.get("tasks", ()) if task["status"] == "pending"
            and (job.get("restoring") if task.get("kind") == "restore" else ready(job, task))]


async def lend_gpu(worker):
    """Antes de usar o worker que divide a GPU com o melhorador de prompt, o melhorador sai.
    Devolve False se ele ainda nao pode sair (esta no meio da carga)."""
    if worker["index"] != ENHANCER_WORKER:
        return True
    return await enhancer.sleep()


async def return_gpu(app):
    """Passado um tempo sem uso, o worker devolve a GPU ao melhorador de prompt."""
    if ENHANCER_WORKER < 0 or enhancer.state != "sleeping":
        return
    worker = WORKERS[ENHANCER_WORKER]
    if worker["task"] or waiting_tasks() or time.time() - worker["idle_since"] < ENHANCER_RETURN_AFTER:
        return
    try:
        async with app["http"].post(worker["url"] + "/free",
                                    json={"unload_models": True, "free_memory": True}):
            pass
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return
    enhancer.wake()


async def start_task(app, worker, task):
    job = JOBS[task["job"]]
    try:
        names = {}
        restore = task.get("kind") == "restore"
        for field, (filename, data, content_type) in ({} if restore else job["inputs"]).items():
            key = (job["id"], field)
            if key not in worker["uploaded"]:
                name = f"wanstudio_{job['id']}_{field}{Path(filename).suffix.lower() or '.png'}"
                worker["uploaded"][key] = await upload_file(app, worker, name, data, content_type)
            names[field] = worker["uploaded"][key]
        spec = job
        if restore:
            # Os quadros brutos do pedaco voltam ao ComfyUI como um video sem perda.
            source = job["workdir"] / f"bruto_{task['index']:03d}.mp4"
            await stitch.clip(frames_pattern(job, task["index"], raw=True), 0, task["length"], source,
                              job["fps"], lossless=True)
            video = await upload_file(app, worker, f"wanstudio_{task['prefix']}.mp4", source.read_bytes(), "video/mp4")
            graph, samplers, stages = workflows.build_restore(job, job["files"], task["prefix"], video)
        elif job["mode"] == "swap":
            names["video"] = await upload_file(
                app, worker, f"wanstudio_{task['prefix']}.mp4", task["video"].read_bytes(), "video/mp4")
            if task["after"] is not None or task.get("anchor") is not None:
                # Os quadros do vizinho, ja gerados, que este pedaco repete para continuar dele;
                # no comeco de uma segunda cadeia, os da ancora, de que ele herda a aparencia.
                source, first = (stitch.copied if task["after"] is not None else stitch.anchored)(
                    job["plan"], task["index"], job["overlap"])
                motion = job["workdir"] / f"continua_{task['index']:03d}.mp4"
                await stitch.clip(frames_pattern(job, source, raw=True), first, job["overlap"], motion,
                                  job["fps"], reverse=task["reverse"], factor=raw_factor(job))
                names["motion"] = await upload_file(
                    app, worker, f"wanstudio_{task['prefix']}_continua.mp4", motion.read_bytes(), "video/mp4")
            width, height = job["generate"]
            spec = {**job, "width": width, "height": height, "length": task["length"],
                    # o comeco de uma segunda cadeia: os quadros recebidos sao de outro instante
                    "anchored": task["after"] is None and task.get("anchor") is not None}
            if job.get("restore"):  # sai bruto: a restauracao e os intermediarios vem depois
                spec.update(hd=False, factor=1)
        if not restore:
            graph, samplers, stages = workflows.build(
                spec, job["files"], job["positive"], job["negative"], task["prefix"], **names)
        status, body = await comfy_json(app, "POST", "/prompt", worker,
                                        json={"prompt": graph, "client_id": worker["client_id"]})
        if status != 200:
            raise RuntimeError(rejection_text(body))
    except (aiohttp.ClientError, asyncio.TimeoutError):
        # Este worker nao respondeu: a tarefa fica para outro, se houver algum de pe.
        worker["retry_at"] = time.time() + WORKER_RETRY
        if all(other["retry_at"] > time.time() for other in WORKERS):
            fail(job, "o ComfyUI ficou fora do ar")
        return
    except Exception as exc:
        return fail(job, str(exc) or type(exc).__name__)
    # O job pode ter sido excluido (ou ter falhado em outro pedaco) enquanto os arquivos subiam:
    # ai a tarefa ja nasce para ser interrompida pelo `reconcile`.
    wanted = task["status"] == "pending" and JOBS.get(job["id"]) is job
    task.update(status="running" if wanted else "stopping", worker=worker["index"],
                prompt_id=body["prompt_id"], samplers=samplers, stages=stages)
    worker["task"] = task
    if not wanted:
        return discard(job)  # leva junto o que esta tarefa gravou enquanto se preparava
    if job["status"] != "running":
        job.update(status="running", started=time.time())
    refresh(job)


async def warm_inputs():
    """A imagem e o video do pedaco de aquecimento (cinza, sem ninguem), feitos uma vez."""
    folder = DATA / "aquecimento"
    image, video = folder / "pessoa.png", folder / "video.mp4"
    if not (image.exists() and video.exists()):
        folder.mkdir(parents=True, exist_ok=True)
        source = ["-f", "lavfi", "-i", f"color=c=0x808080:s=256x256:r={workflows.SWAP_FPS}"]
        await run_tool("ffmpeg", "-nostdin", "-y", "-v", "error", *source, "-frames:v", "1", str(image))
        await run_tool("ffmpeg", "-nostdin", "-y", "-v", "error", *source,
                       "-frames:v", str(workflows.SWAP_MIN_FRAMES), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video))
    return image.read_bytes(), video.read_bytes()


async def warm_up(app):
    """Com a troca de pessoa instalada e nada na fila, cada GPU roda uma vez um pedaco minimo
    dela. O que demora na primeira troca de uma sessao nao e gerar: e ler 18 GB de modelos do
    disco e iniciar a deteccao de pose na GPU, uns 3 minutos e meio por GPU (medido: a primeira
    troca de 10 s levou 10,6 min; as seguintes, 4,6). Aquecendo antes, isso acontece enquanto
    o usuario ainda escolhe os arquivos. Um pedido que chegue no meio interrompe o aquecimento
    (veja `dispatch`); o que ja foi lido do disco continua valendo."""
    if not WARM_UP or waiting_tasks() or STATE["restart"]:
        return
    now = time.time()
    cold = [worker for worker in WORKERS
            if not worker["warm"] and worker["task"] is None and worker["retry_at"] <= now]
    if not cold:
        return
    try:
        files = (await catalog(app))["animate"]
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        return
    if not (files["ok"] and files["turbo"]):
        return
    for worker in cold:
        if not await lend_gpu(worker):
            continue  # o melhorador de prompt esta carregando nessa GPU: fica para depois
        worker["warm"] = True  # uma tentativa so: se falhar, a primeira troca carrega tudo
        try:
            image, video = await warm_inputs()
            names = {"start_image": await upload_file(app, worker, "wanstudio_aquecimento.png", image, "image/png"),
                     "video": await upload_file(app, worker, "wanstudio_aquecimento.mp4", video, "video/mp4")}
            graph, _, _ = workflows.build(
                workflows.warm_spec(), files["files"], presets.compose_prompt(workflows.ANIMATE_PROMPT),
                presets.compose_negative(""), "aquecimento", **names)
            status, body = await comfy_json(app, "POST", "/prompt", worker,
                                            json={"prompt": graph, "client_id": worker["client_id"]})
            if status != 200:
                raise RuntimeError(rejection_text(body))
        except Exception as exc:
            print(f"[aquecimento] GPU {worker['index']}: {exc!r}", file=sys.stderr)
            continue
        # Uma tarefa sem job: ocupa o worker ate o ComfyUI terminar, e o `reconcile` o libera.
        worker["task"] = {"job": None, "index": 0, "status": "running", "worker": worker["index"],
                          "prompt_id": body["prompt_id"], "samplers": [], "stages": {}, "warm": True}


async def dispatch(app):
    """Entrega as tarefas em espera aos workers livres."""
    waiting = waiting_tasks()
    now = time.time()
    if waiting:  # trabalho de verdade tem preferencia sobre o aquecimento
        for worker in WORKERS:
            if worker["task"] and worker["task"].get("warm"):
                worker["task"]["status"] = "stopping"
    restarting = STATE["restart"]["worker"] if STATE["restart"] else None
    # O worker que divide a GPU com o melhorador fica por ultimo: so entra quando falta braco.
    free = sorted((worker for worker in WORKERS if worker["task"] is None
                   and worker["retry_at"] <= now and worker["index"] != restarting),
                  key=lambda worker: worker["index"] == ENHANCER_WORKER)
    for worker in free:
        task = next((task for task in waiting if task["status"] == "pending"
                     and task["pin"] in (None, worker["index"])), None)
        if task is None or not await lend_gpu(worker):
            continue
        await start_task(app, worker, task)


async def cancel_prompt(app, worker, prompt_id):
    async with app["http"].post(worker["url"] + "/queue", json={"delete": [prompt_id]}):
        pass
    async with app["http"].post(worker["url"] + "/interrupt", json={"prompt_id": prompt_id}):
        pass


async def reconcile(app, worker):
    """Confere a tarefa do worker contra a fila do ComfyUI."""
    task = worker["task"]
    if task is None:
        return
    # A fila e lida antes do historico: o que saiu da fila ja esta no historico.
    _, queue = await comfy_json(app, "GET", "/queue", worker, timeout=QUEUE_TIMEOUT)
    listed = {item[1] for key in ("queue_running", "queue_pending") for item in queue.get(key, [])}
    prompt_id = task["prompt_id"]
    if task["status"] == "stopping":  # job cancelado, excluido ou com erro em outro pedaco
        if prompt_id not in listed:
            return release(worker)
        if not task.get("interrupted"):
            task["interrupted"] = True
            await cancel_prompt(app, worker, prompt_id)
        return
    if prompt_id in listed:
        return
    _, history = await comfy_json(app, "GET", f"/history/{prompt_id}", worker)
    release(worker)
    if task.get("warm"):
        # A GPU do melhorador de prompt fica aquecida pelo mesmo tempo que depois de uma troca.
        # Devolvida logo (testado: 30 s), a primeira troca pegou o melhorador no meio da carga
        # e esperou por ele: 6,7 min em vez de 5.
        return
    job = JOBS.get(task["job"])
    if job is None or job["status"] not in SUBMITTED:
        return
    if prompt_id not in history:
        return fail(job, "o job sumiu da fila (o ComfyUI foi reiniciado?)")
    await finish_task(app, worker, job, task, history[prompt_id])


async def engine(app):
    """Um ciclo: recolhe o que terminou e entrega o que espera. So um ciclo roda por vez."""
    async with app["engine"]:
        for worker in WORKERS:
            try:
                await reconcile(app, worker)
            except (aiohttp.ClientError, asyncio.TimeoutError):
                pass  # este ComfyUI nao respondeu agora; os outros seguem
        await dispatch(app)
        await warm_up(app)
        await return_gpu(app)


async def poller(app):
    while True:
        await asyncio.sleep(POLL_SECONDS)
        try:
            await engine(app)
        except Exception as exc:  # o loop nao pode morrer
            print(f"[poller] {exc!r}", file=sys.stderr)


def running_task(worker, data):
    """A tarefa em andamento a que um evento do ComfyUI se refere, se houver."""
    task = worker["task"]
    if task and task["status"] == "running" and task["prompt_id"] == data.get("prompt_id"):
        return task
    return None


def on_progress(worker, data):
    """Converte o progresso de um sampler em progresso da tarefa."""
    task = running_task(worker, data)
    if task and data.get("node") in task["samplers"] and data.get("max"):
        done = task["samplers"].index(data["node"]) + data["value"] / data["max"]
        # os 5% finais ficam para o VAE e a gravacao do arquivo
        task["progress"] = 0.95 * done / len(task["samplers"])
        if task["job"] in JOBS:
            refresh(JOBS[task["job"]])


def on_executing(worker, data):
    """Anota a etapa (analisando o video, recortando a pessoa...) do no que comecou a rodar."""
    task = running_task(worker, data)
    node = data.get("node")
    if not task or node is None:
        return
    if node in task["stages"]:
        task["stage"] = task["stages"][node]
    elif node in task["samplers"]:
        task["stage"] = ""  # durante a amostragem vale o percentual
    if task["job"] in JOBS:
        refresh(JOBS[task["job"]])


async def progress_listener(app, worker):
    # O ComfyUI so manda estes eventos para o clientId que enviou a tarefa.
    ws_url = worker["url"].replace("http", "ws", 1) + f"/ws?clientId={worker['client_id']}"
    handlers = {"progress": on_progress, "executing": on_executing}
    while True:
        try:
            async with app["http"].ws_connect(ws_url, heartbeat=30) as ws:
                async for msg in ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue
                    event = json.loads(msg.data)
                    handler = handlers.get(event.get("type"))
                    if handler:
                        handler(worker, event.get("data") or {})
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError):
            pass
        await asyncio.sleep(3)


def new_task(job, index=0, piece=None, video=None):
    """Uma tarefa: o job inteiro ou, na troca de pessoa, um pedaco do plano."""
    piece = piece or {"start": 0, "length": 0, "after": None, "reverse": False}
    prefix = job["id"] if video is None else f"{job['id']}_{index:03d}"
    return {"job": job["id"], "index": index, "prefix": prefix, "status": "pending", "worker": None,
            "prompt_id": None, "samplers": [], "stages": {}, "progress": 0.0, "stage": "",
            "video": video, "pin": job.get("pin"), **piece}


async def launch(app, jobs, params, files):
    """Segunda metade do /api/generate, fora da requisicao: melhora o prompt (se pedido),
    corta o video em pedacos (troca de pessoa) e cria as tarefas. Erros viram erro no card,
    nao na requisicao."""
    first = jobs[0]
    swap = first["mode"] == "swap"
    try:
        # Na troca de pessoa o video ja diz o que acontece: o prompt e opcional.
        text = first["prompt"] or workflows.ANIMATE_PROMPT
        if params.get("auto_enhance") and enhancer.state == "ready" and not swap:
            try:
                text = await asyncio.wait_for(
                    enhancer.enhance(text, first["mode"], first["camera"] != "none"),
                    ENHANCE_TIMEOUT)
            except Exception as exc:  # sem melhoria, segue com o texto original
                print(f"[enhancer] {exc!r}", file=sys.stderr)
        positive = presets.compose_prompt(text, first["camera"], first["effect"], first["style"])
        negative = presets.compose_negative(first["negative"])
        for job in jobs:
            if job["id"] not in JOBS:  # cancelado enquanto preparava
                continue
            tasks = [new_task(job)]
            if swap:
                tasks = []
                for index, piece in enumerate(job["plan"]):
                    video = job["workdir"] / f"pedaco_{index:03d}.mp4"
                    await stitch.cut(job["workdir"] / "preparado.mp4", video, piece["start"],
                                     piece["length"], reverse=piece["reverse"])
                    tasks.append(new_task(job, index, piece, video))
                if job.get("restore"):  # uma tarefa de restauracao por pedaco, para depois
                    for index, piece in enumerate(job["plan"]):
                        tasks.append({**new_task(job, index, piece), "kind": "restore", "after": None,
                                      "reverse": False, "prefix": f"{job['id']}_r{index:03d}"})
            if job["id"] in JOBS:
                job.update(status="queued", tasks=tasks, files=files, positive=positive,
                           negative=negative, final_prompt=positive)
            else:
                discard(job)  # excluido enquanto o video era cortado
    except Exception as exc:
        message = str(exc) or type(exc).__name__
    else:
        message = None
    for job in jobs:
        if message and job["status"] == "preparing":
            fail(job, message)
    save_jobs()
    await engine(app)


# ---------------------------------------------------------------- rotas

def json_error(message, status):
    return web.json_response({"error": message}, status=status)


async def index(_request):
    return web.FileResponse(STATIC / "index.html")


async def get_presets(_request):
    return web.json_response(presets.public())


async def get_status(request):
    app = request.app
    payload = {
        "comfy": False, "gpu": None, "models": {}, "workers": len(WORKERS),
        "enhancer": {"state": enhancer.state, "detail": enhancer.detail},
        "extras": extras_note(), "idle": idle_info(), "shutdown": STATE["shutdown"],
        "restart": STATE["restart"], "managed": MANAGED, "ram": memory_gb(), "now": time.time(),
        "price_hour": PRICE_HOUR or None, "focus": "swap" if ONLY_SWAP else None,
    }
    try:
        sets = await catalog(app)
        device = await gpu_device(app)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        return web.json_response(payload)
    payload["vram"] = STATE["vram"]
    if device.get("name"):
        name = device["name"].split(" : ")[0].removeprefix("cuda:0 ")
        count = f"{len(WORKERS)}× " if len(WORKERS) > 1 else ""
        payload["gpu"] = f"{count}{name} · {round(device.get('vram_total', 0) / 2**30)} GB"
    payload["comfy"] = True
    # Quantas GPUs estao, agora, carregando a troca de pessoa de antemao (veja `warm_up`).
    payload["warming"] = sum(1 for worker in WORKERS if worker["task"] and worker["task"].get("warm"))
    # Videos da producao em massa na fila: o site nao os lista, mas eles ocupam as GPUs.
    payload["held"] = sum(1 for job in JOBS.values() if job.get("hold") and job["status"] in ACTIVE)
    payload["models"] = {name: {"ok": s["ok"], "turbo": s["turbo"],
                                "hd": workflows.hd_method({"hd": True}, s["files"]),
                                "rife": bool(s["files"].get("interpolator"))}
                         for name, s in sets.items()}
    return web.json_response(payload)


async def list_jobs(request):
    """Os jobs do site, ou, com ?hold=1, os de quem pediu para guarda-los (a producao em massa,
    free/producao.py): o site baixa e apaga tudo o que ve pronto, e levaria os dela junto."""
    held = request.query.get("hold") == "1"
    jobs = sorted((j for j in JOBS.values() if bool(j.get("hold")) == held), key=lambda j: j["created"], reverse=True)
    return web.json_response({"now": time.time(), "jobs": [public(j) for j in jobs]})


async def read_limited(part, limit):
    data = bytearray()
    while chunk := await part.read_chunk(1 << 16):
        data += chunk
        if len(data) > limit:
            raise web.HTTPRequestEntityTooLarge(limit, len(data))
    return bytes(data)


async def save_limited(part, target, limit):
    """Grava a parte em disco sem carrega-la inteira na memoria."""
    size = 0
    with target.open("wb") as handle:
        while chunk := await part.read_chunk(1 << 16):
            size += len(chunk)
            if size > limit:
                raise web.HTTPRequestEntityTooLarge(limit, size)
            handle.write(chunk)
    return size


async def read_form(request, workdir):
    """Le o formulario: parametros, imagens (na memoria) e video (em `workdir`)."""
    params, images, video = {}, {}, None
    async for part in await request.multipart():
        if part.name == "params":
            params = json.loads(await part.text())
        elif part.name in ("start_image", "end_image"):
            data = await read_limited(part, MAX_IMAGE_BYTES)
            if data:
                images[part.name] = (part.filename or "image.png", data,
                                     part.headers.get("Content-Type", "image/png"))
        elif part.name == "video":
            workdir.mkdir(parents=True, exist_ok=True)
            target = workdir / f"original{Path(part.filename or 'video.mp4').suffix[:8]}"
            if await save_limited(part, target, MAX_VIDEO_BYTES):
                video = target
    if not isinstance(params, dict):
        raise ValueError("parâmetros inválidos")
    return params, images, video


async def generate(request):
    if not request.content_type.startswith("multipart/"):
        return json_error("requisição inválida", 400)
    batch = uuid.uuid4().hex[:10]
    workdir = UPLOADS / f"{batch}0"  # a troca de pessoa tem um job so, de id <batch>0
    response = await accept(request, batch, workdir)
    if response.status != 200:  # recusado: nada do que chegou sera usado
        shutil.rmtree(workdir, ignore_errors=True)
    return response


async def accept(request, batch, workdir):
    app = request.app
    try:
        params, images, video = await read_form(request, workdir)
        spec = workflows.resolve(params, "start_image" in images, "end_image" in images,
                                 video is not None)
        swap = spec["mode"] == "swap"
        # Camera, efeito e estilo nao se aplicam a troca: o video dita a cena.
        look = {key: "none" if swap else str(params.get(key, "none"))
                for key in ("camera", "effect", "style")}
        presets.compose_prompt("x", look["camera"], look["effect"], look["style"])
        count = 1 if swap else min(max(int(params.get("count", 1)), 1), MAX_BATCH)
        if spec.get("pin") is not None and spec["pin"] >= len(WORKERS):
            raise ValueError("ajuste de teste inválido")
    except (ValueError, TypeError) as exc:  # inclui JSON invalido
        return json_error(str(exc), 400)
    if not swap:  # um video enviado fora da troca de pessoa nao serve para nada
        shutil.rmtree(workdir, ignore_errors=True)

    needed = workflows.required_set(spec["model"], spec["mode"])
    wanted = {"t2v": (), "img": (), "i2v": ("start_image",), "flf": ("start_image", "end_image"),
              "swap": ("start_image",)}
    try:
        sets = await catalog(app)
        if not sets[needed]["ok"]:
            return json_error("os modelos deste modo ainda não estão instalados na GPU", 400)
        if swap:
            if (STATE["vram"] or 0) < workflows.min_vram(spec):
                raise ValueError(f"esta qualidade precisa de uma GPU com {workflows.min_vram(spec)} GB "
                                 "de memória ou mais")
            spec["hd_method"] = workflows.hd_method(spec, sets[needed]["files"])
            spec["hd"] = spec["hd_method"] is not None
            spec["restore"] = spec["hd_method"] == "seedvr2" and not spec.get("preview")
            if spec["restore"] and not spec.pop("chunk_asked", False) and (STATE["vram"] or 0) < workflows.FP8_MIN_VRAM:
                spec["chunk"] = min(spec["chunk"], workflows.SWAP_HD_CHUNK)
            spec.pop("chunk_asked", None)
            if workflows.animate_unet(spec, sets[needed]["files"]) is None:
                raise ValueError("ajuste de teste inválido")
            if spec.get("whole") and not sets[needed]["files"].get("foreground"):
                raise ValueError("ajuste de teste inválido: o modelo do recorte da pessoa inteira não está instalado")
            await prepare_video(video, workdir / "preparado.mp4", spec)
            spec["factor"] = workflows.smooth_factor(spec, sets[needed]["files"])
            video.unlink(missing_ok=True)
            spec["workdir"] = workdir
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return json_error("ComfyUI fora do ar", 502)
    except ValueError as exc:  # video ilegivel ou curto demais
        return json_error(str(exc), 400)
    except RuntimeError as exc:
        return json_error(str(exc), 502)
    spec["fast"] = spec["fast"] and sets[needed]["turbo"]
    if swap and not spec["fast"]:
        return json_error("a LoRA de poucos passos do Animate ainda não está instalada na GPU", 400)

    now = time.time()
    jobs = []
    for i in range(count):
        job = {
            "resolution": params.get("resolution", "480p"), "aspect": params.get("aspect", "16:9"),
            **spec,
            "id": f"{batch}{i}", "status": "preparing", "progress": 0.0, "stage": "", "hold": bool(params.get("hold")),
            "created": now + i / 1000, "seed": (spec["seed"] + i) % workflows.MAX_SEED,
            "prompt": str(params.get("prompt", "")).strip(),
            "negative": str(params.get("negative", "")),
            "inputs": {field: images[field] for field in wanted[spec["mode"]]},
            **look,
        }
        JOBS[job["id"]] = job
        jobs.append(job)
    touch()
    save_jobs()

    spawn(app, launch(app, jobs, params, sets[needed]["files"]))
    return web.json_response({"jobs": [public(j) for j in jobs]})


async def delete_job(request):
    job = JOBS.pop(request.match_info["id"], None)
    if not job:
        raise web.HTTPNotFound()
    discard(job)  # o reconcile interrompe no ComfyUI o que estiver rodando
    (OUTPUTS / (job["id"] + EXTENSIONS[job["kind"]])).unlink(missing_ok=True)
    touch()
    save_jobs()
    spawn(request.app, engine(request.app))
    return web.json_response({"ok": True})


async def enhance_prompt(request):
    try:
        body = await request.json()
        text = str(body.get("prompt", "")).strip()
    except (ValueError, AttributeError):
        return json_error("requisição inválida", 400)
    if not text:
        return json_error("escreva um prompt", 400)
    if enhancer.state != "ready":
        return json_error("o melhorador de prompt ainda não está pronto", 503)
    touch()
    try:
        result = await asyncio.wait_for(
            enhancer.enhance(text, str(body.get("mode", "t2v")), body.get("camera", "none") != "none"),
            ENHANCE_TIMEOUT)
    except asyncio.TimeoutError:
        return json_error("o melhorador demorou demais; tente de novo", 504)
    except Exception as exc:
        return json_error(f"o melhorador falhou: {exc}", 500)
    return web.json_response({"prompt": result})


async def keep_alive(_request):
    touch()
    return web.json_response(idle_info())


async def shutdown(_request):
    """So levanta a bandeira; quem desliga os processos e o launcher do notebook."""
    STATE["shutdown"] = True
    return web.json_response({"ok": True})


async def restart_worker(request):
    """Pede ao launcher que reinicie um ComfyUI com outras opcoes de linha de comando; o
    launcher chama de novo com {"done": true} quando termina. E como se mede, numa sessao so,
    o que cada opcao rende na GPU."""
    try:
        worker = WORKERS[int(request.match_info["index"])]
        body = await request.json()
        args = body.get("args", [])
    except (ValueError, IndexError, AttributeError):
        raise web.HTTPNotFound() from None
    if body.get("done"):
        STATE.update(restart=None, nodes_at=0.0, catalog_at=0.0)
        worker["retry_at"] = 0.0
        return web.json_response({"ok": True})
    allowed = isinstance(args, list) and all(
        arg in RESTART_ARGS or str(arg).replace(".", "", 1).isdigit() for arg in args)
    if not MANAGED or not allowed:
        return json_error("opções não permitidas", 400)
    if worker["task"] or STATE["restart"]:
        return json_error("este ComfyUI está ocupado", 409)
    STATE["restart"] = {"worker": worker["index"], "args": [str(arg) for arg in args]}
    return web.json_response({"ok": True})


async def get_log(request):
    """O fim de um registro da maquina, para diagnostico: e onde o ComfyUI diz quanto tempo
    cada passo levou, se o modelo coube na GPU e por que um job falhou."""
    name = request.query.get("name", "comfyui")
    if name not in LOG_NAMES:
        raise web.HTTPNotFound()
    try:
        with (LOGS / f"{name}.log").open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - MAX_LOG_BYTES))
            data = handle.read()
    except OSError:
        data = b""
    return web.Response(text=data.decode("utf-8", errors="replace"), content_type="text/plain")


CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Authorization, Content-Type",
    "Access-Control-Max-Age": "86400",
}


@web.middleware
async def cors(request, handler):
    """Permite que o site estatico (outro dominio) chame a API. So com senha definida:
    sem senha o servidor fica restrito a mesma origem."""
    if not PASSWORD:
        return await handler(request)
    if request.method == "OPTIONS":
        return web.Response(status=204, headers=CORS_HEADERS)
    try:
        response = await handler(request)
    except web.HTTPException as exc:
        exc.headers.update(CORS_HEADERS)
        raise
    response.headers.update(CORS_HEADERS)
    return response


@web.middleware
async def auth(request, handler):
    if PASSWORD:
        header = request.headers.get("Authorization", "")
        ok = False
        if header.startswith("Basic "):
            try:
                _, _, given = base64.b64decode(header[6:]).decode().partition(":")
                ok = hmac.compare_digest(given.encode(), PASSWORD.encode())
            except ValueError:
                pass
        if not ok:
            return web.Response(status=401, text="senha necessária",
                                headers={"WWW-Authenticate": 'Basic realm="Wan Studio"'})
    return await handler(request)


async def clear_queues(app):
    """Ao subir, nenhum job esta em andamento: o que restar nas filas e de uma execucao
    anterior deste servidor e so ocuparia a GPU."""
    for worker in WORKERS:
        try:
            async with app["http"].post(worker["url"] + "/queue", json={"clear": True}):
                pass
            async with app["http"].post(worker["url"] + "/interrupt", json={}):
                pass
        except (aiohttp.ClientError, asyncio.TimeoutError):
            pass


async def background(app):
    app["http"] = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=600, connect=5))
    app["tasks"] = set()
    app["engine"] = asyncio.Lock()
    app["freeing"] = asyncio.Lock()
    if not ONLY_SWAP:  # o modelo dele nem foi baixado: ficaria esperando para sempre
        enhancer.start()
    await clear_queues(app)
    loops = [asyncio.create_task(poller(app))]
    loops += [asyncio.create_task(progress_listener(app, worker)) for worker in WORKERS]
    yield
    for task in loops + list(app["tasks"]):
        task.cancel()
    await app["http"].close()


def make_app():
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(UPLOADS, ignore_errors=True)  # sobras de uma execucao interrompida
    UPLOADS.mkdir(parents=True, exist_ok=True)
    load_jobs()
    app = web.Application(middlewares=[cors, auth])
    app.cleanup_ctx.append(background)
    app.add_routes([
        web.get("/", index),
        web.get("/api/presets", get_presets),
        web.get("/api/status", get_status),
        web.get("/api/jobs", list_jobs),
        web.post("/api/generate", generate),
        web.delete("/api/jobs/{id}", delete_job),
        web.post("/api/enhance", enhance_prompt),
        web.post("/api/touch", keep_alive),
        web.post("/api/shutdown", shutdown),
        web.post("/api/worker/{index}/restart", restart_worker),
        web.get("/api/log", get_log),
        web.static("/static", STATIC),
        web.static("/media", OUTPUTS),
    ])
    return app


if __name__ == "__main__":
    if HOST not in ("127.0.0.1", "localhost") and not PASSWORD:
        sys.exit("Recusando abrir para a rede sem senha: defina APP_PASSWORD ou use HOST=127.0.0.1.")
    web.run_app(make_app(), host=HOST, port=PORT)
