"""Testa a producao em massa (free/producao.py) contra o servidor de verdade com dois ComfyUI
simulados, sem Kaggle: o script recebe o endereco do estudio por WAN_SESSION.

  python tests/producao_test.py

Os videos de entrada sao de verdade (ffmpeg), o encurtamento antes do envio e a conversao para
o formato do Instagram tambem. Precisa de Python com aiohttp e de ffmpeg no PATH.
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
WORK = HERE / ".work" / "producao"
BASE = WORK / "producao"
SERVER = "http://127.0.0.1:8097"
FAKES = ("http://127.0.0.1:8195", "http://127.0.0.1:8196")
failures = []


def check(label, condition, detail=""):
    print(("ok    " if condition else "FALHA ") + label + (f"  [{detail}]" if detail else ""), flush=True)
    if not condition:
        failures.append(label)


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *map(str, args)], check=True)


def get(path, base=SERVER):
    with urllib.request.urlopen(base + path, timeout=30) as response:
        return json.loads(response.read())


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,r_frame_rate",
                          "-of", "json", str(path)], capture_output=True, text=True).stdout
    return json.loads(out or "{}").get("streams", [])


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
            urllib.request.urlopen(url, timeout=3).close()
            return
        except urllib.error.HTTPError:
            return
        except OSError:
            time.sleep(0.5)
    raise SystemExit(f"{url} não respondeu; veja os logs em {WORK}")


def tool_env():
    return {**os.environ, "WAN_SESSION": SERVER + "|", "WAN_PRODUCAO_DIR": str(BASE), "WAN_POLL_SECONDS": "0.5",
            "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}


def tool(*args):
    result = subprocess.run([sys.executable, str(ROOT / "free" / "producao.py"), *args], env=tool_env(),
                            capture_output=True, text=True, encoding="utf-8", timeout=600)
    return result.returncode, result.stdout + result.stderr


def clip(name, size, seconds):
    ffmpeg("-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30:duration={seconds}", "-f", "lavfi", "-i",
           f"sine=frequency=330:duration={seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
           BASE / "videos" / name)


def graphs(skip=()):
    """Os grafos que os ComfyUI simulados receberam, menos os de `skip` (ids)."""
    return [list(graph.values()) for base in FAKES for key, graph in get("/_graphs", base).items() if key not in skip]


def graph_ids():
    return {key for base in FAKES for key in get("/_graphs", base)}


def last(out, count=1):
    return " | ".join(line.strip() for line in out.strip().splitlines()[-count:])[:170]


def scenarios():
    # ---- 1. pasta vazia: cria as pastas e explica o que por nelas
    code, out = tool()
    check("M1 sem nada nas pastas: cria tudo e explica", code == 1 and all((BASE / name).is_dir() for name in ("pessoa", "videos", "prontos", "usados"))
          and (BASE / "opcoes.txt").exists() and (BASE / "LEIA-ME.txt").exists() and "Ponha a imagem da pessoa em" in out, last(out, 2))

    # ---- 2. tres videos (um ilegivel), em pe e com som, com alta definicao e movimento fluido
    ffmpeg("-f", "lavfi", "-i", "color=c=0x2a6fb0:size=640x640:duration=1", "-frames:v", "1", BASE / "pessoa" / "minha pessoa.png")
    clip("dança 1.mp4", "1080x1920", 4)
    clip("passeio.mp4", "720x1280", 6)
    (BASE / "videos" / "quebrado.mp4").write_bytes(b"isto nao e um video")
    (BASE / "opcoes.txt").write_text("qualidade = rapida\nsegundos = 3\ncomecar_em = 2\nalta_definicao = sim\n"
                                     "fluido = sim\ninstagram = sim\ndesligar_no_fim = nao\n", encoding="utf-8")
    code, out = tool()
    made = sorted(path.name for path in (BASE / "prontos").iterdir())
    check("M2 produz os dois videos bons e segue apesar do ilegivel", code == 0 and made == ["dança 1.mp4", "passeio.mp4"]
          and "Fim: 2 vídeo(s)" in out and "1 com erro" in out, f"{made} | {last(out, 2)}")
    streams = {name: probe(BASE / "prontos" / name) for name in made}
    video = {name: next((s for s in found if s["codec_type"] == "video"), {}) for name, found in streams.items()}
    check("M3 formato do Instagram: 1080x1920, 30 quadros por segundo, com o som",
          bool(video) and all((item.get("width"), item.get("height"), item.get("r_frame_rate")) == (1080, 1920, "30/1") for item in video.values())
          and all(any(s["codec_type"] == "audio" for s in found) for found in streams.values()),
          str({name: (item.get("width"), item.get("height"), item.get("r_frame_rate")) for name, item in video.items()}))
    check("M4 os videos feitos vao para 'usados'; o ilegivel fica, com o motivo anotado",
          sorted(path.name for path in (BASE / "usados").iterdir()) == ["dança 1.mp4", "passeio.mp4"]
          and [path.name for path in (BASE / "videos").iterdir()] == ["quebrado.mp4"]
          and "quebrado.mp4" in (BASE / "erros.txt").read_text(encoding="utf-8") and not (BASE / ".andamento.json").exists(),
          (BASE / "erros.txt").read_text(encoding="utf-8").strip()[:120])
    sent = graphs()
    animate = [next(item["inputs"] for item in graph if item["class_type"] == "WanAnimateToVideo") for graph in sent
               if any(item["class_type"] == "WanAnimateToVideo" for item in graph)]
    restored = [graph for graph in sent if any(item["class_type"] == "SeedVR2Conditioning" for item in graph)]
    # Pulando 2 s do comeco, o video de 4 s so tem 2 s para dar (29 quadros); o de 6 s da os 3 pedidos.
    check("M5 o estudio recebeu o pedido certo: 3 s a partir do segundo 2, em pe, restaurado e com os quadros a mais",
          sorted((item["width"], item["height"], item["length"]) for item in animate) == [(368, 640, 29), (368, 640, 45)]
          and len(restored) == 2 and all(any(item["class_type"] == "FrameInterpolate" and item["inputs"]["multiplier"] == 2
                                             for item in graph) for graph in restored),
          f"{[(item['width'], item['height'], item['length']) for item in animate]}; restaurados: {len(restored)}")
    status = get("/api/status")
    check("M6 nada fica no estudio, e com 'desligar_no_fim = nao' a GPU continua ligada",
          not get("/api/jobs?hold=1")["jobs"] and not get("/api/jobs")["jobs"] and status["shutdown"] is False
          and not list((WORK / "data" / "uploads").iterdir()), f"shutdown={status['shutdown']}")

    # ---- 3. interrompido no meio: o site nao enxerga o video da producao, e rodar de novo retoma
    (BASE / "videos" / "quebrado.mp4").unlink()
    clip("retomar.mp4", "720x1280", 4)
    before = graph_ids()  # nao da para contar pela posicao: os grafos novos da primeira GPU entram no meio da lista
    proc = subprocess.Popen([sys.executable, str(ROOT / "free" / "producao.py")], env=tool_env(), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8")
    seen = ""
    deadline = time.time() + 120
    while time.time() < deadline and "enviado: retomar.mp4" not in seen:
        seen += proc.stdout.readline()
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.kill()
    proc.wait(timeout=30)
    held, visible = get("/api/jobs?hold=1")["jobs"], get("/api/jobs")["jobs"]
    check("M7 o video da producao nao aparece para o site (que o levaria para a galeria do navegador)",
          len(held) == 1 and held[0].get("hold") is True and not visible and get("/api/status").get("held") in (0, 1),
          f"producao: {len(held)}; site: {len(visible)}")
    (BASE / "opcoes.txt").write_text("qualidade = rapida\nsegundos = 3\nalta_definicao = nao\nfluido = nao\n"
                                     "instagram = nao\ndesligar_no_fim = sim\n", encoding="utf-8")
    code, out = tool()
    new = [graph for graph in graphs(before) if any(item["class_type"] == "WanAnimateToVideo" for item in graph)]
    result = next((s for s in probe(BASE / "prontos" / "retomar.mp4") if s["codec_type"] == "video"), {})
    # O video foi pedido na primeira rodada, com a alta definicao; a segunda so o busca (e, com
    # "instagram = nao", entrega como o estudio fez: 720 de largura).
    check("M8 rodar de novo retoma o mesmo video, sem enviar outra vez, e entrega como o estudio fez",
          code == 0 and "enviado: retomar.mp4" not in out and "✓ retomar.mp4" in out and len(new) == 1
          and (result.get("width"), result.get("height")) == (720, 1252), f"{len(new)} envio(s); {result.get('width')}x{result.get('height')} | {last(out)}")
    check("M9 com 'desligar_no_fim = sim' pede o desligamento no fim", "Desligando a GPU" in out and get("/api/status")["shutdown"] is True,
          last(out))


def main():
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=416x240:rate=1:duration=1", "-frames:v", "1", WORK / "sample.png")
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", WORK / "sample.mp4")
    procs = [spawn("fake_comfy.py", f"fake{index}.log", "--port", base.rsplit(":", 1)[1], FAKE_ASSETS=str(WORK),
                   FAKE_SETS="animate", FAKE_STEP="0.1") for index, base in enumerate(FAKES)]
    procs.append(spawn("run_server_enhancer.py", "server.log", PORT=SERVER.rsplit(":", 1)[1], COMFY_URLS=",".join(FAKES),
                       ENHANCER_WORKER="-1", WARM_UP="0", WAN_MANAGED="1", DATA_DIR=str(WORK / "data"), LOG_DIR=str(WORK / "logs")))
    try:
        for base in FAKES:
            wait_for(base + "/queue")
        wait_for(SERVER + "/api/presets")
        scenarios()
    finally:
        for proc in procs:
            if os.name == "nt":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
            else:
                proc.terminate()
    print(f"\n{len(failures)} falha(s)" + (": " + "; ".join(failures) if failures else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
