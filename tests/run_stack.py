"""Sobe a pilha do notebook localmente, do mesmo jeito que as celulas fazem, trocando so o que
exige Linux/GPU: o ComfyUI vira o simulador e o tunel vira o endereco local.

  1. desempacota o BUNDLE do notebook gerado (como a celula 1)
  2. launcher.start()      (celula 2)
  3. launcher.keepalive()  (celula 3) ate o site pedir para desligar
"""
import base64
import io
import json
import os
import re
import secrets
import shutil
import sys
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
WORK = HERE / ".work"
ROOT = WORK / "root"

notebook = json.loads((PROJECT / "free" / "wan_studio_free.ipynb").read_text(encoding="utf-8"))
source = "".join(notebook["cells"][1]["source"])
bundle = re.search(r'BUNDLE = "([^"]+)"', source).group(1)

shutil.rmtree(ROOT, ignore_errors=True)
ROOT.mkdir(parents=True)
tarfile.open(fileobj=io.BytesIO(base64.b64decode(bundle))).extractall(ROOT, filter="data")
(ROOT / "ComfyUI").mkdir()
shutil.copy(HERE / "fake_comfy.py", ROOT / "ComfyUI" / "main.py")
# Pareamento so do teste: os anuncios desta pilha nao podem chegar ao navegador de verdade
# do usuario, que escuta o topico do pareamento real.
(ROOT / "free" / "pairing.json").write_text(json.dumps({
    "topic": "wan-teste-" + secrets.token_urlsafe(18),
    "key": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("="), "site": "",
}), encoding="utf-8")

(WORK / "fake_sets.txt").unlink(missing_ok=True)
os.environ.update(
    FAKE_ASSETS=str(WORK),
    FAKE_SHARED=str(WORK / "fake_sets.txt"),  # os dois simuladores dividem a "pasta de modelos"
    WAN_WORKERS="2",                          # como no Kaggle: duas GPUs, um ComfyUI em cada
    WARM_UP="0",                              # o aquecimento mudaria a conta dos grafos recebidos
    WAN_SKIP_EXTRAS="1",
    WAN_KEEPALIVE_STEP="2",
    WAN_TUNNEL_CMD=json.dumps([sys.executable, "-c",
                               "import time; print('http://127.0.0.1:8080', flush=True); time.sleep(10**6)"]),
    PYTHONDONTWRITEBYTECODE="1",
)
sys.path.insert(0, str(ROOT / "free"))
import launcher  # noqa: E402

launcher.start(idle_minutes=60)
(WORK / "code.txt").write_text(launcher.connection_code(), encoding="utf-8")
print("CODE_READY", flush=True)
launcher.keepalive()
print("STACK_STOPPED", flush=True)
