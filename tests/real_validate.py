"""Valida os grafos do Wan Studio contra um ComfyUI de verdade.

  python tests/real_validate.py [http://127.0.0.1:8188]

O ComfyUI confere tipos de no, entradas obrigatorias, ligacoes e valores de lista antes de
enfileirar, entao basta que os arquivos de modelo existam com os nomes certos: podem ate ser
arquivos vazios (a execucao falha ao abri-los, mas a validacao ja aconteceu). Rode isto ao
trocar a versao do ComfyUI ou dos pacotes extras em vm/setup.sh.

Precisa de `requests` e `Pillow`; a troca de pessoa tambem usa o ffmpeg (video de amostra).
"""
import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import requests
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import presets  # noqa: E402
import workflows  # noqa: E402

COMFY = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8188"
LISTS = {
    "unet": ("UNETLoader", "unet_name"), "gguf": ("UnetLoaderGGUF", "unet_name"),
    "lora": ("LoraLoaderModelOnly", "lora_name"), "vae": ("VAELoader", "vae_name"),
    "clip": ("CLIPLoader", "clip_name"), "clip_vision": ("CLIPVisionLoader", "clip_name"),
    "detection": ("OnnxDetectionModelLoader", "vitpose_model"),
    "upscale": ("UpscaleModelLoader", "model_name"),
    "frame_interpolation": ("FrameInterpolationModelLoader", "model_name"),
}
CASES = [
    ("5b", "t2v", True), ("5b", "t2v", False), ("5b", "i2v", True), ("5b", "i2v", False),
    ("5b", "img", True), ("14b", "t2v", True), ("14b", "t2v", False), ("14b", "i2v", True),
    ("14b", "i2v", False), ("14b", "flf", True), ("14b", "flf", False), ("14b", "img", True),
]
# Troca de pessoa: (quadros, manter o cenario, turbo). 149 quadros = dois blocos emendados.
# Um pedaco da troca de pessoa: (quadros, mantem o cenario, qualidade, continua de um vizinho).
SWAP_CASES = [(49, True, "boa", False), (21, True, "maxima", False), (49, True, "rapida", True),
              (33, False, "boa", True)]


def info(node):
    return requests.get(f"{COMFY}/object_info/{node}", timeout=120).json()


def options(node, field):
    try:
        spec = info(node)[node]["input"]["required"][field]
    except KeyError:
        return []
    if spec and isinstance(spec[0], list):
        return spec[0]
    if len(spec) > 1 and isinstance(spec[1], dict):
        return spec[1].get("options", [])
    return []


def upload(name, data, mime):
    reply = requests.post(f"{COMFY}/upload/image", files={"image": (name, data, mime)},
                          data={"overwrite": "true"}, timeout=120)
    reply.raise_for_status()
    return reply.json()["name"]


def sample_image():
    buffer = io.BytesIO()
    Image.new("RGB", (640, 360), (40, 120, 200)).save(buffer, "PNG")
    return buffer.getvalue()


def sample_video(frames):
    """Video de teste no formato que o servidor entrega ao ComfyUI: 832x480, 16 fps."""
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "amostra.mp4"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=size=832x480:rate=16", "-frames:v", str(frames), "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", str(path)], check=True)
        return path.read_bytes()


def validate(label, graph):
    reply = requests.post(f"{COMFY}/prompt", json={"prompt": graph, "client_id": "validate"}, timeout=300)
    body = reply.json()
    requests.post(f"{COMFY}/queue", json={"clear": True}, timeout=60)
    requests.post(f"{COMFY}/interrupt", json={}, timeout=60)
    if reply.status_code == 200 and not body.get("node_errors"):
        print(f"VALIDO    {label} ({len(graph)} nos)")
        return 0
    print(f"REJEITADO {label} -> {json.dumps(body)[:900]}")
    return 1


def main():
    installed = {key: options(*spec) for key, spec in LISTS.items()}
    wanted = {node for entry in workflows.CATALOG.values() for node in entry.get("nodes", ())}
    installed["nodes"] = [node for node in sorted(wanted) if node in info(node)]
    sets = workflows.available(installed)
    for name, found in sets.items():
        print(f"conjunto {name}: instalado={found['ok']} turbo={found['turbo']}")

    positive, negative = presets.compose_prompt("teste", "orbit", "fire", "noir"), presets.compose_negative("texto")
    start, end = upload("wanstudio_test_start.png", sample_image(), "image/png"), upload(
        "wanstudio_test_end.png", sample_image(), "image/png")
    failures = 0
    for model, mode, fast in CASES:
        found = sets[workflows.required_set(model, mode)]
        label = f"{model:>3} {mode} {'turbo' if fast else 'normal'}"
        if not found["ok"] or (fast and not found["turbo"]):
            print(f"PULADO    {label} (modelos ausentes)")
            continue
        spec = workflows.resolve({"mode": mode, "model": model, "prompt": "teste", "fast": fast}, True, True)
        images = {"i2v": {"start_image": start}, "flf": {"start_image": start, "end_image": end}}.get(mode, {})
        graph, _, _ = workflows.build(spec, found["files"], positive, negative, f"val_{model}_{mode}", **images)
        failures += validate(label, graph)

    animate = sets["animate"]
    for frames, replace, quality, continued in SWAP_CASES:
        label = (f"troca {frames:>3} quadros {'com cenario' if replace else 'so movimento'} {quality}"
                 f"{' continuando' if continued else ''}")
        if not animate["turbo"]:
            print(f"PULADO    {label} (Animate ou pre-processadores ausentes)")
            continue
        video = upload(f"wanstudio_test_{frames}.mp4", sample_video(frames), "video/mp4")
        motion = upload("wanstudio_test_continua.mp4", sample_video(5), "video/mp4") if continued else None
        spec = workflows.resolve({"mode": "swap", "quality": quality, "replace": replace}, True, False, True)
        (width, height), _, _ = workflows.swap_size(spec, 832, 480)
        spec.update(width=width, height=height, length=frames,
                    factor=workflows.smooth_factor(spec, animate["files"]))
        graph, _, _ = workflows.build(spec, animate["files"], presets.compose_prompt(workflows.ANIMATE_PROMPT),
                                      negative, f"val_swap_{frames}", start_image=start, video=video, motion=motion)
        failures += validate(label, graph)
    print("falhas de validacao:", failures)
    return failures


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
