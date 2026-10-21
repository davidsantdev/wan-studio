"""Troca o rosto de um vídeo pelo rosto de uma foto, aqui no seu PC, com o FaceFusion.

Uso (duplo clique no "Trocar rosto em video.bat", ou pela linha de comando):

  python trocar_rosto_video.py                      usa a pasta "entrada" (rosto.* e o vídeo)
  python trocar_rosto_video.py ROSTO.png VIDEO.mp4  usa esses dois arquivos
  python trocar_rosto_video.py ... --qualidade maxima   mais detalhe (bem mais lento)

A foto é o rosto que entra; o vídeo é a pessoa que recebe o rosto (o corpo, a roupa, o
movimento e o som continuam os do vídeo). O resultado fica na pasta "saida".

Antes da primeira vez, rode `python local/instalar.py` (baixa o FaceFusion e os modelos, uns
2,5 GB, para a pasta `facefusion-local`, ao lado do projeto).

Use apenas imagens e vídeos de pessoas que autorizaram o uso.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent          # <repo>/local
REPO = AQUI.parent                              # <repo>
# O FaceFusion, o venv e os modelos são pesados e não entram no git: ficam na pasta
# `facefusion-local`, ao lado do projeto por padrão (WAN_FACE_LOCAL muda o lugar). `local/instalar.py`
# a cria.
INSTALL = Path(os.environ.get("WAN_FACE_LOCAL") or REPO.parent / "facefusion-local")
FACEFUSION = INSTALL / "facefusion"
VENV_PYTHON = INSTALL / "venv" / "Scripts" / "python.exe"
BIN = INSTALL / "bin"
ENTRADA = INSTALL / "entrada"
SAIDA = INSTALL / "saida"
# As DLLs do CUDA/cuDNN vêm nos pacotes nvidia-*-cu12 (quando instalados): o onnxruntime-gpu só
# acha a placa NVIDIA com estas pastas no PATH. (O padrão é DirectML, que não precisa delas.)
NVIDIA = INSTALL / "venv" / "Lib" / "site-packages" / "nvidia"
CUDA_BINS = [p for p in (NVIDIA / "cudnn" / "bin", NVIDIA / "cublas" / "bin",
                         NVIDIA / "cuda_runtime" / "bin") if p.is_dir()]
IMAGENS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
VIDEOS = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v")

# Cada qualidade: em quantos pedaços o rosto é refeito (maior = mais nítido e mais lento), qual
# melhorador de nitidez ("nenhum" pula esse passo e corre bem mais) e se leva a expressão da
# pessoa do vídeo para o rosto novo (mais natural). Medido numa GTX 1050 Ti (clipe de 2 s): o passo
# do melhorador quase dobra o tempo, então "expresso" (sem ele) fica ~3x mais rápido que "boa".
QUALIDADES = {
    "expresso": {"boost": "256x256", "enhancer": "nenhum", "expressao": False},
    "rapida": {"boost": "256x256", "enhancer": "gfpgan_1.4", "expressao": False},
    "boa": {"boost": "512x512", "enhancer": "gfpgan_1.4", "expressao": False},
    "maxima": {"boost": "1024x1024", "enhancer": "codeformer", "expressao": True},
}
# Medido: numa GTX 1050 Ti, 4 threads renderam mais que 8 (menos disputa por memória).
THREADS = 4

# GPU, na ordem de preferência: (nome do provider no onnxruntime, token do FaceFusion, rótulo).
# DirectML usa qualquer placa com DirectX 12 (AMD, Intel ou NVIDIA) e não precisa de CUDA.
GPUS = [("DmlExecutionProvider", "directml", "GPU (DirectML)"),
        ("CUDAExecutionProvider", "cuda", "GPU (NVIDIA/CUDA)")]


def detectar_gpu():
    """O melhor jeito de usar uma GPU que de fato inicializa aqui, como (token do FaceFusion,
    rótulo); None se só a CPU funcionar. O provider pode aparecer na lista e mesmo assim falhar
    (placa velha, driver antigo), então testa numa sessão de verdade."""
    for pasta in CUDA_BINS:  # as DLLs do CUDA, se o onnxruntime-gpu estiver instalado
        try:
            os.add_dll_directory(str(pasta))
        except OSError:
            pass
    try:
        import numpy as np
        import onnxruntime
        from onnx import TensorProto, helper
        disponiveis = onnxruntime.get_available_providers()
        node = helper.make_node("Add", ["a", "b"], ["c"])
        info = [helper.make_tensor_value_info(n, TensorProto.FLOAT, [1]) for n in ("a", "b", "c")]
        model = helper.make_model(helper.make_graph([node], "g", info[:2], info[2:]),
                                  opset_imports=[helper.make_opsetid("", 18)])
        model.ir_version = 10
        for provider, token, rotulo in GPUS:
            if provider not in disponiveis:
                continue
            try:
                session = onnxruntime.InferenceSession(model.SerializeToString(), providers=[provider])
                if provider in session.get_providers():
                    session.run(None, {"a": np.array([1.0], np.float32), "b": np.array([2.0], np.float32)})
                    return token, rotulo
            except Exception:
                continue
    except Exception:
        pass
    return None


# A parte do rosto que a máscara segue: a pele e os traços, sem a testa/cabelo nem o fundo. Com
# ela (mais a oclusão), a troca mistura no contorno do rosto, sem a emenda retangular da caixa.
REGIOES = ("skin", "left-eyebrow", "right-eyebrow", "left-eye", "right-eye", "nose",
           "mouth", "upper-lip", "lower-lip")


def comando_facefusion(rostos, video, saida, qualidade, token, temp=None, threads=None):
    """O comando do FaceFusion para trocar o rosto de `rostos` (um caminho, ou vários do mesmo
    rosto — mais fotos melhoram a semelhança) na pessoa de `video`, na `qualidade` (QUALIDADES),
    usando `token` como aceleração (directml/cuda/None; None = CPU); e o ambiente com as DLLs no
    PATH. A máscara segue o contorno do rosto (box + oclusão + região), com o detector yolo_face,
    o marcador 2dfan4 e o melhorador a 80% — o que dá o acabamento mais natural."""
    fontes = [rostos] if isinstance(rostos, (str, bytes, os.PathLike)) else list(rostos)
    preset = QUALIDADES[qualidade]
    melhora = preset["enhancer"] != "nenhum"
    processadores = ["face_swapper"] + (["expression_restorer"] if preset["expressao"] else []) \
        + (["face_enhancer"] if melhora else [])
    comando = [
        str(VENV_PYTHON), "facefusion.py", "headless-run",
        "-s", *[str(Path(f).resolve()) for f in fontes], "-t", str(Path(video).resolve()),
        "-o", str(Path(saida).resolve()), "--processors", *processadores,
        "--face-swapper-model", "hyperswap_1a_256", "--face-swapper-pixel-boost", preset["boost"],
        "--face-selector-mode", "one",
        "--face-detector-model", "yolo_face", "--face-detector-score", "0.5", "--face-landmarker-model", "2dfan4",
        "--face-mask-types", "box", "occlusion", "region", "--face-mask-regions", *REGIOES, "--face-mask-blur", "0.3",
        "--output-video-quality", "95",
        "--execution-providers", *([token, "cpu"] if token else ["cpu"]),
        "--execution-thread-count", str(threads or THREADS), "--log-level", "info",
    ]
    if melhora:
        comando += ["--face-enhancer-model", preset["enhancer"], "--face-enhancer-blend", "80"]
    if temp:
        comando += ["--temp-path", str(Path(temp).resolve())]
    ambiente = dict(os.environ)
    extra_path = [str(BIN)] + [str(p) for p in CUDA_BINS]
    ambiente["PATH"] = os.pathsep.join(extra_path) + os.pathsep + ambiente.get("PATH", "")
    return comando, ambiente


def achar(pasta, extensoes):
    return next((p for p in sorted(pasta.iterdir()) if p.suffix.lower() in extensoes), None) if pasta.is_dir() else None


def todas(pasta, extensoes):
    return [p for p in sorted(pasta.iterdir()) if p.suffix.lower() in extensoes] if pasta.is_dir() else []


def resolver_entradas(args):
    """(lista de fotos do rosto, vídeo). Várias fotos do mesmo rosto melhoram a semelhança: valem
    todas as imagens dos argumentos, ou todas as que estiverem na pasta entrada."""
    rostos = [Path(v) for v in args.arquivos if Path(v).suffix.lower() in IMAGENS]
    videos = [Path(v) for v in args.arquivos if Path(v).suffix.lower() in VIDEOS]
    if not rostos:
        rostos = todas(ENTRADA, IMAGENS)
    video = videos[0] if videos else achar(ENTRADA, VIDEOS)
    return rostos, video


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("arquivos", nargs="*", help="a foto do rosto e o vídeo (ou deixe vazio e use a pasta entrada)")
    parser.add_argument("--qualidade", choices=list(QUALIDADES), default=None,
                        help="expresso, rapida, boa (padrão na GPU) ou maxima")
    parser.add_argument("--cpu", action="store_true", help="força a CPU mesmo com GPU disponível")
    args = parser.parse_args()

    if not VENV_PYTHON.exists():
        print("O FaceFusion ainda não está instalado neste PC.")
        print("  Rode primeiro:  python local/instalar.py")
        return 2
    ENTRADA.mkdir(parents=True, exist_ok=True)
    SAIDA.mkdir(parents=True, exist_ok=True)
    rostos, video = resolver_entradas(args)
    if not rostos or video is None:
        print("Faltou a foto do rosto ou o vídeo.")
        print(f"  Ponha uma (ou várias) foto do rosto e um vídeo na pasta:\n    {ENTRADA}")
        print("  Ou arraste tudo para cima do 'Trocar rosto em video.bat'.")
        return 2

    gpu = None if args.cpu else detectar_gpu()
    token, rotulo = gpu if gpu else (None, "CPU (sem GPU; mais lento)")
    # Na GPU o padrão é "boa" (512); "maxima" (1024) pode não caber numa placa de 4 GB. Na CPU,
    # "rapida" (256), que é o único jeito de não levar uma eternidade.
    qualidade = args.qualidade or ("boa" if gpu else "rapida")
    saida = SAIDA / f"{video.stem}_com_rosto_de_{rostos[0].stem}{video.suffix.lower()}"
    comando, ambiente = comando_facefusion(rostos, video, saida, qualidade, token)
    print(f"Rosto:  {', '.join(r.name for r in rostos)}")
    print(f"Vídeo:  {video.name}")
    print(f"Onde:   {rotulo}")
    print(f"Qualidade: {qualidade}")
    print("Trocando o rosto… (não feche esta janela)\n")
    codigo = subprocess.run(comando, cwd=str(FACEFUSION), env=ambiente).returncode
    if codigo == 0 and saida.exists():
        print(f"\nPronto! O vídeo está em:\n    {saida}")
        try:
            os.startfile(saida)  # abre o vídeo
        except OSError:
            pass
        return 0
    print("\nNão deu certo. Confira se há um rosto de frente na foto e um rosto no vídeo.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
