"""Roda os testes de ponta a ponta do Wan Studio, sem GPU.

  python tests/run_all.py

O que acontece:
  1. os atalhos "Ligar GPU" rodam contra APIs simuladas do Kaggle (free/ligar.py), do
     Lightning AI (lightning/ligar.py; precisa de um bash, no Windows o do Git) e do Modal
     (nuvem/ligar.py, com a sessao de verdade subindo aqui sobre um ComfyUI simulado);
  2. a emenda dos pedacos da troca de pessoa roda com o ffmpeg de verdade (stitch_test.py);
  3. o motor do servidor roda com dois ComfyUI simulados, pela API (engine_test.py);
  4. a pilha do notebook sobe localmente (launcher de verdade + dois ComfyUI simulados) e um
     Chrome sem janela percorre o site estatico: parear, conectar, gerar, trocar pessoa em
     video curto e longo, galeria, descoberta, desligar;
  5. o servidor sobe em modo "mesma origem" com um melhorador de prompt simulado e o Chrome
     testa melhorar/desfazer, erro, fila e cancelamento.

Precisa de: Python com aiohttp, cryptography e numpy; Node com playwright-core (`npm install
playwright-core` nesta pasta); Google Chrome; ffmpeg no PATH (gera o video de amostra).
A descoberta automatica usa o ntfy.sh de verdade, entao precisa de internet.
Rode `python free/build.py` antes, se tiver alterado o codigo.
"""
import base64
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
WORK = HERE / ".work"
PORTS = (8188, 8189, 8080, 8092, 8190, 8191, 8093, 8192, 8094, 8195, 8196, 8097)


def sample_media():
    WORK.mkdir(exist_ok=True)
    jobs = {
        "sample.mp4": ["-f", "lavfi", "-i", "testsrc2=size=832x480:rate=24:duration=3", "-c:v", "libx264",
                       "-pix_fmt", "yuv420p", "-movflags", "+faststart"],
        # Verde liso: a "saida gerada" dos simuladores, facil de distinguir do video enviado.
        "sample.png": ["-f", "lavfi", "-i", "color=c=0x30b060:size=832x480:duration=1", "-frames:v", "1"],
        "long.mp4": ["-f", "lavfi", "-i", "testsrc2=size=832x480:rate=24:duration=11", "-f", "lavfi", "-i",
                     "sine=frequency=330:duration=11", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac"],
        "start.png": ["-f", "lavfi", "-i", "color=c=0x2a6fb0:size=1280x720:duration=1", "-frames:v", "1"],
    }
    for name, args in jobs.items():
        if not (WORK / name).exists():
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args, str(WORK / name)], check=True)


def spawn(script, log, args=(), **env):
    handle = open(WORK / log, "w", encoding="utf-8")
    full_env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8", **env}
    return subprocess.Popen([sys.executable, str(HERE / script), *args], stdout=handle,
                            stderr=subprocess.STDOUT, env=full_env)


def wait_for(check, seconds, what):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if check():
            return
        time.sleep(1)
    raise SystemExit(f"tempo esgotado esperando {what}; veja os logs em {WORK}")


def responds(url):
    try:
        urllib.request.urlopen(url, timeout=3).close()
        return True
    except urllib.error.HTTPError:
        return True  # 401 tambem significa que esta no ar
    except OSError:
        return False


def kill_tree(proc):
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.terminate()


def stop_stack(proc):
    """Pede o desligamento pela propria API (o launcher encerra os filhos); se nao der, mata."""
    if proc.poll() is not None:
        return
    try:
        code = (WORK / "code.txt").read_text(encoding="utf-8")
        session = json.loads(base64.urlsafe_b64decode(code + "=" * (-len(code) % 4)))
        token = base64.b64encode(f"wan:{session['p']}".encode()).decode()
        request = urllib.request.Request("http://127.0.0.1:8080/api/shutdown", method="POST",
                                         headers={"Authorization": f"Basic {token}"})
        urllib.request.urlopen(request, timeout=5).close()
        proc.wait(timeout=20)
    except Exception:
        kill_tree(proc)


