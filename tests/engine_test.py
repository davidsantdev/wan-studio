"""Testa o motor do servidor com dois ComfyUI simulados (duas "GPUs"), pela API, sem navegador.

  python tests/engine_test.py

Cobre o que a troca de pessoa longa exige: o video cortado em pedacos encadeados a partir do
meio, dois pedacos por vez (um avancando, um recuando), a emenda com o audio, cancelamento e
erro no meio do caminho, e o rodizio da segunda GPU com o melhorador de prompt.
Precisa de Python com aiohttp e de ffmpeg no PATH.
"""
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORK = HERE / ".work" / "engine"
SERVER = "http://127.0.0.1:8093"
FAKES = ("http://127.0.0.1:8190", "http://127.0.0.1:8191")
# Uma segunda pilha, com uma placa grande so (como as do Lightning AI), para a qualidade ultra.
BIG_SERVER, BIG_FAKE = "http://127.0.0.1:8094", "http://127.0.0.1:8192"
failures = []

sys.path.insert(0, str(HERE.parent / "app"))
import stitch  # noqa: E402
import workflows  # noqa: E402


def check(label, condition, detail=""):
    print(("ok    " if condition else "FALHA ") + label + (f"  [{detail}]" if detail else ""), flush=True)
    if not condition:
        failures.append(label)


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *map(str, args)], check=True)


def call(url, data=None, content_type=None, method=None):
    headers = {"Content-Type": content_type} if content_type else {}
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def get(path, base=None):
    return json.loads(call((base or SERVER) + path)[1])


def post(path, body):
    status, raw = call(SERVER + path, json.dumps(body).encode(), "application/json", "POST")
    return status, json.loads(raw or b"{}")


def generate(params, **files):
    boundary = "----wan" + secrets.token_hex(8)
    body = f'--{boundary}\r\nContent-Disposition: form-data; name="params"\r\n\r\n{json.dumps(params)}\r\n'.encode()
    for field, path in files.items():
        mime = "video/mp4" if path.suffix == ".mp4" else "image/png"
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{path.name}"\r\n'
                 f"Content-Type: {mime}\r\n\r\n").encode() + path.read_bytes() + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    status, raw = call(SERVER + "/api/generate", body, f"multipart/form-data; boundary={boundary}", "POST")
    return status, json.loads(raw)


def job(job_id):
    return next((item for item in get("/api/jobs")["jobs"] if item["id"] == job_id), None)


