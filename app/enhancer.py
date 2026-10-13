"""Melhorador de prompt opcional: um LLM pequeno traduz o texto do usuario para ingles e o
detalha do jeito que o Wan 2.2 entende melhor.

O modelo so e carregado se existir em disco (vm/setup.sh baixa) e houver onde roda-lo sem
disputar memoria com o ComfyUI: por padrao, uma segunda GPU. Sem isso o estudio funciona
igual, apenas sem essa ajuda.
"""

import asyncio
import os
import re
import subprocess
import threading
import time
from pathlib import Path

MODEL_DIR = Path(os.environ.get(
    "ENHANCER_DIR", Path(__file__).resolve().parent.parent / "models-llm" / "enhancer"))
DEVICE = os.environ.get("ENHANCER_DEVICE", "auto")  # auto | off | cpu | cuda:N
MAX_INPUT_CHARS = 2000

_TEMPLATE = (
    "You write prompts for Wan 2.2, an AI {target} model. Rewrite the user's idea as one vivid "
    "English paragraph that the model can follow.\n"
    "Rules:\n"
    "- The user may write in any language, often Portuguese. Always answer in English.\n"
    "- Keep every subject, action, place and detail the user gave. Never change what the "
    "{noun} is about and never add new characters.\n"
    "- {focus}\n"
    "- Describe only what can be seen. No sound, music, speech, on-screen text or titles.\n"
    "{camera}"
    "- Write {length} words. Output only the prompt: no quotes, no preamble, no lists, no "
    "explanations."
)
_UNSEEN = (
    "Do not invent appearance details such as clothes, colors or faces, because they could "
    "contradict the image."
)
_MODES = {
    "t2v": {
        "target": "text-to-video", "noun": "video", "length": "60 to 100",
        "focus": "Describe, in this order: the subject and how it looks, the action and how it "
                 "moves, the setting, the lighting and mood.",
    },
    "i2v": {
        "target": "image-to-video", "noun": "video", "length": "30 to 60",
        "focus": "The video starts from an image the user provided, which you cannot see. "
                 "Describe what happens next: how the subject and the scene move and change. "
                 + _UNSEEN,
    },
    "flf": {
        "target": "image-to-video", "noun": "video", "length": "30 to 60",
        "focus": "The video goes from a first image to a last image that the user provided, "
                 "which you cannot see. Describe the motion or transformation that connects "
                 "them. " + _UNSEEN,
    },
    "img": {
        "target": "text-to-image", "noun": "image", "length": "50 to 90",
        "focus": "Describe, in this order: the subject and how it looks, the pose or moment, "
                 "the composition and framing, the setting, the lighting and mood. It is a "
                 "single still image, so describe no motion over time.",
    },
}


def system_prompt(mode, camera_fixed):
    """Instrucoes do LLM. `camera_fixed`: o usuario ja escolheu um movimento de camera."""
    if mode == "img":
        camera = ""
    elif camera_fixed:
        camera = "- Do not describe any camera movement; it is added separately.\n"
    else:
        # Sem jargao: o Wan le ao pe da letra e "crane shot" vira um guindaste na cena.
        camera = ("- End with one fitting camera movement, said in plain words such as \"the "
                  "camera slowly pushes in\" or \"the camera rises above the scene\". Never use "
                  "the words dolly, crane, truck or drone.\n")
    return _TEMPLATE.format(camera=camera, **_MODES.get(mode, _MODES["t2v"]))


def clean(reply, fallback):
    """Tira raciocinio, rotulos e aspas da resposta; devolve `fallback` se nao sobrar nada."""
    text = re.sub(r"<think>.*?</think>", " ", reply, flags=re.S).split("</think>")[-1]
    text = re.sub(r"\*\*|__", "", text)  # negrito de markdown
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^(enhanced prompt|prompt|here is[^:]{0,60})\s*:\s*", "", text, flags=re.I)
    text = text.strip("\"'“”`* ")
    return text if len(text) >= 10 else fallback


def gpu_count():
    try:
        listing = subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return 0
    return sum(1 for line in listing.stdout.splitlines() if line.startswith("GPU "))


