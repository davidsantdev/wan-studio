"""Servidor local do Wan Studio só para a troca de rosto em vídeo, rodando na GPU do seu PC
(FaceFusion + DirectML). Serve o próprio site e a API que ele usa; o navegador abre em
http://127.0.0.1:8080 e conversa com este servidor na mesma origem.

Só a aba "Trocar rosto → Vídeo" funciona aqui; os outros modos (trocar pessoa, trocar rosto em
foto, texto/imagem → vídeo) precisam da GPU grande e aparecem como não instalados.

Antes da primeira vez, rode `python local/instalar.py`.

Use apenas imagens e vídeos de pessoas que autorizaram o uso.
"""
import asyncio
import json
import re
import sys
import time
import uuid
from pathlib import Path

from aiohttp import web

import trocar_rosto_video as ff

REPO = ff.REPO                          # o site e os presets ficam no repositório
STATIC = REPO / "app" / "static"
SAIDA = ff.SAIDA
TRABALHO = ff.INSTALL / ".trabalho"     # vídeos enviados e recortados, por job (fora do repo)
PORTA = 8080

sys.path.insert(0, str(REPO / "app"))
try:
    import presets
    PRESETS = presets.public()
except Exception:
    PRESETS = {"cameras": [], "effects": [], "styles": []}

MODELOS = {nome: {"ok": nome == "faceswap", "turbo": True, "hd": None, "rife": False}
           for nome in ("5b", "14b_i2v", "14b_t2v", "animate", "klein", "faceswap")}
ETAPAS = {"analysing": (0.02, 0.06, "analisando o vídeo"), "extracting": (0.06, 0.12, "preparando os quadros"),
          "processing": (0.12, 0.93, "trocando o rosto"), "merging": (0.93, 0.99, "montando o vídeo")}
BARRA = re.compile(r"(analysing|extracting|processing|merging):\s*\d+%\|[^|]*\|\s*(\d+)/(\d+)")
GPU_TOKEN, GPU_ROTULO = (ff.detectar_gpu() or (None, "CPU (sem GPU)"))

JOBS = {}  # id -> dict do job (o que o site lê em /api/jobs)


def publico(job):
    return {k: v for k, v in job.items() if k not in ("proc", "work")}


