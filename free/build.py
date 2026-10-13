"""Gera o que o usuario final usa:

  site/index.html              o site inteiro em um arquivo (hospede em qualquer lugar ou abra do disco)
  free/wan_studio_free.ipynb   o notebook que liga a GPU gratuita (Kaggle ou Colab)
  Wan Studio.url               atalho do Windows para o site local

Rode de novo sempre que alterar app/, vm/ ou free/launcher.py:

  python free/build.py
  python free/build.py --site https://meu-wan.netlify.app   (quando o site tiver endereco proprio)

`free/pairing.json` guarda o segredo que liga o notebook ao site. Ele vai dentro do notebook:
mantenha o notebook privado e nao publique esse arquivo.
"""

import argparse
import base64
import io
import json
import secrets
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FREE = ROOT / "free"
STATIC = ROOT / "app" / "static"
PAIRING_FILE = FREE / "pairing.json"
SITE_FILE = ROOT / "site" / "index.html"
NOTEBOOK_FILE = FREE / "wan_studio_free.ipynb"
SHORTCUT_FILE = ROOT / "Wan Studio.url"
BUNDLED = ("app", "vm", "free/launcher.py", "free/pairing.json")

sys.path.insert(0, str(ROOT / "app"))
import presets  # noqa: E402

INTRO = """\
# Wan Studio — GPU gratuita

Este notebook liga a GPU que gera os vídeos do seu **Wan Studio**. Mantenha-o **privado**:
ele contém a chave que conecta esta sessão ao seu site.

**Antes de rodar** (painel direito, *Session options*):

- **Accelerator: GPU T4 x2**
- **Internet: On** (exige telefone verificado na conta do Kaggle)

No Colab: *Ambiente de execução → Alterar o tipo de ambiente → GPU T4*.

**Para usar:** clique em **Run All**. Em uns 5 minutos a segunda célula mostra o link e o
código de conexão; depois da primeira vez, o site conecta sozinho. O melhorador de prompt, a
troca de pessoa (Animate) e o modelo 14B continuam baixando em segundo plano e aparecem no site
quando ficarem prontos.

**Para não gastar cota à toa:** a sessão se encerra sozinha depois de `IDLE_MINUTES` sem uso.
Para deixar rodando sem esta aba aberta, use *Save Version → Save & Run All (Commit)*.
"""

SETUP = '''\
# 1. Configuração e instalação (uns 5 minutos na primeira célula)
SITE_URL = __SITE__   # endereço do seu site, se você o hospedou (opcional)
IDLE_MINUTES = 60     # encerra a sessão após esse tempo sem uso; 0 = nunca

import base64, io, os, shutil, sys, tarfile

os.environ.update(__ENV__)  # ajustes de teste: python free/build.py --env NOME=valor
BUNDLE = "__BUNDLE__"


def pick_root():
    """Disco temporario com mais espaco; nunca /kaggle/working, que vira saida do notebook."""
    if os.path.isdir("/content"):
        return "/content/wan-studio"
    candidates = ["/tmp"]
    if os.path.isdir("/kaggle"):
        try:
            os.makedirs("/kaggle/temp", exist_ok=True)
            candidates.insert(0, "/kaggle/temp")
        except OSError:
            pass
    best = max(candidates, key=lambda path: shutil.disk_usage(path).free)
    return best + "/wan-studio"


ROOT = pick_root()
os.makedirs(ROOT, exist_ok=True)
archive = tarfile.open(fileobj=io.BytesIO(base64.b64decode(BUNDLE)))
try:
    archive.extractall(ROOT, filter="data")
except TypeError:  # Python anterior ao parametro filter
    archive.extractall(ROOT)
sys.path.insert(0, ROOT + "/free")
sys.modules.pop("launcher", None)
import launcher

print(f"Pasta de trabalho: {ROOT} ({shutil.disk_usage(ROOT).free // 2**30} GB livres)")
launcher.install()
'''

START = '''\
# 2. Liga o estúdio e mostra o link e o código de conexão
launcher.start(site_url=SITE_URL, idle_minutes=IDLE_MINUTES)
'''

KEEPALIVE = '''\
# 3. Mantém a sessão no ar (interrompa esta célula para encerrar)
launcher.keepalive()
'''


