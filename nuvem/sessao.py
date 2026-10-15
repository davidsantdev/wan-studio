"""Uma sessao do Wan Studio dentro de um conteiner com GPU do Modal. Quem a chama e a funcao
`sessao` de nuvem/wan_nuvem.py; aqui nao ha nada do Modal alem do que ela entrega (o tunel e
o dicionario de estado), para dar para testar sem a nuvem (tests/nuvem_test.py).

Faz o que o notebook do Kaggle faz (free/launcher.py: ligar, anunciar ao site, manter no ar e
desligar por ociosidade), com tres diferencas:

  - nada e instalado nem baixado aqui: o ComfyUI ja vem na imagem e os modelos num volume, que
    `prepare` liga a pasta de modelos do ComfyUI. A maquina cobra por segundo ligada;
  - o endereco publico e um tunel do proprio Modal, sem cloudflared;
  - a sessao registra que esta viva num dicionario do Modal (`state`), para o atalho
    nuvem/ligar.py e o botao Ligar do site nao subirem duas ao mesmo tempo.

O que fica em `state` (chaves): "session", o registro da sessao atual ou da ultima
({"id", "gpu", "started", "url", "password", "ended", "busy", "stage", "beat"}); e "usage", a
lista do tempo que cada sessao ficou ligada, de onde sai a conta dos creditos do mes.
"""

import os
import shutil
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMFY = ROOT / "ComfyUI"
STUDIO_PORT = 8080
# Depois disto sem dar sinal, a sessao registrada e dada como morta (a volta do `keepalive`
# leva uns 20 s). Enquanto o ComfyUI sobe nao ha volta nenhuma: vale o prazo de `STARTING`.
BEAT_LIMIT = 120
STARTING = 15 * 60
USAGE_KEPT = 500
# O minimo para a troca de pessoa: sem isto a sessao nem liga, e o erro diz o que fazer.
ESSENTIAL = (("text_encoders", "umt5_xxl_fp8_e4m3fn_scaled.safetensors"),
             ("vae", "wan_2.1_vae.safetensors"),
             ("clip_vision", "clip_vision_h.safetensors"),
             ("loras", "lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors"))
# Com este arquivo no volume (o download de costume), a sessao oferece os outros modos tambem.
FULL_STUDIO = "wan2.2_ti2v_5B_fp16.safetensors"


def alive(record, now=None):
    """A sessao registrada em `state["session"]` ainda esta no ar?"""
    if not record or record.get("ended"):
        return False
    now = now or time.time()
    limit = BEAT_LIMIT if record.get("url") else STARTING
    return now - record.get("beat", 0) < limit


def link_models(models_dir, comfy=None):
    """A pasta de modelos do ComfyUI vira um atalho para o volume (o que a imagem traz nela,
    so as pastas vazias, vai junto na primeira vez)."""
    comfy = Path(comfy or COMFY)
    target = Path(models_dir) / "models"
    target.mkdir(parents=True, exist_ok=True)
    models = comfy / "models"
    if models.is_symlink():
        return target
    if models.is_dir():
        shutil.copytree(models, target, dirs_exist_ok=True)
        shutil.rmtree(models)
    models.symlink_to(target, target_is_directory=True)
    return target


def missing_models(target):
    """O que falta do minimo, como "pasta/arquivo"; o Animate vale em qualquer compressao."""
    target = Path(target)
    missing = [f"{folder}/{name}" for folder, name in ESSENTIAL if not (target / folder / name).exists()]
    if not list((target / "diffusion_models").glob("*Animate*")):
        missing.append("diffusion_models/(Wan 2.2 Animate)")
    return missing


def only_swap(target):
    """O volume so tem a troca de pessoa (um download antigo, de antes de o 5B vir junto)?"""
    return not (Path(target) / "diffusion_models" / FULL_STUDIO).exists()


def install_nodes(root=None, comfy=None):
    """Os nos do proprio Wan Studio chegam com o codigo (montado na partida), nao com a imagem."""
    source = Path(root or ROOT) / "vm" / "nodes" / "wan_studio"
    if source.is_dir():
        shutil.copytree(source, Path(comfy or COMFY) / "custom_nodes" / "wan_studio", dirs_exist_ok=True)