async def ffprobe(*args):
    proc = await asyncio.create_subprocess_exec(
        str(ff.BIN / "ffprobe.exe"), "-v", "error", *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    out, _ = await proc.communicate()
    return out.decode("utf-8", "replace").strip()


async def preparar_clip(origem, destino, inicio, segundos):
    """Recorta o trecho pedido do vídeo, no tamanho e na velocidade dele (até 1920 px e 30 fps),
    com o som. Devolve (largura, altura, fps, quadros)."""
    info = await ffprobe("-select_streams", "v:0", "-show_entries",
                         "stream=width,height,avg_frame_rate:format=duration", "-of", "json", str(origem))
    data = json.loads(info or "{}")
    stream = (data.get("streams") or [{}])[0]
    largura, altura = int(stream.get("width", 0)), int(stream.get("height", 0))
    if not (largura and altura):
        raise ValueError("o vídeo enviado não pôde ser lido")
    top, _, bottom = str(stream.get("avg_frame_rate", "30/1")).partition("/")
    fps = min(30.0, (float(top) / float(bottom or 1)) or 30.0)
    escala = min(1.0, 1920 / max(largura, altura))
    largura, altura = max(2, round(largura * escala / 2) * 2), max(2, round(altura * escala / 2) * 2)
    ffmpeg = str(ff.BIN / "ffmpeg.exe")
    proc = await asyncio.create_subprocess_exec(
        ffmpeg, "-nostdin", "-y", "-v", "error", "-ss", f"{inicio:.3f}", "-i", str(origem),
        "-map", "0:v:0", "-map", "0:a:0?", "-t", f"{segundos:.3f}",
        "-vf", f"fps={fps:.3f},scale={largura}:{altura},setsar=1",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(destino))
    if await proc.wait() != 0:
        raise ValueError("não consegui preparar o trecho do vídeo")
    quadros = await ffprobe("-count_frames", "-select_streams", "v:0", "-show_entries",
                            "stream=nb_read_frames", "-of", "csv=p=0", str(destino))
    return largura, altura, round(fps, 3), int(quadros.split(",")[0] or 0)


async def rodar_job(job):
    """Prepara o trecho, roda o FaceFusion e vai marcando o andamento; no fim, o resultado."""
    work = Path(job["work"])
    origem, clip = work / "original.mp4", work / "clip.mp4"
    rostos = sorted(work.glob("rosto_*.png")) or [work / "rosto_0.png"]
    saida = SAIDA / f"{job['id']}.mp4"
    try:
        largura, altura, fps, quadros = await preparar_clip(origem, clip, job["start"], job["seconds"])
        if quadros < 1:
            raise ValueError("o trecho do vídeo ficou vazio")
        job.update(width=largura, height=altura, fps=fps, length=quadros,
                   duration=round(quadros / fps, 1), status="running", stage="trocando o rosto")
        comando, ambiente = ff.comando_facefusion(rostos, clip, saida, job["quality"], GPU_TOKEN, temp=work / "tmp")
        proc = await asyncio.create_subprocess_exec(
            *comando, cwd=str(ff.FACEFUSION), env=ambiente,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        job["proc"] = proc
        linhas = []
        pendente = b""
        while True:
            pedaco = await proc.stdout.read(4096)
            if not pedaco:
                break
            pendente += pedaco
            partes = re.split(rb"[\r\n]", pendente)
            pendente = partes.pop()
            for parte in partes:
                linha = parte.decode("utf-8", "replace")
                achou = BARRA.search(linha)
                if achou:
                    inicio, fim, _rot = ETAPAS[achou.group(1)]
                    fracao = min(1.0, int(achou.group(2)) / max(1, int(achou.group(3))))
                    job["progress"] = round(max(job["progress"], inicio + (fim - inicio) * fracao), 3)
                    job["stage"] = ETAPAS[achou.group(1)][2]
                elif linha.strip():
                    linhas.append(linha)
        codigo = await proc.wait()
        if codigo == 0 and saida.exists():
            job.update(status="done", progress=1.0, stage="", url=f"/media/{saida.name}", finished=time.time())
        else:
            job.update(status="error", stage="", error=erro_amigavel(linhas))
    except Exception as exc:
        job.update(status="error", stage="", error=str(exc) or "não consegui trocar o rosto")
    finally:
        job.pop("proc", None)


def erro_amigavel(linhas):
    texto = "\n".join(linhas).lower()
    if "no source face" in texto:
        return "não encontrei um rosto na foto (de frente funciona melhor)"
    if "out of memory" in texto or "bad allocation" in texto or "dml" in texto and "alloc" in texto:
        return "faltou memória na GPU: tente a qualidade Boa, ou um trecho menor"
    return "o FaceFusion não conseguiu trocar o rosto neste vídeo"


# ---------------------------------------------------------------- rotas

async def status(_request):
    ocupado = any(j["status"] in ("preparing", "running") for j in JOBS.values())
    return web.json_response({
        "comfy": True, "gpu": f"GPU local · {GPU_ROTULO}", "vram": None, "workers": 1,
        "warming": 0, "held": 0, "focus": "rosto", "extras": None, "price_hour": 0,
        "idle": {"limit": 0, "busy": ocupado, "seconds": 0},
        "enhancer": {"state": "off", "detail": ""}, "models": MODELOS,
    })


async def get_presets(_request):
    return web.json_response(PRESETS)


async def generate(request):
    params, rostos, video = {}, 0, None
    work = TRABALHO / uuid.uuid4().hex[:10]
    work.mkdir(parents=True, exist_ok=True)
    try:
        reader = await request.multipart()
        async for part in reader:
            if part.name == "params":
                params = json.loads(await part.text())
            elif part.name == "start_image":  # uma ou várias fotos do mesmo rosto
                destino = work / f"rosto_{rostos}.png"
                rostos += 1
                with open(destino, "wb") as saida:
                    while chunk := await part.read_chunk():
                        saida.write(chunk)
            elif part.name == "video":
                video = work / "original.mp4"
                with open(video, "wb") as saida:
                    while chunk := await part.read_chunk():
                        saida.write(chunk)
    except Exception:
        return web.json_response({"error": "não consegui ler o envio"}, status=400)
    if params.get("mode") != "face" or params.get("target") != "video":
        return web.json_response({"error": "este servidor local só faz a troca de rosto em vídeo"}, status=400)
    if rostos == 0 or video is None:
        return web.json_response({"error": "envie a foto do rosto e o vídeo da pessoa que vai recebê-lo"}, status=400)
    quality = params.get("quality", "boa")
    if quality not in ff.QUALIDADES:
        quality = "boa"
    job = {
        "id": uuid.uuid4().hex[:10], "mode": "face", "kind": "video", "model": "facefusion",
        "quality": quality, "hair": False, "fast": True, "replace": True,
        "camera": "none", "effect": "none", "style": "none", "hd": False, "fluid": False,
        "prompt": "", "negative": "", "width": 0, "height": 0, "duration": 0, "fps": 0,
        "status": "preparing", "progress": 0.0, "stage": "preparando", "created": time.time(),
        "seed": int(params.get("seed") or 0) % 2**31,
        "seconds": max(1.0, min(60.0, float(params.get("duration", 10)))),
        "start": max(0.0, float(params.get("start", 0) or 0)), "work": str(work),
    }
    JOBS[job["id"]] = job
    asyncio.create_task(rodar_job(job))
    return web.json_response({"jobs": [publico(job)]})


async def list_jobs(_request):
    return web.json_response({"jobs": [publico(j) for j in JOBS.values()], "now": time.time()})


async def delete_job(request):
    job = JOBS.pop(request.match_info["id"], None)
    if job and job.get("proc"):
        try:
            job["proc"].kill()
        except ProcessLookupError:
            pass
    return web.json_response({"ok": True})


async def media(request):
    caminho = SAIDA / Path(request.match_info["name"]).name
    if not caminho.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(caminho, headers={"Content-Type": "video/mp4"})


async def touch(_request):
    return web.json_response({"ok": True})


async def shutdown(_request):
    asyncio.get_event_loop().call_later(0.3, lambda: sys.exit(0))
    return web.json_response({"ok": True})


async def index(_request):
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    config = f"<script>window.WAN_PRESETS = {json.dumps(PRESETS, ensure_ascii=False)};</script>\n  "
    html = html.replace('<script src="/static/app.js"></script>', config + '<script src="/static/app.js"></script>')
    return web.Response(text=html, content_type="text/html")


def main():
    if not ff.VENV_PYTHON.exists():
        print("O FaceFusion ainda não está instalado. Rode primeiro: python local/instalar.py")
        return
    for pasta in (SAIDA, TRABALHO):
        pasta.mkdir(parents=True, exist_ok=True)
    app = web.Application(client_max_size=600 * 1024 * 1024)  # vídeos de até ~600 MB
    app.router.add_get("/", index)
    app.router.add_static("/static/", STATIC)
    app.router.add_get("/api/status", status)
    app.router.add_get("/api/presets", get_presets)
    app.router.add_post("/api/generate", generate)
    app.router.add_get("/api/jobs", list_jobs)
    app.router.add_delete("/api/jobs/{id}", delete_job)
    app.router.add_get("/media/{name}", media)
    app.router.add_post("/api/touch", touch)
    app.router.add_post("/api/shutdown", shutdown)
    print(f"Wan Studio local no ar: http://127.0.0.1:{PORTA}")
    print(f"GPU: {GPU_ROTULO}")
    print("Feche esta janela para desligar.")
    web.run_app(app, host="127.0.0.1", port=PORTA, print=None)


if __name__ == "__main__":
    main()
