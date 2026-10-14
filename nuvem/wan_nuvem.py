"""O Wan Studio numa GPU grande do Modal (https://modal.com), que cobra por segundo ligada e da
30 dolares de credito por mes. Quem usa isto e nuvem/ligar.py (no Windows, dois cliques em
"Ligar GPU rapida.bat"); os comandos do Modal ficam para quem quiser mexer a mao:

  modal deploy nuvem/wan_nuvem.py          publica (monta a imagem na primeira vez, ~8 min)
  modal run nuvem/wan_nuvem.py::baixar     baixa os modelos da troca de pessoa para o volume

O que e publicado:

  imagem    Debian + PyTorch + ComfyUI e os nos, montada por vm/setup.sh (as mesmas versoes do
            Kaggle). O codigo do Wan Studio (app/, vm/, free/launcher.py) nao entra nela: e
            anexado a cada publicacao, entao mudar o codigo nao refaz a imagem.
  volume    "wan-studio-modelos": os modelos, baixados uma vez so (`baixar`).
  sessao    a funcao com GPU: liga o ComfyUI e o servidor, abre um tunel publico, anuncia o
            endereco ao site (como o notebook do Kaggle) e fica no ar ate passar o tempo sem
            uso (nuvem/sessao.py). So uma por vez.
  acordar   um endereco fixo, sem GPU, que o site chama para ligar a sessao: com ele o
            computador nao precisa estar ligado. So atende quem manda a chave do pareamento.
  estado    um dicionario: a sessao em curso, a chave do `acordar` e o tempo de uso do mes.
"""

import hmac
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import modal

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
NAME = os.environ.get("WAN_NUVEM_APP", "wan-studio")
REMOTE = "/wan"            # o projeto dentro do conteiner
MODELS_DIR = "/modelos"    # onde o volume aparece
PYTHON = "3.12"
# O PyTorch e o do Kaggle (2.11), na variante de CUDA 13: e a que os nucleos otimizados do
# ComfyUI (comfy-kitchen) e o onnxruntime-gpu atual pedem, e o driver do Modal (580) a aceita.
TORCH = "2.11.0"
TORCH_INDEX = "https://download.pytorch.org/whl/cu130"
# A placa de quando ninguem escolhe. Quem liga pode pedir outra (nuvem/ligar.py --gpu).
GPU = os.environ.get("WAN_GPU", "H100")
SESSION_HOURS = 24         # o teto do Modal para uma funcao; a sessao sai antes, por ociosidade

app = modal.App(NAME)
models = modal.Volume.from_name(f"{NAME}-modelos", create_if_missing=True)
state = modal.Dict.from_name(f"{NAME}-estado", create_if_missing=True)

SETUP = ("MODELS=free PHASE=core SKIP_MODELS=1 SKIP_GPU_CHECK=1 SKIP_APT=1 KEEP_TORCH=1 NO_VENV=1 "
         "PYTHON=python bash vm/setup.sh")


def build_image():
    """A imagem da sessao e do download. Dentro do conteiner a definicao nao e usada (e os
    arquivos locais nem existem la): basta um objeto do mesmo tipo."""
    if not modal.is_local():
        return modal.Image.debian_slim(python_version=PYTHON)
    ignore = ["**/__pycache__", "**/*.pyc"]
    return (
        modal.Image.debian_slim(python_version=PYTHON)
        .apt_install("git", "ffmpeg", "aria2", "curl", "ca-certificates", "libgl1", "libglib2.0-0")
        .pip_install(f"torch=={TORCH}", "torchvision", "torchaudio", index_url=TORCH_INDEX)
        # So o instalador e a lista de pacotes entram na imagem: e deles que ela depende. Saem no
        # fim da etapa para o codigo anexado a cada publicacao ocupar o lugar.
        .add_local_file(ROOT / "vm" / "setup.sh", f"{REMOTE}/vm/setup.sh", copy=True)
        .add_local_file(ROOT / "app" / "requirements.txt", f"{REMOTE}/app/requirements.txt", copy=True)
        .run_commands(f"cd {REMOTE} && {SETUP} && rm -rf {REMOTE}/vm {REMOTE}/app {REMOTE}/downloads")
        .add_local_dir(ROOT / "vm", f"{REMOTE}/vm", ignore=ignore)
        .add_local_dir(ROOT / "app", f"{REMOTE}/app", ignore=ignore)
        .add_local_file(ROOT / "free" / "launcher.py", f"{REMOTE}/free/launcher.py")
        .add_local_file(HERE / "sessao.py", f"{REMOTE}/nuvem/sessao.py")
    )


def build_light():
    """A imagem do `acordar`: so o que um endereco web precisa."""
    image = modal.Image.debian_slim(python_version=PYTHON).pip_install("fastapi[standard]")
    if modal.is_local():
        image = image.add_local_file(HERE / "sessao.py", f"{REMOTE}/nuvem/sessao.py")
    return image