def prepare(models_dir):
    target = link_models(models_dir)
    missing = missing_models(target)
    if missing:
        raise SystemExit("os modelos da troca de pessoa não estão no volume (" + ", ".join(missing)
                         + "). Rode: python nuvem\\ligar.py modelos")
    if only_swap(target):  # o servidor avisa o site, que mostra so esse modo
        os.environ["SO_TROCA"] = "1"
    install_nodes()


def record(state, **fields):
    """Atualiza `state["session"]`. O dicionario e remoto: falhar aqui nao derruba a sessao."""
    try:
        current = dict(state.get("session") or {})
        current.update(fields, beat=time.time())
        state["session"] = current
    except Exception as error:  # rede do Modal
        print(f"(aviso: não consegui registrar o estado da sessão: {error})")


def charge(state, entry):
    """Guarda quanto tempo a sessao ficou ligada, para a conta dos creditos do mes."""
    try:
        usage = list(state.get("usage") or [])
        usage.append(entry)
        state["usage"] = usage[-USAGE_KEPT:]
    except Exception as error:
        print(f"(aviso: não consegui registrar o uso da sessão: {error})")


def run(config, state, forward, launcher=None, prepare=prepare):
    """Liga, mantem e desliga uma sessao. `config`: {"id", "gpu", "idle_minutes", "site",
    "pairing", "models_dir", "comfy_args", "price"}. `forward(porta)` abre o tunel: um contexto que
    entrega um objeto com `.url`. `launcher` e `prepare` so sao trocados nos testes.
    Devolve um resumo: {"reason", "seconds", "code"}."""
    other = state.get("session")
    if alive(other) and other.get("id") != config["id"]:
        print("Já há uma sessão no ar: esta não sobe outra.")
        return {"reason": "já ligada", "seconds": 0, "code": 0}

    began = time.time()
    record(state, id=config["id"], gpu=config.get("gpu", ""), started=began, url=None, password=None,
           ended=None, busy=False, stage="iniciando")
    reason, code = "erro", 1
    try:
        prepare(config.get("models_dir", "/modelos"))
        if launcher is None:
            sys.path.insert(0, str(ROOT / "free"))
            import launcher
        launcher.PAIRING = dict(config.get("pairing") or {})
        os.environ.update(WAN_SKIP_EXTRAS="1", WAN_STUDIO_HOST="0.0.0.0")
        if config.get("comfy_args"):
            os.environ["WAN_COMFY_ARGS"] = config["comfy_args"]
        if config.get("price"):  # dolares por hora: o servidor mostra quanto cada video gastou
            os.environ["WAN_PRICE_HOUR"] = str(config["price"])

        def tick(info):
            record(state, busy=bool(info and info["idle"]["busy"]), stage="no ar" if info else "sem resposta")

        with forward(STUDIO_PORT) as tunnel:
            launcher.SESSION["tick"] = tick
            launcher.start(site_url=config.get("site", ""), idle_minutes=float(config.get("idle_minutes", 5)),
                           tunnel_url=tunnel.url)
            record(state, url=launcher.SESSION["url"], password=launcher.SESSION["password"], stage="no ar")
            launcher.keepalive()
        reason, code = "desligada", 0
    except SystemExit as error:  # o launcher encerra assim quando algo essencial falha
        reason = str(error) or "encerrada"
        print(f"SystemExit: {error}")
    except KeyboardInterrupt:  # o Modal tomou a maquina de volta, ou a funcao foi cancelada
        reason = "interrompida"
        print("Sessão interrompida pela plataforma.")
    except Exception:
        traceback.print_exc()
    finally:
        if launcher is not None:
            launcher.stop(quiet=True)
        seconds = round(time.time() - began)
        record(state, ended=time.time(), url=None, password=None, stage=reason, busy=False)
        charge(state, {"id": config["id"], "gpu": config.get("gpu", ""), "started": round(began),
                       "seconds": seconds, "reason": reason})
        print(f"Sessão encerrada ({reason}) depois de {seconds // 60} min {seconds % 60} s.")
    return {"reason": reason, "seconds": seconds, "code": code}