def pick_device():
    if DEVICE == "off":
        return None
    if DEVICE != "auto":
        return DEVICE
    # A primeira GPU e do ComfyUI; so vale usar uma que esteja sobrando.
    return "cuda:1" if gpu_count() >= 2 else None


class Enhancer:
    """Estados: off, waiting (modelo ainda baixando), loading, ready, sleeping (cedeu a GPU a
    um worker de video e volta depois), error."""

    def __init__(self):
        self.state = "off"
        self.detail = ""
        self._model = None
        self._tokenizer = None
        self._device = None
        self._lock = None
        self._lent = False              # a GPU esta emprestada a um worker de video
        self._guard = threading.Lock()  # carregar e descarregar nunca ao mesmo tempo

    def start(self):
        threading.Thread(target=self._run, name="enhancer", daemon=True).start()

    def _set(self, state, detail=""):
        self.state, self.detail = state, detail

    def _run(self):
        self._device = pick_device()
        if self._device is None:
            reason = "desativado" if DEVICE == "off" else "precisa de uma segunda GPU"
            return self._set("off", reason)
        self._set("waiting", "baixando o modelo")
        while not (MODEL_DIR / ".complete").exists():
            time.sleep(10)
        self._wake_up()

    def _wake_up(self):
        """Carrega o modelo, a menos que a GPU esteja emprestada: ai fica dormindo."""
        with self._guard:
            if self._lent:
                return self._set("sleeping", "a segunda GPU está gerando vídeo")
            self._set("loading", "carregando o modelo")
            try:
                self._load(self._device)
            except Exception as exc:  # qualquer falha aqui so desliga o recurso
                return self._set("error", f"{type(exc).__name__}: {exc}"[:300])
            self._set("ready")

    def _load(self, device):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        dtype = torch.float16 if device.startswith("cuda") else torch.float32
        self._tokenizer = AutoTokenizer.from_pretrained(MODEL_DIR)
        try:
            model = AutoModelForCausalLM.from_pretrained(MODEL_DIR, dtype=dtype)
        except TypeError:  # transformers antigo so conhece torch_dtype
            model = AutoModelForCausalLM.from_pretrained(MODEL_DIR, torch_dtype=dtype)
        self._model = model.to(device).eval()

    def _unload(self):
        import gc

        import torch

        with self._guard:
            self._model = self._tokenizer = None
            gc.collect()
            torch.cuda.empty_cache()
            self._set("sleeping", "a segunda GPU está gerando vídeo")

    async def sleep(self):
        """Cede a GPU a um worker de video. Devolve False enquanto nao puder ceder (o modelo
        esta no meio da carga); nesse caso basta chamar de novo mais tarde."""
        self._lent = True
        if self.state == "loading":
            return False
        if self.state == "ready":
            if self._lock is None:
                self._lock = asyncio.Lock()
            async with self._lock:  # espera uma melhoria em andamento terminar
                await asyncio.get_running_loop().run_in_executor(None, self._unload)
        return True

    def wake(self):
        """A GPU voltou a ficar livre: carrega o modelo de novo, em segundo plano."""
        self._lent = False
        if self.state == "sleeping":
            self._set("loading", "carregando o modelo")
            threading.Thread(target=self._wake_up, name="enhancer", daemon=True).start()

    def _generate(self, prompt, mode, camera_fixed):
        import torch

        messages = [
            {"role": "system", "content": system_prompt(mode, camera_fixed)},
            {"role": "user", "content": prompt[:MAX_INPUT_CHARS]},
        ]
        text = self._tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True, enable_thinking=False)
        inputs = self._tokenizer(text, return_tensors="pt").to(self._device)
        with torch.inference_mode():
            output = self._model.generate(
                **inputs, max_new_tokens=260, do_sample=True, temperature=0.7, top_p=0.8,
                top_k=20, repetition_penalty=1.05)
        reply = self._tokenizer.decode(output[0][inputs["input_ids"].shape[1]:],
                                       skip_special_tokens=True)
        return clean(reply, prompt)

    async def enhance(self, prompt, mode="t2v", camera_fixed=False):
        if self.state != "ready":
            raise RuntimeError("o melhorador de prompt não está pronto")
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, self._generate, prompt, mode, camera_fixed)
