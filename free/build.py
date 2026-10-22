"""Gera o que o usuario final usa:

  site/index.html              o site inteiro em um arquivo (hospede em qualquer lugar ou abra do disco)
  free/wan_studio_free.ipynb   o notebook que liga a GPU gratuita (Kaggle ou Colab)
  Wan Studio.url               atalho do Windows para o site local

Rode de novo sempre que alterar app/, vm/ ou free/launcher.py:

  python free/build.py
  python free/build.py --site https://meu-wan.netlify.app   (quando o site tiver endereco proprio)
  python free/build.py --so-troca   (a sessao baixa so as trocas de pessoa e de rosto: 46 GB em vez de 102)

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
# O notebook da sessao sem GPU (--cpu), que so serve para conferir se o codigo roda: nao gasta
# a cota de GPU, e cada passo leva minutos. O ComfyUI roda em CPU, nada e aquecido, os modelos
# de 14B ficam de fora e a sessao nao se anuncia ao site (ele nao pode confundi-la com a GPU).
CPU_NOTEBOOK_FILE = FREE / "wan_studio_cpu.ipynb"
# (O Animate em INT8 nao entra: na CPU o passo dele nao terminou em 35 minutos; so a GPU diz.)
CPU_ENV = {"SKIP_GPU_CHECK": "1", "KEEP_TORCH": "1", "SKIP_SPEEDUPS": "1", "PESSOA_INTEIRA": "1", "SO_TROCA": "1",
           "WAN_COMFY_ARGS": "--cpu", "WAN_SAM2_DEVICE": "cpu", "WARM_UP": "0",
           "WAN_NTFY_URL": "http://127.0.0.1:9"}
# A sessao de costume tem todos os modos, com a troca de pessoa baixando primeiro. Com
# --so-troca ela baixa so as trocas de pessoa e de rosto (46 GB em vez de 102) e deixa as duas GPUs para elas
# (vm/setup.sh, SO_TROCA).
SWAP_ENV = {"SO_TROCA": "1"}
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
código de conexão; depois da primeira vez, o site conecta sozinho. __DOWNLOADS__

**Vídeo demorado, ou vai sair de perto?** O Kaggle **desliga a sessão interativa depois de
~60 min sem você mexer NESTA página do Kaggle** (mexer no site, em outra aba, não conta) — mesmo
com um vídeo ainda sendo gerado. Para deixar gerando e voltar depois, use *Save Version → Save &
Run All (Commit)*: é um **processo em segundo plano, sem esse limite de inatividade** (vai até o
teto de ~9–12 h da sessão), e o site se conecta sozinho a ele.

**Para não gastar cota à toa:** a sessão também se encerra sozinha depois de `IDLE_MINUTES` sem
uso de verdade — nunca no meio de um vídeo (enquanto gera, ela se marca "em uso").
"""

DOWNLOADS = {
    True: "Os modelos das trocas continuam baixando em segundo plano: o da troca de pessoa (Wan 2.2\n"
          "Animate, 20 GB) em uns 3 minutos e o da troca de rosto (FLUX.2 Klein, 19 GB) logo depois;\n"
          "o site libera cada uma quando o modelo dela fica pronto.",
    False: "Os modelos continuam baixando em segundo plano, a troca de pessoa primeiro (uns 3\n"
           "minutos); depois a troca de rosto, a geração de imagem e de vídeo (5B), o melhorador de\n"
           "prompt e o 14B. Cada modo aparece no site quando o modelo dele fica pronto.",
}

SETUP = '''\
# 1. Configuração e instalação (uns 5 minutos na primeira célula)
SITE_URL = __SITE__   # endereço do seu site, se você o hospedou (opcional)
IDLE_MINUTES = 60     # encerra a sessão após esse tempo sem uso; 0 = nunca

import base64, io, os, shutil, sys, tarfile

os.environ.update(__ENV__)  # o tipo de sessão e os ajustes de teste: veja python free/build.py --help
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


def build_notebook(pairing, env=None, target=NOTEBOOK_FILE):
    setup = (SETUP.replace("__SITE__", json.dumps(pairing["site"])).replace("__ENV__", json.dumps(env or {}))
             .replace("__BUNDLE__", bundle()))
    notebook = {
        "cells": [cell("markdown", "leia-me", INTRO.replace("__DOWNLOADS__", DOWNLOADS[bool((env or {}).get("SO_TROCA"))])),
                  cell("code", "instalar", setup),
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
    target.write_text(json.dumps(notebook, indent=1), encoding="utf-8", newline="\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--site", help="endereco publico do site, para o notebook montar o link")
    parser.add_argument("--env", action="append", default=[], metavar="NOME=valor",
                        help="variavel de ambiente para a sessao (so para medir na GPU; refaca sem ela depois)")
    parser.add_argument("--so-troca", action="store_true",
                        help="a sessao baixa so as trocas de pessoa e de rosto (46 GB em vez de 102) e o site mostra so elas")
    parser.add_argument("--cpu", action="store_true",
                        help="gera o notebook da sessao sem GPU (free/wan_studio_cpu.ipynb), para conferir o "
                             "codigo sem gastar cota: python free/ligar.py --cpu")
    args = parser.parse_args()
    tests = dict(item.split("=", 1) for item in args.env)
    env = {**(SWAP_ENV if args.so_troca else {}), **tests}

    pairing = load_pairing(args.site)
    if args.cpu:  # nao mexe no site nem no notebook de verdade
        build_notebook(pairing, {**CPU_ENV, **env}, CPU_NOTEBOOK_FILE)
        print(f"{CPU_NOTEBOOK_FILE.relative_to(ROOT)}  ({max(1, CPU_NOTEBOOK_FILE.stat().st_size // 1024)} KB)")
        return
    build_site()
    build_notebook(pairing, env)
    if tests:
        print(f"ATENCAO: notebook de teste, com {tests}. Rode de novo sem --env antes de usar de verdade.")
    print("sessao: " + ("so as trocas de pessoa e de rosto (--so-troca)" if args.so_troca else "todos os modos, a troca de pessoa primeiro"))
    for path in (SITE_FILE, NOTEBOOK_FILE, SHORTCUT_FILE):
        print(f"{path.relative_to(ROOT)}  ({max(1, path.stat().st_size // 1024)} KB)")
    if pairing["site"]:
        print(f"site configurado: {pairing['site']}")


if __name__ == "__main__":
    main()