image = build_image()
light = build_light()


def session_body():
    sys.path.insert(0, f"{REMOTE}/nuvem")
    import sessao as body
    return body


@app.function(image=image, volumes={MODELS_DIR: models}, timeout=6 * 3600, cpu=4, memory=2048)
def baixar(everything: bool = False):
    """Baixa os modelos para o volume: os da troca de pessoa (36 GB) ou, com `everything`,
    tambem os do 5B e do 14B (mais 50 GB). O que ja esta la nao e baixado de novo."""
    env = {**os.environ, "MODELS": "free", "PHASE": "models", "MODELS_DIR": MODELS_DIR,
           # Os pedacos de um download chegam fora de ordem: num disco local, e so depois o
           # arquivo pronto vai para o volume, que e feito para gravar de uma vez.
           "INCOMING": "/tmp/baixando", "NO_ROOM_CHECK": "1", "ANIMATE_KIND": "fp8", "ENHANCER": "off",
           "NO_VENV": "1", "PYTHON": sys.executable, "TROCA_PRIMEIRO": "1"}
    if not everything:
        env["SO_TROCA"] = "1"
    code = subprocess.run(["bash", f"{REMOTE}/vm/setup.sh"], env=env).returncode
    models.commit()
    body = session_body()
    missing = body.missing_models(Path(MODELS_DIR) / "models")
    if code or missing:
        raise RuntimeError("o download dos modelos falhou" + (f" (faltam: {', '.join(missing)})" if missing else ""))
    return sorted(str(path.relative_to(MODELS_DIR)) for path in Path(MODELS_DIR, "models").rglob("*")
                  if path.is_file() and path.stat().st_size > 1 << 20)


@app.function(image=image, gpu=GPU, volumes={MODELS_DIR: models}, timeout=SESSION_HOURS * 3600,
              cpu=4, memory=32 * 1024, max_containers=1, scaledown_window=2)
def sessao(config: dict):
    """Uma sessao do Wan Studio, do ligar ao desligar por ociosidade."""
    return session_body().run({**config, "models_dir": MODELS_DIR}, state, modal.forward)


def request_session(config, now=None):
    """Liga uma sessao se nao houver uma no ar. `config`: {"gpu", "idle_minutes", "site",
    "pairing", "comfy_args"}. Devolve {"state": "on" | "starting" | "requested", "gpu", "id"}.
    Serve ao atalho (no computador) e ao `acordar` (no Modal): por isso a funcao e buscada
    pelo nome, que vale nos dois lugares."""
    sys.path.insert(0, str(HERE))
    sys.path.insert(0, f"{REMOTE}/nuvem")
    import sessao as body

    now = now or time.time()
    current = state.get("session")
    if body.alive(current, now):
        return {"state": "on" if current.get("url") else "starting", "gpu": current.get("gpu", ""),
                "id": current.get("id")}
    gpu = config.get("gpu") or GPU
    function = modal.Function.from_name(NAME, "sessao")
    if gpu != GPU:
        function = function.with_options(gpu=gpu)
    session_id = uuid.uuid4().hex[:12]
    # O registro vem antes do pedido: dois cliques seguidos encontram a sessao "pedida".
    asked = {"id": session_id, "gpu": gpu, "started": now, "beat": now, "url": None, "password": None,
             "ended": None, "busy": False, "stage": "pedida"}
    state["session"] = asked
    try:
        call = function.spawn({**config, "gpu": gpu, "id": session_id})
    except Exception:
        # Recusada (sem credito, sem cartao, placa que nao existe): o registro nao pode ficar
        # dizendo que ha uma sessao subindo, ou os proximos pedidos esperariam por ela.
        state["session"] = {**asked, "ended": now, "stage": "recusada pelo Modal"}
        raise
    state["session"] = {**(state.get("session") or asked), "call": call.object_id}
    return {"state": "requested", "gpu": gpu, "id": session_id}


@app.function(image=light, max_containers=1, scaledown_window=2)
@modal.asgi_app(label=f"{NAME}-acordar")
def acordar():
    """POST / com {"key": ...}: liga a sessao com a configuracao que o atalho deixou guardada."""
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.middleware.cors import CORSMiddleware

    web = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    web.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["POST", "OPTIONS"],
                       allow_headers=["Content-Type"], max_age=86400)

    @web.post("/")
    def wake(body: dict):
        saved = state.get("wake") or {}
        key = str(saved.get("key") or "")
        if not key or not hmac.compare_digest(str(body.get("key", "")).encode(), key.encode()):
            raise HTTPException(status_code=403, detail="chave inválida")
        try:
            return request_session(saved.get("config") or {})
        except Exception as error:  # sem credito, placa indisponivel...
            raise HTTPException(status_code=502, detail=str(error)[:300]) from None

    return web