def node(script):
    return subprocess.run(["node", str(HERE / script)]).returncode


def main():
    busy = [port for port in PORTS if responds(f"http://127.0.0.1:{port}/")]
    if busy:
        raise SystemExit(f"portas em uso: {busy}. Feche o que estiver usando e tente de novo.")
    sample_media()
    results, stack, others = {}, None, []
    # O atalho "Ligar GPU", contra uma API do Kaggle simulada (nao precisa dos outros servidores).
    results["ligar a GPU"] = subprocess.run([sys.executable, str(HERE / "ligar_test.py")]).returncode
    # O mesmo no Lightning AI: API simulada, com os comandos remotos rodando num bash daqui.
    results["ligar no Lightning"] = subprocess.run([sys.executable, str(HERE / "lightning_test.py")]).returncode
    # A GPU rapida (Modal): um modal de mentira, e a sessao de verdade com o ComfyUI simulado.
    results["ligar no Modal"] = subprocess.run([sys.executable, str(HERE / "nuvem_test.py")]).returncode
    # A emenda dos pedacos (ffmpeg) e o motor com duas GPUs simuladas, pela API.
    results["emenda"] = subprocess.run([sys.executable, str(HERE / "stitch_test.py")]).returncode
    # O fechamento do recorte da pessoa (a parte do no do ComfyUI que e so conta; precisa do numpy).
    results["recorte"] = subprocess.run([sys.executable, str(HERE / "mask_test.py")]).returncode
    results["motor"] = subprocess.run([sys.executable, str(HERE / "engine_test.py")]).returncode
    # A producao em massa (pasta de videos -> pasta de resultados), contra o servidor de verdade.
    results["producao em massa"] = subprocess.run([sys.executable, str(HERE / "producao_test.py")]).returncode
    try:
        (WORK / "code.txt").unlink(missing_ok=True)
        stack = spawn("run_stack.py", "stack.log")
        wait_for(lambda: (WORK / "code.txt").exists(), 120, "a pilha do notebook")
        results["site estatico"] = node("site_test.js")
        stop_stack(stack)

        # Aqui a "placa" e grande (uma L40S de 45 GB), para o site mostrar a qualidade ultra.
        others.append(spawn("fake_comfy.py", "fake2.log", args=["--port", "8189"], FAKE_ASSETS=str(WORK),
                            FAKE_SETS="14b_i2v,14b_t2v,animate,animate_fp8", FAKE_STEP="0.5",
                            FAKE_GPU="NVIDIA L40S", FAKE_VRAM_GB="45"))
        # Sem o que uma rodada anterior deixou: um job interrompido la apareceria aqui como erro.
        shutil.rmtree(WORK / "data_srv", ignore_errors=True)
        logs = WORK / "logs_srv"  # um registro maior que o limite de /api/log
        logs.mkdir(exist_ok=True)
        (logs / "comfyui.log").write_text("inicio\n" + "x" * 300_000 + "\nPrompt executed in 12.34 seconds\n",
                                          encoding="utf-8", newline="\n")
        others.append(spawn("run_server_enhancer.py", "server2.log", PORT="8092", LOG_DIR=str(logs), WARM_UP="0",
                            COMFY_URL="http://127.0.0.1:8189", DATA_DIR=str(WORK / "data_srv")))
        wait_for(lambda: responds("http://127.0.0.1:8092/api/presets"), 60, "o servidor de teste")
        results["modo servidor"] = node("server_test.js")
    finally:
        if stack is not None:
            stop_stack(stack)
        for proc in others:
            kill_tree(proc)
    print()
    for name, code in results.items():
        print(f"{name}: {'ok' if code == 0 else 'FALHOU'}")
    return 1 if any(results.values()) or len(results) < 9 else 0


if __name__ == "__main__":
    sys.exit(main())