def wait_job(job_id, seconds=120, watch=None):
    """Espera o job terminar; `watch(job)` e chamado a cada consulta."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        current = job(job_id)
        if watch:
            watch(current)
        if current is None or current["status"] in ("done", "error"):
            return current
        time.sleep(0.3)
    return job(job_id)


def graphs(base, warm=False):
    """Os grafos que um simulador recebeu, na ordem, como listas de nos: os dos pedidos ou,
    com `warm`, os de aquecimento (que nao gravam nada: terminam num PreviewImage)."""
    found = [list(graph.values()) for graph in get("/_graphs", base).values()]
    return [graph for graph in found if (node(graph, "PreviewImage") is not None) == warm]


def node(graph, kind):
    return next((item["inputs"] for item in graph if item["class_type"] == kind), None)


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-show_entries",
                          "stream=codec_type,width,height,r_frame_rate,nb_read_frames", "-of", "json", str(path)],
                         capture_output=True, text=True).stdout
    return json.loads(out)["streams"]


def spawn(script, log, *args, **env):
    handle = open(WORK / log, "w", encoding="utf-8")
    full = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8", **env}
    for name in ("COMFY_URL", "APP_PASSWORD"):
        full.pop(name, None)
    return subprocess.Popen([sys.executable, str(HERE / script), *args], stdout=handle, stderr=subprocess.STDOUT, env=full)


def wait_for(url, seconds=60):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            if call(url)[0] in (200, 404):
                return
        except OSError:  # ainda nao esta escutando (numa maquina ocupada o processo demora a subir)
            pass
        time.sleep(0.5)
    raise SystemExit(f"{url} não respondeu; veja os logs em {WORK}")


def scenarios():
    person, short, long = WORK / "pessoa.png", WORK / "curto.mp4", WORK / "longo.mp4"
    # O plano de antes de 12/10/2026, que o motor ainda executa por ajuste de teste e que exercita
    # o que as qualidades de hoje nao usam: comeco por um pedaco curto no meio do video, as duas
    # GPUs ao mesmo tempo, pedacos gerados ao contrario e quadros intermediarios calculados.
    old = {"fps": 8, "out_fps": 16, "chunk": 49, "middle": 21}
    swap = {"mode": "swap", "duration": 10, "quality": "rapida", "seed": 5, "expert": old}

    # ---- aquecimento: com a troca de pessoa instalada, cada GPU carrega tudo de antemao
    deadline = time.time() + 20
    while time.time() < deadline and get("/api/status").get("warming") != 2:
        time.sleep(0.1)
    warming = get("/api/status").get("warming")
    # Um pedido de verdade chega no meio (dois videos, um para cada GPU): passa na frente.
    code, reply = generate({"mode": "t2v", "model": "14b", "prompt": "ondas", "count": 2, "duration": 3})
    ids = [item["id"] for item in reply.get("jobs", [])]
    finished = [wait_job(job_id) for job_id in ids]
    warm = [graphs(base, warm=True) for base in FAKES]
    first = [found[0] if found else [] for found in warm]
    check("G0 aquecimento: cada GPU recebe uma vez um pedaco minimo da troca, que nao grava nada",
          warming == 2 and all(len(found) == 1 for found in warm)
          and all((node(graph, "WanAnimateToVideo") or {}).get("length") == 17 and node(graph, "WanAnimateToVideo")["width"] == 256
                  and node(graph, "KSampler")["steps"] == 1 and node(graph, "SaveImage") is None
                  and node(graph, "PoseAndFaceDetection") is not None and node(graph, "WanStudioPersonMask") is None
                  for graph in first), f"{[len(found) for found in warm]} grafos; aquecendo: {warming}")
    stopped = []
    for base in FAKES:
        for prompt_id, graph in get("/_graphs", base).items():
            if any(item["class_type"] == "PreviewImage" for item in graph.values()):
                entry = get(f"/history/{prompt_id}", base).get(prompt_id, {})
                stopped.append(any(message[0] == "execution_interrupted" for message in entry.get("status", {}).get("messages", [])))
    check("G0a um pedido de verdade interrompe o aquecimento das duas GPUs e roda em seguida",
          stopped == [True, True] and len(finished) == 2 and all(item and item["status"] == "done" for item in finished),
          f"interrompidos {stopped}; pedidos {[item and item['status'] for item in finished]}")
    for job_id in ids:
        call(SERVER + f"/api/jobs/{job_id}", method="DELETE")
    deadline = time.time() + 30
    while time.time() < deadline and get("/api/status").get("warming"):
        time.sleep(0.3)
    time.sleep(5)  # ENHANCER_RETURN_AFTER aqui e 2 s
    status = get("/api/status")
    check("G0b depois disso nenhuma GPU fica aquecendo e o melhorador de prompt recebe a GPU de volta",
          status.get("warming") == 0 and status["enhancer"]["state"] == "ready", str((status.get("warming"), status["enhancer"]["state"])))
    check("G1 duas GPUs aparecem no estado, e a sessao oferece todos os modos",
          status["workers"] == 2 and status["gpu"].startswith("2× Tesla T4") and "focus" in status and status["focus"] is None,
          f"{status['gpu']} {status.get('focus')}")

    # ---- troca de pessoa de 10 s na qualidade rapida: 8 quadros gerados por segundo (os
    # outros 8 sao interpolados em cada pedaco), 3 pedacos, do meio para as pontas, nas duas GPUs
    before = [len(graphs(base)) for base in FAKES]
    code, reply = generate(swap, start_image=person, video=long)
    made = reply["jobs"][0] if code == 200 else {}
    check("G2 aceita 10 s e planeja os pedacos", code == 200 and made.get("chunks") == 3 and made.get("duration") == 10.0
          and (made.get("width"), made.get("height")) == (832, 480) and made.get("quality") == "rapida"
          and (made.get("fps"), made.get("out_fps"), made.get("factor")) == (8, 16, 2), str(reply)[:200])
    seen = {"sleeping": False, "pieces": set(), "stages": set()}

    def watch(current):
        if current:
            seen["pieces"].add(current.get("chunks_done"))
            seen["stages"].add(current.get("stage"))
        seen["sleeping"] = seen["sleeping"] or get("/api/status")["enhancer"]["state"] == "sleeping"
    began = time.time()
    done = wait_job(made.get("id"), watch=watch)
    took = time.time() - began
    check("G3 a troca termina", bool(done) and done["status"] == "done", str(done and (done["status"], done.get("error"))))
    per_gpu = [graphs(base)[start:] for base, start in zip(FAKES, before)]
    every = per_gpu[0] + per_gpu[1]
    animate = [node(graph, "WanAnimateToVideo") for graph in every]
    check("G4 tres pedacos, divididos entre as duas GPUs", len(every) == 3 and min(len(per_gpu[0]), len(per_gpu[1])) >= 1,
          f"{len(per_gpu[0])} + {len(per_gpu[1])}")
    check("G5 um pedaco curto de partida e dois que continuam dele, um para cada lado",
          sorted(item["length"] for item in animate) == [21, 37, 37]
          and [item["length"] for item in animate if "continue_motion" not in item] == [21],
          str(sorted((item["length"], "continue_motion" in item) for item in animate)))
    check("G6 qualidade rapida: 4 passos, gerado em 640x368, com um intermediario entre cada dois quadros",
          all(node(graph, "KSampler")["steps"] == 4 for graph in every)
          and all((item["width"], item["height"]) == (640, 368) for item in animate)
          and all((node(graph, "FrameInterpolate") or {}).get("multiplier") == 2 for graph in every))
    result = WORK / "resultado.mp4"
    result.write_bytes(call(SERVER + done["url"])[1] if done and done.get("url") else b"")
    streams = probe(result) if result.stat().st_size else []
    video = next((s for s in streams if s["codec_type"] == "video"), {})
    check("G7 video emendado: os 80 quadros gerados e os 79 intermediarios, a 16 fps, no tamanho final, com o audio",
          video.get("nb_read_frames") == "159" and video.get("r_frame_rate") == "16/1"
          and (video.get("width"), video.get("height")) == (832, 480) and any(s["codec_type"] == "audio" for s in streams),
          str([(s["codec_type"], s.get("nb_read_frames")) for s in streams]))
    check("G8 o card acompanha os pedacos e a emenda", {0, 1} <= seen["pieces"] and "juntando os pedaços" in seen["stages"],
          f"pedacos {sorted(p for p in seen['pieces'] if p is not None)}; etapas {sorted(s for s in seen['stages'] if s)}")
    check("G9 o melhorador de prompt cede a segunda GPU durante a geracao", seen["sleeping"])
    time.sleep(4)
    check("G10 e volta sozinho depois", get("/api/status")["enhancer"]["state"] == "ready")
    leftovers = [entry.name for entry in (WORK / "data" / "uploads").iterdir()]
    check("G11 nada do job fica guardado no servidor depois de pronto", not leftovers, str(leftovers))
    call(SERVER + f"/api/jobs/{made.get('id')}", method="DELETE")

    # ---- uma GPU so (ajuste de teste): os pedacos avancam do comeco, um de cada vez
    before = [len(graphs(base)) for base in FAKES]
    code, reply = generate({**swap, "quality": "boa", "expert": {"middle": 0, "worker": 0, "chunk": 33, "size": [480, 480],
                                                               "fps": 16, "steps": 6}},
                           start_image=person, video=long)
    done = wait_job(reply["jobs"][0]["id"]) if code == 200 else None
    fresh = graphs(FAKES[0])[before[0]:]
    lengths = [node(graph, "WanAnimateToVideo")["length"] for graph in fresh]
    check("G12 ajustes de teste: tudo numa GPU, pedacos de 33 avancando, 6 passos, tamanho pedido",
          bool(done) and done["status"] == "done" and len(graphs(FAKES[1])) == before[1] and lengths[:2] == [33, 33]
          and "continue_motion" not in node(fresh[0], "WanAnimateToVideo")
          and all("continue_motion" in node(graph, "WanAnimateToVideo") for graph in fresh[1:])
          and node(fresh[0], "KSampler")["steps"] == 6 and node(fresh[0], "WanAnimateToVideo")["width"] == 480,
          f"{len(fresh)} pedacos {lengths}")
    check("G12a fps de teste: todos os quadros gerados, sem interpolar",
          bool(done) and (done.get("fps"), done.get("out_fps"), done.get("factor")) == (16, 16, 1)
          and all(node(graph, "FrameInterpolate") is None for graph in fresh), str(done and (done.get("fps"), done.get("factor"))))
    call(SERVER + f"/api/jobs/{reply['jobs'][0]['id']}", method="DELETE")

    # ---- todos os quadros gerados (16 fps) nas duas GPUs: 5 pedacos, dois niveis para cada lado
    def frames_of(done, name):
        target = WORK / name
        target.write_bytes(call(SERVER + done["url"])[1] if done and done.get("url") else b"")
        found = next((s for s in (probe(target) if target.stat().st_size else []) if s["codec_type"] == "video"), {})
        return int(found.get("nb_read_frames") or 0), found.get("r_frame_rate")
    before = [len(graphs(base)) for base in FAKES]
    code, reply = generate({**swap, "expert": {"fps": 16, "chunk": 49, "middle": 21}}, start_image=person, video=long)
    done = wait_job(reply["jobs"][0]["id"]) if code == 200 else None
    fresh = [graph for base, start in zip(FAKES, before) for graph in graphs(base)[start:]]
    pieces = [node(graph, "WanAnimateToVideo") for graph in fresh]
    check("G12b a 16 fps: 5 pedacos, 4 continuando de um vizinho, 160 quadros sem intermediarios",
          bool(done) and done["status"] == "done" and sorted(item["length"] for item in pieces) == [21, 33, 33, 49, 49]
          and sum("continue_motion" in item for item in pieces) == 4
          and all(node(graph, "FrameInterpolate") is None for graph in fresh)
          and frames_of(done, "dezesseis.mp4") == (160, "16/1"), str(sorted(item["length"] for item in pieces)))
    call(SERVER + f"/api/jobs/{reply['jobs'][0]['id']}", method="DELETE")

    # ---- duas cadeias que avancam, uma em cada GPU: a segunda parte de quadros do primeiro
    # pedaco (a ancora) e so comeca depois dele; na emenda entre as duas ha mistura
    def motions(graph):
        return [item["inputs"]["file"] for item in graph if item["class_type"] == "LoadVideo"
                and "_continua" in item["inputs"]["file"]]
    for anchor, label in ((True, "G12d"), (False, "G12e")):
        before = [len(graphs(base)) for base in FAKES]
        code, reply = generate({**swap, "expert": {"fps": 16, "chunk": 49, "chains": 2, "anchor": anchor}},
                               start_image=person, video=long)
        made = reply["jobs"][0] if code == 200 else {}
        done = wait_job(made.get("id"))
        per_gpu = [graphs(base)[start:] for base, start in zip(FAKES, before)]
        fresh = per_gpu[0] + per_gpu[1]
        plan = stitch.chains(160, 49, 5, anchor=anchor)
        second = next(index for index, piece in enumerate(plan) if piece.get("blend"))
        lengths = sorted(node(graph, "WanAnimateToVideo")["length"] for graph in fresh)
        fed = sorted(name.rsplit("_", 2)[-2] for graph in fresh for name in motions(graph))
        expected = [f"{index:03d}" for index in range(1, len(plan)) if anchor or index != second]
        check(f"{label} duas cadeias {'com' if anchor else 'sem'} ancora: os pedacos do plano nas duas GPUs, "
              f"{'todos menos o primeiro' if anchor else 'so os que continuam de um vizinho'} recebendo quadros "
              "ja gerados, e o video inteiro no fim",
              bool(done) and done["status"] == "done" and made.get("chunks") == len(plan)
              and lengths == sorted(piece["length"] for piece in plan) and fed == expected
              and min(len(per_gpu[0]), len(per_gpu[1])) >= 2 and frames_of(done, f"cadeias_{label}.mp4") == (160, "16/1"),
              f"{len(per_gpu[0])} + {len(per_gpu[1])} pedacos {lengths}; com quadros do vizinho: {fed}")
        call(SERVER + f"/api/jobs/{made.get('id')}", method="DELETE")
    code, reply = generate({**swap, "expert": {"chains": 3}}, start_image=person, video=long)
    check("G12f numero de cadeias invalido: recusado", code == 400, str(reply)[:80])

    # ---- a pessoa inteira (ajuste de teste): o recorte do SAM2 completado pelo do BiRefNet
    before = [len(graphs(base)) for base in FAKES]
    code, reply = generate({**swap, "duration": 3, "expert": {"whole": True, "worker": 0}}, start_image=person, video=long)
    made = reply["jobs"][0] if code == 200 else {}
    done = wait_job(made.get("id"))
    fresh = graphs(FAKES[0])[before[0]:]
    check("G12h pessoa inteira: o recorte de cada pedaco passa pelo BiRefNet antes de ir para o Animate",
          bool(done) and done["status"] == "done" and len(fresh) == 1 and node(fresh[0], "WanStudioWholePerson") is not None
          and (node(fresh[0], "WanStudioForegroundLoader") or {}).get("model") == "BiRefNet-portrait",
          str(done and (done["status"], done.get("error"))))
    call(SERVER + f"/api/jobs/{made.get('id')}", method="DELETE")

    # ---- o rosto em detalhe (ajuste de teste): cada pedaco ganha uma segunda passada na cabeca
    before = [len(graphs(base)) for base in FAKES]
    code, reply = generate({**swap, "duration": 3, "expert": {"face": True, "worker": 0}}, start_image=person, video=long)
    made = reply["jobs"][0] if code == 200 else {}
    done = wait_job(made.get("id"))
    fresh = graphs(FAKES[0])[before[0]:]
    check("G12i rosto em detalhe: dois Animate e dois samplers no pedaco, e o video sai com os quadros do pedaco",
          bool(done) and done["status"] == "done" and len(fresh) == 1
          and sum(item["class_type"] == "WanAnimateToVideo" for item in fresh[0]) == 2
          and sum(item["class_type"] == "KSampler" for item in fresh[0]) == 2 and node(fresh[0], "WanStudioFacePaste") is not None,
          str(done and (done["status"], done.get("error"))))
    call(SERVER + f"/api/jobs/{made.get('id')}", method="DELETE")

    # ---- a janela na pessoa (ajuste de teste), nas duas cadeias: cada pedaco gera so o retangulo
    # em que a pessoa esta e o cola de volta no video; a cor so e igualada a de um vizinho no tempo
    before = [len(graphs(base)) for base in FAKES]
    code, reply = generate({**swap, "expert": {"fps": 16, "chunk": 49, "chains": 2, "window": True}},
                           start_image=person, video=long)
    made = reply["jobs"][0] if code == 200 else {}
    done = wait_job(made.get("id"))
    fresh = [graph for base, start in zip(FAKES, before) for graph in graphs(base)[start:]]
    plan = stitch.chains(160, 49, 5)
    matched = sum("previous" in (node(graph, "WanStudioPaste") or {}) for graph in fresh)
    check("G12g janela na pessoa: todo pedaco passa pela janela e pela colagem, o Animate gera no tamanho dela, "
          "e a cor e igualada so nos pedacos que continuam de um vizinho no tempo",
          bool(done) and done["status"] == "done" and len(fresh) == len(plan)
          and all(node(graph, "WanStudioWindow") is not None and node(graph, "WanStudioPaste") is not None
                  and isinstance(node(graph, "WanAnimateToVideo")["width"], list) for graph in fresh)
          and matched == len(plan) - 2 and frames_of(done, "janela.mp4") == (160, "16/1"),
          f"{len(fresh)} pedacos, {matched} com a cor igualada")
    call(SERVER + f"/api/jobs/{made.get('id')}", method="DELETE")

    # ---- sem o modelo de interpolacao: os intermediarios ficam para o ffmpeg, na emenda
    before = [len(graphs(base)) for base in FAKES]
    code, reply = generate({**swap, "expert": {**old, "smooth": "ffmpeg"}}, start_image=person, video=long)
    done = wait_job(reply["jobs"][0]["id"]) if code == 200 else None
    fresh = [graph for base, start in zip(FAKES, before) for graph in graphs(base)[start:]]
    count, rate = frames_of(done, "ffmpeg.mp4") if done and done["status"] == "done" else (0, None)
    check("G12c intermediarios pelo ffmpeg: os pedacos saem so com os quadros gerados e o video, a 16 fps",
          bool(done) and done["status"] == "done" and done.get("factor") == 1 and len(fresh) == 3
          and all(node(graph, "FrameInterpolate") is None for graph in fresh) and rate == "16/1" and 157 <= count <= 160,
          f"{count} quadros a {rate}")
    call(SERVER + f"/api/jobs/{reply['jobs'][0]['id']}", method="DELETE")

    # ---- video curto: um pedaco so
    before = sum(len(graphs(base)) for base in FAKES)
    code, reply = generate({**swap, "duration": 5}, start_image=person, video=short)
    made = reply["jobs"][0] if code == 200 else {}
    done = wait_job(made.get("id"))
    check("G13 video de 3 s: um pedaco, sem vizinho", bool(done) and done["status"] == "done" and made.get("chunks") == 1
          and sum(len(graphs(base)) for base in FAKES) == before + 1, str(made.get("chunks")))
    call(SERVER + f"/api/jobs/{made.get('id')}", method="DELETE")

    # ---- qualidade maxima: gerado com mais pixels, entregue nesse tamanho
    before = [len(graphs(base)) for base in FAKES]
    code, reply = generate({"mode": "swap", "duration": 5, "quality": "maxima", "seed": 5}, start_image=person, video=short)
    made = reply["jobs"][0] if code == 200 else {}
    done = wait_job(made.get("id"))
    fine = ([node(graph, "WanAnimateToVideo") for base, start in zip(FAKES, before) for graph in graphs(base)[start:]]
            or [{"width": 0, "height": 0}])[-1]
    check("G13d qualidade maxima: gerado e entregue em 1024x576, todos os quadros, em pedacos de 49",
          bool(done) and done["status"] == "done" and (made.get("width"), made.get("height")) == (1024, 576)
          and (fine["width"], fine["height"]) == (1024, 576) and made.get("chunk") == 49
          and (made.get("fps"), made.get("out_fps"), made.get("factor")) == (16, 16, 1),
          f"{made.get('width')}x{made.get('height')} gerado {fine['width']}x{fine['height']}")
    call(SERVER + f"/api/jobs/{made.get('id')}", method="DELETE")

    # ---- qualidade ultra numa placa de 16 GB: recusada antes de qualquer trabalho
    code, reply = generate({**swap, "duration": 5, "quality": "ultra"}, start_image=person, video=short)
    check("G13f qualidade ultra numa T4: recusada, dizendo de quanta memoria precisa",
          code == 400 and "20 GB" in reply.get("error", "") and get("/api/status").get("vram") == 15.0
          and not list((WORK / "data" / "uploads").iterdir()), str(reply)[:120])

    # ---- trecho de 1,4 s: a 8 quadros por segundo nao daria um pedaco; gera todos os quadros
    code, reply = generate({**swap, "duration": 5, "start": 1.6}, start_image=person, video=short)
    made = reply["jobs"][0] if code == 200 else {}
    done = wait_job(made.get("id"))
    check("G13a trecho curto demais para gerar so parte dos quadros: gera a 16 fps",
          bool(done) and done["status"] == "done" and (made.get("fps"), made.get("out_fps"), made.get("factor")) == (16, 16, 1)
          and made.get("length") == 21, str((code, made.get("fps"), made.get("length"), str(reply)[:80])))
    call(SERVER + f"/api/jobs/{made.get('id')}", method="DELETE")

    # ---- alta definicao: os quadros gerados em 480p sao restaurados (SeedVR2) e saem em 720p
    def split(fresh):
        """Dos grafos novos: (os de geracao, os de restauracao)."""
        return ([graph for graph in fresh if node(graph, "WanAnimateToVideo") is not None],
                [graph for graph in fresh if node(graph, "SeedVR2Conditioning") is not None])

    def lanczos(graph):  # o recorte da pessoa tambem usa ImageScale: vale o que amplia com lanczos
        return next((item["inputs"] for item in graph
                     if item["class_type"] == "ImageScale" and item["inputs"].get("upscale_method") == "lanczos"), {})
    before = [len(graphs(base)) for base in FAKES]
    stages = set()
    code, reply = generate({**swap, "duration": 5, "quality": "boa", "hd": True, "expert": {**old, "fps": 12, "out_fps": 24}},
                           start_image=person, video=short)
    made = reply["jobs"][0] if code == 200 else {}
    done = wait_job(made.get("id"), watch=lambda current: stages.add((current or {}).get("stage")))
    made_graphs, fixed = split([graph for base, start in zip(FAKES, before) for graph in graphs(base)[start:]])
    count, rate = frames_of(done, "hd.mp4") if done and done["status"] == "done" else (0, None)
    hd = WORK / "hd.mp4"
    size = next(((s["width"], s["height"]) for s in (probe(hd) if hd.stat().st_size else []) if s["codec_type"] == "video"), None)
    scale = lanczos(fixed[0]) if fixed else {}
    check("G13b alta definicao: o pedaco e gerado bruto em 480p; outro grafo o restaura em 1248x720 e interpola",
          bool(done) and done["status"] == "done" and (made.get("width"), made.get("height")) == (1248, 720)
          and made.get("hd_method") == "seedvr2" and len(made_graphs) == 1 and len(fixed) == 1
          and node(made_graphs[0], "WanAnimateToVideo")["width"] == 832 and node(made_graphs[0], "FrameInterpolate") is None
          and node(made_graphs[0], "SeedVR2Conditioning") is None and node(fixed[0], "WanAnimateToVideo") is None
          and node(fixed[0], "LoadVideo") is not None and (scale.get("width"), scale.get("height")) == (1248, 720)
          and (node(fixed[0], "FrameInterpolate") or {}).get("multiplier") == 2
          and size == (1248, 720) and (count, rate) == (65, "24/1") and "restaurando os detalhes" in stages,
          f"{made.get('width')}x{made.get('height')} {made.get('hd_method')} {len(made_graphs)}+{len(fixed)} grafos, "
          f"arquivo {size} {count} quadros a {rate}")
    call(SERVER + f"/api/jobs/{made.get('id')}", method="DELETE")

    # ---- alta definicao num video de 10 s: os tres pedacos sao gerados, a memoria e liberada nas
    # duas GPUs e so entao os tres sao restaurados
    before = [len(graphs(base)) for base in FAKES]
    freed = [len(get("/_state", base).get("freed", [])) for base in FAKES]
    code, reply = generate({**swap, "hd": True}, start_image=person, video=long)
    made = reply["jobs"][0] if code == 200 else {}
    done = wait_job(made.get("id"))
    order = []  # por GPU, na ordem de chegada: "g" de geracao, "r" de restauracao
    for base, start in zip(FAKES, before):
        order.append("".join("r" if node(graph, "SeedVR2Conditioning") is not None else "g" for graph in graphs(base)[start:]))
    after = [get("/_state", base).get("freed", []) for base in FAKES]
    count, rate = frames_of(done, "hd_longo.mp4") if done and done["status"] == "done" else (0, None)
    check("G13e alta definicao em 10 s: 3 pedacos gerados, memoria liberada nas duas GPUs, 3 restaurados, 159 quadros",
          bool(done) and done["status"] == "done" and sorted("".join(order)) == list("gggrrr")
          and all("rg" not in sequence for sequence in order)
          and all(len(now) > was for now, was in zip(after, freed)) and (count, rate) == (159, "16/1")
          and not list((WORK / "data" / "uploads").iterdir()),
          f"ordem por GPU {order}; liberacoes {[len(now) - was for now, was in zip(after, freed)]}; {count} quadros a {rate}")
    call(SERVER + f"/api/jobs/{made.get('id')}", method="DELETE")

    # ---- ajustes de teste: a ampliacao simples no lugar da restauracao, e outra compressao do modelo
    code, reply = generate({**swap, "duration": 5, "hd": True, "expert": {"hd_method": "esrgan", "unet": "Q4_K_S"}},
                           start_image=person, video=short)
    made = reply["jobs"][0] if code == 200 else {}
    done = wait_job(made.get("id"))
    last = [graph for base in FAKES for graph in graphs(base) if node(graph, "ImageUpscaleWithModel") is not None]
    missing = generate({**swap, "duration": 5, "expert": {"unet": "Q6_K"}}, start_image=person, video=short)[0]
    check("G13c ampliacao simples a pedido; compressao do modelo que nao esta instalada e recusada",
          bool(done) and done["status"] == "done" and made.get("hd_method") == "esrgan" and len(last) == 1
          and node(last[0], "SeedVR2Conditioning") is None
          and node(last[0], "UnetLoaderGGUF")["unet_name"].endswith("Q4_K_S.gguf") and missing == 400,
          f"{made.get('hd_method')} {len(last)} recusa={missing}")
    call(SERVER + f"/api/jobs/{made.get('id')}", method="DELETE")

    # ---- as qualidades de hoje: todos os quadros, do primeiro ao ultimo, um pedaco de cada vez
    # (cada um continua do anterior), mesmo havendo duas GPUs
    before = [len(graphs(base)) for base in FAKES]
    together = set()
    code, reply = generate({"mode": "swap", "duration": 10, "quality": "boa", "seed": 5}, start_image=person, video=long)
    made = reply["jobs"][0] if code == 200 else {}
    done = wait_job(made.get("id"), watch=lambda _: together.add(sum(len(get("/queue", base)["queue_running"]) for base in FAKES)))
    fresh = [graph for base, start in zip(FAKES, before) for graph in graphs(base)[start:]]
    pieces = [node(graph, "WanAnimateToVideo") for graph in fresh]
    check("G19 qualidade boa: 16 quadros por segundo, 4 passos, pedacos de 77, sem pedaco de partida no meio",
          code == 200 and (made.get("fps"), made.get("out_fps"), made.get("factor")) == (16, 16, 1) and made.get("chunk") == 77
          and made.get("chunks") == 3 and made.get("steps") == 4 and (made.get("width"), made.get("height")) == (832, 480),
          str({key: made.get(key) for key in ("fps", "out_fps", "factor", "chunk", "chunks", "steps")}))
    check("G19a os pedacos saem em fila, um de cada vez: o primeiro sem vizinho, os outros continuando",
          bool(done) and done["status"] == "done" and sorted(item["length"] for item in pieces) == [17, 77, 77]
          and sum("continue_motion" not in item for item in pieces) == 1 and max(together, default=0) == 1
          and all(node(graph, "FrameInterpolate") is None for graph in fresh),
          f"{sorted(item['length'] for item in pieces)}; ao mesmo tempo: {sorted(together)}")
    check("G19b arquivo: os 160 quadros gerados, a 16 fps", frames_of(done, "boa.mp4") == (160, "16/1"),
          str(frames_of(done, "boa.mp4")))
    call(SERVER + f"/api/jobs/{made.get('id')}", method="DELETE")

    # ---- com a alta definicao, na T4, os pedacos voltam a ser de 49 quadros (a restauracao recebe
    # um pedaco inteiro de uma vez); um ajuste de teste que pede outro tamanho e respeitado
    code, reply = generate({"mode": "swap", "duration": 10, "quality": "boa", "seed": 5, "hd": True}, start_image=person, video=long)
    made = reply["jobs"][0] if code == 200 else {}
    call(SERVER + f"/api/jobs/{made.get('id')}", method="DELETE")
    code, reply = generate({"mode": "swap", "duration": 10, "quality": "boa", "seed": 5, "hd": True, "expert": {"chunk": 77}},
                           start_image=person, video=long)
    asked = reply["jobs"][0] if code == 200 else {}
    call(SERVER + f"/api/jobs/{asked.get('id')}", method="DELETE")
    check("G19c com a alta definicao os pedacos sao de 49 quadros; pedido de teste com outro tamanho e respeitado",
          (made.get("chunk"), made.get("chunks"), made.get("hd_method")) == (49, 4, "seedvr2") and asked.get("chunk") == 77
          and "chunk_asked" not in made, f"{made.get('chunk')} em {made.get('chunks')} pedacos; pedido: {asked.get('chunk')}")
    time.sleep(6)  # os pedacos interrompidos saem das filas

    # ---- erro num pedaco derruba o job com a orientacao certa
    code, reply = generate({**swap, "prompt": "FAIL de proposito"}, start_image=person, video=long)
    done = wait_job(reply["jobs"][0]["id"])
    check("G14 falta de memoria num pedaco: erro com a saida para a troca de pessoa",
          bool(done) and done["status"] == "error" and "qualidade rápida" in done.get("error", ""), str(done and done.get("error")))
    call(SERVER + f"/api/jobs/{reply['jobs'][0]['id']}", method="DELETE")

    # ---- cancelar no meio: as duas GPUs param e nada fica para tras
    code, reply = generate(swap, start_image=person, video=long)
    job_id = reply["jobs"][0]["id"]
    deadline = time.time() + 60
    while time.time() < deadline and (job(job_id) or {}).get("chunks_done", 0) < 1:
        time.sleep(0.2)
    call(SERVER + f"/api/jobs/{job_id}", method="DELETE")
    time.sleep(6)
    queues = [get("/queue", base) for base in FAKES]
    check("G15 cancelar no meio esvazia as duas filas e apaga o que foi enviado",
          not get("/api/jobs")["jobs"] and all(not q["queue_running"] and not q["queue_pending"] for q in queues)
          and not list((WORK / "data" / "uploads").iterdir()), json.dumps(queues)[:120])

    # ---- lote de 2 videos: um em cada GPU, ao mesmo tempo
    before = [len(graphs(base)) for base in FAKES]
    code, reply = generate({"mode": "t2v", "model": "14b", "prompt": "ondas", "count": 2, "duration": 3})
    ids = [item["id"] for item in reply.get("jobs", [])]
    time.sleep(2.5)
    both = [item["status"] for item in get("/api/jobs")["jobs"]]
    finished = [wait_job(job_id) for job_id in ids]
    after = [len(graphs(base)) for base in FAKES]
    check("G16 lote de 2: os dois rodam ao mesmo tempo, um em cada GPU", both == ["running", "running"]
          and [a - b for a, b in zip(after, before)] == [1, 1] and all(item and item["status"] == "done" for item in finished),
          f"{both} {[a - b for a, b in zip(after, before)]}")
    for job_id in ids:
        call(SERVER + f"/api/jobs/{job_id}", method="DELETE")

    check("G16b o aquecimento nao se repete depois dos pedidos", all(len(graphs(base, warm=True)) == 1 for base in FAKES),
          str([len(graphs(base, warm=True)) for base in FAKES]))

    # ---- reinicio de um ComfyUI com outras opcoes (so para medir na GPU)
    bad = post("/api/worker/1/restart", {"args": ["--listen", "0.0.0.0"]})[0]
    asked = post("/api/worker/1/restart", {"args": ["--use-sage-attention", "--reserve-vram", "1.5"]})[0]
    pending = get("/api/status")["restart"]
    twice = post("/api/worker/0/restart", {"args": []})[0]
    post("/api/worker/1/restart", {"done": True})
    check("G17 reinicio com opcoes: so as permitidas, um por vez, e o pedido some quando o launcher confirma",
          (bad, asked, twice) == (400, 200, 409) and pending == {"worker": 1, "args": ["--use-sage-attention", "--reserve-vram", "1.5"]}
          and get("/api/status")["restart"] is None, f"{bad} {asked} {twice} {pending}")
    return took


def big_scenarios():
    """A qualidade ultra, numa pilha com uma placa de 45 GB e o Animate em fp8 e em GGUF."""
    global SERVER
    SERVER = BIG_SERVER
    person, long = WORK / "pessoa.png", WORK / "longo.mp4"
    names = ["Wan2_2-Animate-14B_fp8_e4m3fn_scaled_KJ.safetensors", "Wan2.2-Animate-14B-Q4_K_S.gguf"]
    lists = {"unet": [names[0]], "gguf": [names[1]], "vae": ["wan_2.1_vae.safetensors"],
             "clip": [workflows.TEXT_ENCODER], "clip_vision": ["clip_vision_h.safetensors"],
             "detection": ["vitpose-l-wholebody.onnx", "yolov10m.onnx"], "nodes": workflows.CATALOG["animate"]["nodes"]}
    chosen = [workflows.available({**lists, "vram": vram})["animate"]["files"]["unet"] for vram in (45.0, 15.0, None)]
    check("G18 com os dois modelos instalados: fp8 na placa grande (ou desconhecida), GGUF na de 16 GB",
          chosen == [names[0], names[1], names[0]], str(chosen))

    status = get("/api/status")
    check("G18a placa grande no estado: uma GPU, com a memoria dela, numa sessao so de troca de pessoa",
          status["workers"] == 1 and status.get("vram") == 45.0 and status["gpu"] == "NVIDIA L40S · 45 GB"
          and status.get("focus") == "swap", f"{status.get('gpu')} {status.get('vram')} {status.get('focus')}")
    code, reply = generate({"mode": "swap", "duration": 10, "quality": "ultra", "seed": 5, "hd": True},
                           start_image=person, video=long)
    made = reply["jobs"][0] if code == 200 else {}
    check("G18b ultra aceita: 720p direto, todos os quadros, pedacos de 77, 6 passos, sem alta definicao",
          code == 200 and (made.get("width"), made.get("height")) == (1280, 720) and made.get("chunk") == 77
          and made.get("chunks") == 3 and (made.get("fps"), made.get("out_fps"), made.get("factor")) == (16, 16, 1)
          and made.get("steps") == 6 and made.get("hd") is False and made.get("hd_method") is None, str(reply)[:200])
    done = wait_job(made.get("id"), seconds=180)
    fresh = graphs(BIG_FAKE)
    animate = [node(graph, "WanAnimateToVideo") or {} for graph in fresh]
    target = WORK / "ultra.mp4"
    target.write_bytes(call(SERVER + done["url"])[1] if done and done.get("url") else b"")
    video = next((s for s in (probe(target) if target.stat().st_size else []) if s["codec_type"] == "video"), {})
    check("G18c ultra gerada: tres pedacos (77, 77 e 17 quadros) em 1280x720 com o modelo fp8, cada um continuando do anterior",
          bool(done) and done["status"] == "done" and [item.get("length") for item in animate] == [77, 77, 17]
          and all((item.get("width"), item.get("height")) == (1280, 720) for item in animate)
          and ["continue_motion" in item for item in animate] == [False, True, True]
          and all(node(graph, "KSampler")["steps"] == 6 and node(graph, "UnetLoaderGGUF") is None
                  and node(graph, "UNETLoader")["unet_name"] == names[0] and node(graph, "SeedVR2Conditioning") is None
                  for graph in fresh),
          f"{[item.get('length') for item in animate]} {done and (done['status'], done.get('error'))}")
    check("G18d arquivo da ultra: 160 quadros a 16 fps, em 1280x720",
          (video.get("nb_read_frames"), video.get("r_frame_rate"), video.get("width"), video.get("height"))
          == ("160", "16/1", 1280, 720), str((video.get("nb_read_frames"), video.get("r_frame_rate"), video.get("width"))))
    call(SERVER + f"/api/jobs/{made.get('id')}", method="DELETE")

    # ---- na mesma placa, a qualidade boa com alta definicao segue como antes (480p restaurado para 720p)
    code, reply = generate({"mode": "swap", "duration": 5, "quality": "boa", "seed": 5, "hd": True},
                           start_image=person, video=WORK / "curto.mp4")
    made = reply["jobs"][0] if code == 200 else {}
    done = wait_job(made.get("id"), seconds=180)
    check("G18e na placa grande a alta definicao das outras qualidades continua valendo",
          bool(done) and done["status"] == "done" and made.get("hd_method") == "seedvr2"
          and (made.get("width"), made.get("height")) == (1248, 720) and made.get("chunk") == 77, str(reply)[:160])
    call(SERVER + f"/api/jobs/{made.get('id')}", method="DELETE")


def run_big():
    procs = [spawn("fake_comfy.py", "fake_grande.log", "--port", BIG_FAKE.rsplit(":", 1)[1], FAKE_ASSETS=str(WORK),
                   FAKE_SETS="animate,animate_fp8", FAKE_STEP="0.1", FAKE_GPU="NVIDIA L40S", FAKE_VRAM_GB="45"),
             spawn("run_server_enhancer.py", "server_grande.log", PORT=BIG_SERVER.rsplit(":", 1)[1], COMFY_URLS=BIG_FAKE,
                   ENHANCER_WORKER="-1", WARM_UP="0", SO_TROCA="1", DATA_DIR=str(WORK / "data_grande"),
                   LOG_DIR=str(WORK / "logs_grande"))]
    try:
        wait_for(BIG_FAKE + "/queue")
        wait_for(BIG_SERVER + "/api/presets")
        big_scenarios()
    finally:
        stop(procs)


def stop(procs):
    for proc in procs:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        else:
            proc.terminate()


def main():
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)
    ffmpeg("-f", "lavfi", "-i", "color=c=0x2a6fb0:size=640x640:duration=1", "-frames:v", "1", WORK / "pessoa.png")
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=416x240:rate=1:duration=1", "-frames:v", "1", WORK / "sample.png")
    for name, seconds in (("curto.mp4", 3), ("longo.mp4", 11), ("sample.mp4", 3)):
        ffmpeg("-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=24:duration={seconds}", "-f", "lavfi", "-i",
               f"sine=frequency=330:duration={seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", WORK / name)
    shared = WORK / "conjuntos.txt"
    procs = [spawn("fake_comfy.py", f"fake{index}.log", "--port", base.rsplit(":", 1)[1], FAKE_ASSETS=str(WORK),
                   FAKE_SETS="animate,14b_t2v,14b_i2v", FAKE_STEP="0.15", FAKE_SHARED=str(shared))
             for index, base in enumerate(FAKES)]
    procs.append(spawn("run_server_enhancer.py", "server.log", PORT="8093", COMFY_URLS=",".join(FAKES),
                       ENHANCER_WORKER="1", ENHANCER_RETURN_AFTER="2", WAN_MANAGED="1",
                       DATA_DIR=str(WORK / "data"), LOG_DIR=str(WORK / "logs")))
    try:
        for base in FAKES:
            wait_for(base + "/queue")
        wait_for(SERVER + "/api/presets")
        took = scenarios()
    finally:
        stop(procs)
    run_big()
    print(f"\n{len(failures)} falha(s)" + (": " + "; ".join(failures) if failures else "")
          + f"  (a troca de 10 s simulada levou {took:.0f} s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
