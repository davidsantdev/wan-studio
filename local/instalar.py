"""Instala a troca de rosto em vídeo (FaceFusion) neste PC, uma vez.

  python local/instalar.py

Cria a pasta `facefusion-local` ao lado do projeto (uns 2,5 GB: o FaceFusion, um Python próprio,
os modelos e o ffmpeg) e deixa tudo pronto para o 'Trocar rosto em video.bat' e o 'Abrir Wan
Studio local.bat'. Pode rodar de novo: o que já existe não é baixado outra vez. Precisa de `git`.

A GPU é usada pelo DirectML (qualquer placa com DirectX 12 — AMD, Intel ou NVIDIA); sem placa,
roda na CPU (bem mais lento). WAN_FACE_LOCAL muda onde a pasta é criada.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
REPO = AQUI.parent
INSTALL = Path(os.environ.get("WAN_FACE_LOCAL") or REPO.parent / "facefusion-local")
FACEFUSION = INSTALL / "facefusion"
VENV = INSTALL / "venv"
VENV_PY = VENV / "Scripts" / "python.exe"
BIN = INSTALL / "bin"
MODELOS = FACEFUSION / ".assets" / "models"
FF_REF = "72470819a0373be3388b3929c8f8f311f418fc3c"  # FaceFusion 3.9.1
DEPS = ["onnxruntime-directml", "aiohttp", "opencv-python-headless", "numpy", "onnx", "tqdm",
        "scipy", "static-ffmpeg"]


def passo(msg):
    print(f"\n== {msg}", flush=True)


def rodar(cmd, **kw):
    print("  " + " ".join(str(c) for c in cmd))
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def main():
    if not shutil.which("git"):
        print("Preciso do git instalado (https://git-scm.com). Instale e rode de novo.")
        return 1
    INSTALL.mkdir(parents=True, exist_ok=True)

    passo("baixando o FaceFusion")
    if not (FACEFUSION / "facefusion.py").exists():
        rodar(["git", "init", "-q", str(FACEFUSION)])
        rodar(["git", "-C", str(FACEFUSION), "remote", "add", "origin", "https://github.com/facefusion/facefusion"])
        rodar(["git", "-C", str(FACEFUSION), "fetch", "-q", "--depth", "1", "origin", FF_REF])
        rodar(["git", "-C", str(FACEFUSION), "checkout", "-q", "FETCH_HEAD"])
    else:
        print("  já está aqui.")

    passo("criando o Python próprio (venv)")
    if not VENV_PY.exists():
        rodar([sys.executable, "-m", "venv", str(VENV)])
    else:
        print("  já existe.")

    passo("instalando as dependências (pode demorar na primeira vez)")
    rodar([str(VENV_PY), "-m", "pip", "install", "-q", "--disable-pip-version-check", *DEPS])

    passo("preparando o ffmpeg")
    BIN.mkdir(exist_ok=True)
    if not (BIN / "ffmpeg.exe").exists():
        rodar([str(VENV_PY), "-c", "import static_ffmpeg; static_ffmpeg.add_paths()"])
        base = VENV / "Lib" / "site-packages" / "static_ffmpeg" / "bin" / "win32"
        for exe in ("ffmpeg.exe", "ffprobe.exe"):
            if (base / exe).exists():
                shutil.copy2(base / exe, BIN / exe)
    print("  ffmpeg pronto." if (BIN / "ffmpeg.exe").exists() else "  (aviso: ffmpeg não copiou)")

    passo("baixando os modelos do FaceFusion (uns 2 GB; só na primeira vez)")
    if not (MODELOS / "hyperswap_1a_256.onnx").exists():
        vazio = INSTALL / "vazio.png"
        rodar([str(VENV_PY), "-c",
               "import numpy, cv2, sys; cv2.imwrite(sys.argv[1], numpy.full((256, 256, 3), 128, numpy.uint8))",
               str(vazio)])
        ambiente = {**os.environ, "PATH": str(BIN) + os.pathsep + os.environ.get("PATH", "")}
        # O FaceFusion baixa os modelos antes de procurar um rosto; numa foto cinza ele baixa tudo
        # e para (sem rosto). Isso deixa os modelos no lugar.
        subprocess.run([str(VENV_PY), "facefusion.py", "headless-run", "-s", str(vazio), "-t", str(vazio),
                        "-o", str(INSTALL / "teste.png"), "--processors", "face_swapper", "face_enhancer",
                        "--face-swapper-model", "hyperswap_1a_256", "--face-enhancer-model", "gfpgan_1.4",
                        "--execution-providers", "cpu", "--log-level", "error"],
                       cwd=str(FACEFUSION), env=ambiente)
        vazio.unlink(missing_ok=True)
        (INSTALL / "teste.png").unlink(missing_ok=True)
    else:
        print("  já estão aqui.")

    (INSTALL / "entrada").mkdir(exist_ok=True)
    (INSTALL / "saida").mkdir(exist_ok=True)
    pronto = (MODELOS / "hyperswap_1a_256.onnx").exists() and VENV_PY.exists() and (BIN / "ffmpeg.exe").exists()
    passo("tudo pronto!" if pronto else "quase lá — se a troca falhar, rode este instalador de novo")
    print(f"  Instalado em: {INSTALL}")
    print("  Agora é só usar o 'Trocar rosto em video.bat' ou o 'Abrir Wan Studio local.bat'.")
    return 0 if pronto else 1


if __name__ == "__main__":
    sys.exit(main())