def load_pairing(site):
    """O segredo e criado uma vez e reaproveitado: reconstruir nao desfaz o pareamento."""
    if PAIRING_FILE.exists():
        pairing = json.loads(PAIRING_FILE.read_text(encoding="utf-8"))
    else:
        pairing = {"topic": "wan-" + secrets.token_urlsafe(18),
                   "key": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")}
    if site is not None:
        pairing["site"] = site.rstrip("/")
    pairing.setdefault("site", "")
    PAIRING_FILE.write_text(json.dumps(pairing, indent=2) + "\n", encoding="utf-8", newline="\n")
    return pairing


def inline_script(text):
    """Texto seguro para ficar dentro de <script>: nada pode fechar a tag antes da hora."""
    return text.replace("</", "<\\/")


def build_site():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    config = (f"window.WAN_SITE = {json.dumps({'ntfy': 'https://ntfy.sh'})};\n"
              f"window.WAN_PRESETS = {json.dumps(presets.public(), ensure_ascii=False)};")

    link = '<link rel="stylesheet" href="/static/style.css">'
    script = '<script src="/static/app.js"></script>'
    if link not in html or script not in html:
        raise SystemExit("app/static/index.html mudou: nao achei as tags de CSS/JS para embutir")
    html = html.replace(link, f"<style>\n{css}</style>")
    html = html.replace(script, f"<script>\n{inline_script(config)}\n</script>\n"
                                f"  <script>\n{inline_script(js)}</script>")
    SITE_FILE.parent.mkdir(exist_ok=True)
    SITE_FILE.write_text(html, encoding="utf-8", newline="\n")
    SHORTCUT_FILE.write_text(f"[InternetShortcut]\nURL={SITE_FILE.as_uri()}\n", encoding="utf-8",
                             newline="\r\n")


def bundle():
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for entry in BUNDLED:
            source = ROOT / entry
            paths = sorted(source.rglob("*")) if source.is_dir() else [source]
            for path in paths:
                if path.is_file() and "__pycache__" not in path.parts:
                    tar.add(path, arcname=path.relative_to(ROOT).as_posix())
    return base64.b64encode(buffer.getvalue()).decode()


def cell(kind, name, source):
    # O formato 4.5 do notebook exige um id por celula; sem ele o Kaggle avisa a cada execucao.
    body = {"cell_type": kind, "id": name, "metadata": {}, "source": source.splitlines(keepends=True)}
    if kind == "code":
        body.update(execution_count=None, outputs=[])
    return body


def build_notebook(pairing, env=None):
    setup = (SETUP.replace("__SITE__", json.dumps(pairing["site"])).replace("__ENV__", json.dumps(env or {}))
             .replace("__BUNDLE__", bundle()))
    notebook = {
        "cells": [cell("markdown", "leia-me", INTRO), cell("code", "instalar", setup),
                  cell("code", "ligar", START), cell("code", "manter", KEEPALIVE)],
        "metadata": {
            "accelerator": "GPU",
            "colab": {"gpuType": "T4", "provenance": []},
            "kaggle": {"accelerator": "nvidiaTeslaT4", "isGpuEnabled": True,
                       "isInternetEnabled": True, "language": "python", "sourceType": "notebook"},
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    NOTEBOOK_FILE.write_text(json.dumps(notebook, indent=1), encoding="utf-8", newline="\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--site", help="endereco publico do site, para o notebook montar o link")
    parser.add_argument("--env", action="append", default=[], metavar="NOME=valor",
                        help="variavel de ambiente para a sessao (so para medir na GPU; refaca sem ela depois)")
    args = parser.parse_args()
    env = dict(item.split("=", 1) for item in args.env)

    pairing = load_pairing(args.site)
    build_site()
    build_notebook(pairing, env)
    if env:
        print(f"ATENCAO: notebook de teste, com {env}. Rode de novo sem --env antes de usar de verdade.")
    for path in (SITE_FILE, NOTEBOOK_FILE, SHORTCUT_FILE):
        print(f"{path.relative_to(ROOT)}  ({max(1, path.stat().st_size // 1024)} KB)")
    if pairing["site"]:
        print(f"site configurado: {pairing['site']}")


if __name__ == "__main__":
    main()
