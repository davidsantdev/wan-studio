"""Testa nuvem/ (o Wan Studio numa GPU do Modal) sem conta, sem GPU e sem internet.

  python tests/nuvem_test.py

O `modal` e o de mentira (tests/fake_modal), e quem faz o papel da nuvem e tests/nuvem_cloud.py:
ele executa as funcoes que o atalho pede. A sessao que sobe e a de verdade (nuvem/sessao.py, o
launcher e o servidor), com o ComfyUI simulado. O que este teste nao prova e o que so a conta
de verdade mostra: a imagem montar, os modelos baixarem e a placa gerar.
Precisa de Python com aiohttp (o servidor) e das portas 8080 e 8188 livres.
"""
import base64
import importlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
WORK = HERE / ".work" / "nuvem"
HOME = WORK / "modal"
ROOT = WORK / "root"
PAIRING = WORK / "pairing.json"
OPENER = WORK / "abrir.html"
STUDIO = "http://127.0.0.1:8080"
failures = []

ENV = {**os.environ, "FAKE_MODAL_HOME": str(HOME), "WAN_NUVEM_HOME": str(WORK / "casa"),
       "WAN_PAIRING_FILE": str(PAIRING), "WAN_OPENER_FILE": str(OPENER), "WAN_POLL_SECONDS": "0.2",
       "NUVEM_ROOT": str(ROOT), "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
       "PYTHONPATH": str(HERE / "fake_modal") + os.pathsep + os.environ.get("PYTHONPATH", "")}


def check(label, condition, detail=""):
    print(("ok    " if condition else "FALHA ") + label + (f"  [{detail}]" if detail else ""), flush=True)
    if not condition:
        failures.append(label)


def launcher(*args, **env):
    """Roda nuvem/ligar.py com o modal de mentira: (codigo de saida, o que ele escreveu)."""
    result = subprocess.run([sys.executable, str(PROJECT / "nuvem" / "ligar.py"), *args, "--sem-navegador"],
                            env={**ENV, **env}, capture_output=True, text=True, encoding="utf-8", errors="replace",
                            input="", timeout=240)
    return result.returncode, result.stdout + result.stderr


def responds(url):
    try:
        urllib.request.urlopen(url, timeout=2).close()
        return True
    except urllib.error.HTTPError:
        return True
    except OSError:
        return False


def api(record, path):
    token = base64.b64encode(f"wan:{record['password']}".encode()).decode()
    request = urllib.request.Request(record["url"] + path, headers={"Authorization": f"Basic {token}"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def opener_data():
    """O que a pagina de abertura entrega ao site (o codigo depois de "#c=")."""
    text = OPENER.read_text(encoding="utf-8") if OPENER.exists() else ""
    if "#c=" not in text:
        return {}
    code = text.split("#c=")[1].split('"')[0]
    return json.loads(base64.urlsafe_b64decode(code + "=" * (-len(code) % 4)))


def prepare():
    busy = [port for port in (8080, 8188) if responds(f"http://127.0.0.1:{port}/")]
    if busy:
        raise SystemExit(f"portas em uso: {busy}. Feche o que estiver usando e tente de novo.")
    shutil.rmtree(WORK, ignore_errors=True)
    HOME.mkdir(parents=True)
    # A copia do projeto em que a sessao roda: o codigo de verdade, com o ComfyUI simulado.
    ignore = shutil.ignore_patterns("__pycache__")
    shutil.copytree(PROJECT / "app", ROOT / "app", ignore=ignore)
    shutil.copytree(PROJECT / "vm" / "nodes", ROOT / "vm" / "nodes", ignore=ignore)
    for name in ("free/launcher.py", "nuvem/sessao.py"):
        (ROOT / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(PROJECT / name, ROOT / name)
    (ROOT / "ComfyUI").mkdir()
    shutil.copy(HERE / "fake_comfy.py", ROOT / "ComfyUI" / "main.py")
    # Um pareamento so do teste: o de verdade nao sai de free/.
    PAIRING.write_text(json.dumps({
        "topic": "wan-teste-" + secrets.token_urlsafe(18),
        "key": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("="), "site": ""}), encoding="utf-8")


# ---------------------------------------------------------------- pecas, uma a uma

class FakeLauncher:
    """O que nuvem/sessao.py usa do launcher, anotando a ordem das chamadas."""

    def __init__(self):
        self.SESSION = {"tick": None, "url": None, "password": None}
        self.PAIRING = None
        self.events = []

    def start(self, site_url, idle_minutes, tunnel_url):
        self.events.append(("start", site_url, idle_minutes, tunnel_url))
        self.SESSION.update(url=tunnel_url, password="senha-de-teste")

    def keepalive(self):
        self.SESSION["tick"]({"idle": {"busy": True}})
        self.events.append("keepalive")

    def stop(self, quiet=False):
        self.events.append("stop")


def units():
    os.environ["FAKE_MODAL_HOME"] = str(HOME)
    sys.path.insert(0, str(HERE / "fake_modal"))
    sys.path.insert(0, str(PROJECT / "nuvem"))
    import modal
    import sessao

    now = 1_000_000.0
    check("U1 sessão viva: com sinal recente; morta: sem registro, encerrada ou sem sinal há 2 min",
          sessao.alive({"url": "x", "beat": now - 30}, now) and not sessao.alive(None, now)
          and not sessao.alive({"url": "x", "beat": now - 30, "ended": now}, now)
          and not sessao.alive({"url": "x", "beat": now - 200}, now))
    check("U2 enquanto sobe (sem endereço) ela tem 15 minutos antes de ser dada como morta",
          sessao.alive({"url": None, "beat": now - 600}, now) and not sessao.alive({"url": None, "beat": now - 1000}, now))

    tree = WORK / "volume" / "models"
    (tree / "diffusion_models").mkdir(parents=True)
    check("U3 volume vazio: falta tudo, e o Animate é citado", len(sessao.missing_models(tree)) == 5
          and any("Animate" in item for item in sessao.missing_models(tree)), str(sessao.missing_models(tree))[:90])
    for folder, name in sessao.ESSENTIAL:
        (tree / folder).mkdir(exist_ok=True)
        (tree / folder / name).write_bytes(b"x")
    (tree / "diffusion_models" / "Wan2.2-Animate-14B-Q4_K_S.gguf").write_bytes(b"x")
    check("U4 com o mínimo (e o Animate em qualquer compressão) nada falta", sessao.missing_models(tree) == [])
    alone = sessao.only_swap(tree)
    (tree / "diffusion_models" / sessao.FULL_STUDIO).write_bytes(b"x")
    check("U4b volume só com a troca de pessoa: a sessão avisa o site; com o 5B nele (modelos --tudo), oferece tudo",
          alone and not sessao.only_swap(tree))
    (tree / "diffusion_models" / sessao.FULL_STUDIO).unlink()

    comfy = WORK / "comfy"
    (comfy / "models" / "vae").mkdir(parents=True)
    (comfy / "models" / "vae" / "put_vae_here").write_text("", encoding="utf-8")
    try:
        target = sessao.link_models(WORK / "disco", comfy)
        again = sessao.link_models(WORK / "disco", comfy)
        check("U5 a pasta de modelos do ComfyUI vira um atalho para o volume, e o que havia vai junto",
              (comfy / "models").is_symlink() and (target / "vae" / "put_vae_here").exists() and again == target)
    except OSError as error:  # o Windows so cria atalhos com o modo de desenvolvedor ligado
        print(f"--    U5 pulado: este sistema não deixa criar atalhos de pasta ({error.__class__.__name__})")

    state, fake = {}, FakeLauncher()
    config = {"id": "s1", "gpu": "H100", "idle_minutes": 7, "site": "https://site", "pairing": {"topic": "t", "key": "k"}}
    summary = sessao.run(config, state, modal.forward, launcher=fake, prepare=lambda models_dir: None)
    check("U6 a sessão liga com o túnel, a ociosidade e o pareamento pedidos, mantém e desliga",
          fake.events == [("start", "https://site", 7.0, "http://127.0.0.1:8080"), "keepalive", "stop"]
          and fake.PAIRING == {"topic": "t", "key": "k"} and summary["code"] == 0 and summary["reason"] == "desligada",
          str(fake.events)[:120])
    record = state["session"]
    check("U7 o registro fica encerrado, sem endereço nem senha, e o uso entra na conta do mês",
          record["id"] == "s1" and record["ended"] and record["url"] is None and record["password"] is None
          and [entry["gpu"] for entry in state["usage"]] == ["H100"] and state["usage"][0]["reason"] == "desligada",
          str(record)[:120])

    state = {"session": {"id": "outra", "url": "http://x", "beat": time.time()}}
    fake = FakeLauncher()
    summary = sessao.run({**config, "id": "s2"}, state, modal.forward, launcher=fake, prepare=lambda models_dir: None)
    check("U8 com uma sessão no ar, uma segunda não sobe nem mexe no registro da primeira",
          fake.events == [] and state["session"]["id"] == "outra" and summary["reason"] == "já ligada")

    state, fake = {}, FakeLauncher()

    def no_models(_models_dir):
        raise SystemExit("os modelos da troca de pessoa não estão no volume")
    summary = sessao.run({**config, "id": "s3"}, state, modal.forward, launcher=fake, prepare=no_models)
    check("U9 sem os modelos a sessão para antes de ligar qualquer coisa e diz o motivo",
          fake.events == ["stop"] and summary["code"] == 1 and "modelos" in state["session"]["stage"]
          and state["session"]["ended"], str(state["session"].get("stage"))[:80])

    import wan_nuvem
    steps = wan_nuvem.image.steps
    kinds = [step[0] for step in steps]
    build = next(step[1][0] for step in steps if step[0] == "run_commands")
    mounted = {step[2]: step[3] for step in steps if step[0] in ("add_local_file", "add_local_dir")}
    check("U10 a imagem: PyTorch de CUDA 13, o instalador sem modelos nem GPU, e o código anexado depois",
          kinds.index("pip_install") < kinds.index("run_commands") and "cu130" in str(steps[kinds.index("pip_install")])
          and all(flag in build for flag in ("SKIP_MODELS=1", "SKIP_GPU_CHECK=1", "KEEP_TORCH=1", "bash vm/setup.sh"))
          and mounted["/wan/vm/setup.sh"] is True and mounted["/wan/app"] is False and mounted["/wan/vm"] is False
          and mounted["/wan/free/launcher.py"] is False and mounted["/wan/nuvem/sessao.py"] is False, build[:100])
    check("U11 o app publica a sessão, o download e o endereço de acordar",
          sorted(wan_nuvem.app.registered_functions) == ["acordar", "baixar", "sessao"]
          and wan_nuvem.app.registered_web_endpoints == ["acordar"]
          and wan_nuvem.app.registered_functions["sessao"].options.get("gpu") == "H100"
          and wan_nuvem.app.registered_functions["sessao"].options.get("max_containers") == 1)

    modal.save("deployed", {"wan-studio": ["acordar", "baixar", "sessao"]})
    first = wan_nuvem.request_session({"gpu": "L40S", "idle_minutes": 5})
    second = wan_nuvem.request_session({"gpu": "L40S", "idle_minutes": 5})
    asked = [call for call in modal.calls() if call["function"] == "sessao"]
    check("U12 pedir a sessão duas vezes seguidas sobe uma só, na placa pedida",
          first["state"] == "requested" and second["state"] == "starting" and len(asked) == 1
          and asked[0]["options"] == {"gpu": "L40S"} and asked[0]["args"][0]["id"] == first["id"]
          and modal.Dict.from_name("wan-studio-estado").get("session")["call"] == asked[0]["id"],
          f"{first['state']}, {second['state']}, {len(asked)} chamada(s)")

    modal.save("dicts/wan-studio-estado", {})
    modal.save("refuse", "Payment method required to run GPU Functions")
    try:
        wan_nuvem.request_session({"gpu": "H100"})
        refused = False
    except modal.exception.Error:
        refused = True
    left = modal.Dict.from_name("wan-studio-estado").get("session") or {}
    check("U13 se o Modal recusa a máquina, não fica uma sessão 'pedida' travando os próximos cliques",
          refused and not sessao.alive(left), str(left)[:100])
    # Zera a nuvem de mentira para o cenario do atalho, que comeca sem nada publicado.
    for name in ("refuse", "deployed", "calls", "dicts/wan-studio-estado"):
        (HOME / f"{name}.json").unlink(missing_ok=True)


# ---------------------------------------------------------------- o atalho, de ponta a ponta

def state():
    try:
        return json.loads((HOME / "dicts" / "wan-studio-estado.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def load(name, default):
    try:
        return json.loads((HOME / f"{name}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def wait_for(condition, seconds=60):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.2)
    return False


def scenario():
    code, out = launcher(FAKE_MODAL_DENY="1")
    check("L1 sem autorização da conta: explica o que fazer (o cartão inclusive) e para",
          code != 0 and "Approve" in out and "cartão" in out and "Sem a autorização" in out and not load("deployed", {}),
          out.strip()[-90:])

    code, out = launcher()
    cli = load("cli", [])
    calls = load("calls", [])
    record = state().get("session") or {}
    check("L2 primeira vez: autoriza, publica, baixa os modelos, liga e avisa que está pronta",
          code == 0 and "Pronto em" in out and [args[:2] for args in cli] == [["token", "new"], ["token", "new"], ["deploy", str(PROJECT / "nuvem" / "wan_nuvem.py")]]
          and [call["function"] for call in calls] == ["baixar", "sessao"], out.strip()[-160:])
    check("L3 o download é acompanhado pelas linhas que interessam (o que baixa e o andamento)",
          "baixando text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors" in out and "ETA:53s" in out
          and "nao interessa" not in out and "Modelos prontos: 5 arquivos" in out)
    session = calls[-1]["args"][0] if calls else {}
    pairing = json.loads(PAIRING.read_text(encoding="utf-8"))
    check("L4 a sessão é pedida na H100, com 5 min de ociosidade e o pareamento do site",
          session.get("gpu") == "H100" and session.get("idle_minutes") == 5 and calls[-1]["options"] == {}
          and session.get("pairing", {}).get("topic") == pairing["topic"], str({k: session.get(k) for k in ("gpu", "idle_minutes")}))
    opened = opener_data()
    wake = state().get("wake") or {}
    check("L5 o site recebe o pareamento, a hora em que ligou e o endereço e a chave para ligar sozinho",
          opened.get("t") == pairing["topic"] and opened.get("k") == pairing["key"] and opened.get("s")
          and opened.get("w") == "https://conta-de-teste--wan-studio-acordar.modal.run"
          and opened.get("wk") == wake.get("key") and len(opened.get("wk", "")) >= 24
          and wake.get("config", {}).get("gpu") == "H100", str(sorted(opened)))
    try:
        status = api(record, "/api/status")
    except (OSError, KeyError, ValueError) as error:
        status = {"erro": str(error)}
    check("L6 a sessão de verdade responde no endereço do túnel: placa, memória, preço por hora e a troca de pessoa instalada",
          status.get("comfy") is True and "H100" in str(status.get("gpu")) and status.get("vram") == 80.0
          and status.get("models", {}).get("animate", {}).get("ok") and status.get("managed") is True
          and status.get("idle", {}).get("limit") == 300 and status.get("price_hour") == 4.4, str(status)[:140])

    code, out = launcher()
    check("L7 ligar de novo com ela no ar: só abre o site, sem publicar nem pedir outra máquina",
          code == 0 and "já está ligada (H100)" in out and len(load("cli", [])) == len(cli)
          and len(load("calls", [])) == len(calls), out.strip()[-80:])

    code, out = launcher("status")
    check("L8 status: ligada, a placa e a conta do crédito do mês",
          code == 0 and "GPU: ligada (H100)" in out and "Crédito deste mês" in out and "de 30" in out, out.strip()[-150:])
    code, out = launcher("sessao")
    try:
        data = json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        data = {}
    check("L9 sessao: endereço e senha em JSON, para os testes na GPU",
          code == 0 and data.get("url") == STUDIO and data.get("password") == record.get("password")
          and "Wan Studio" not in out, out.strip()[:80])
    code, out = launcher("registro")
    check("L10 registro: o que a máquina escreveu", code == 0 and "Wan Studio está no ar" in out, out.strip()[-80:])

    code, out = launcher("desligar")
    ended = state().get("session") or {}
    usage = state().get("usage") or []
    check("L11 desligar: pede pela API da sessão, espera ela sair e mostra o crédito",
          code == 0 and "GPU desligada." in out and ended.get("ended") and ended.get("stage") == "desligada"
          and len(usage) == 1 and usage[0]["seconds"] > 0 and usage[0]["gpu"] == "H100", out.strip()[-120:])
    check("L12 com a sessão encerrada nada fica no ar", wait_for(lambda: not responds(STUDIO + "/"), 20)
          and wait_for(lambda: not responds("http://127.0.0.1:8188/"), 20))
    code, out = launcher("status")
    check("L13 status depois: desligada, com o que foi gasto", code == 0 and "GPU: desligada" in out
          and "US$ 0.0" in out, out.strip()[-150:])
    code, out = launcher("desligar")
    check("L14 desligar com ela já desligada: só avisa", code == 0 and "já está desligada" in out)

    # O site liga a sessao sozinho: o `acordar` usa a configuracao guardada pelo atalho.
    os.environ["FAKE_MODAL_HOME"] = str(HOME)
    for name in ("modal", "wan_nuvem", "sessao"):
        sys.modules.pop(name, None)
    modal = importlib.import_module("modal")
    wan_nuvem = importlib.import_module("wan_nuvem")
    reply = wan_nuvem.request_session(dict(wake["config"]))
    up = wait_for(lambda: (state().get("session") or {}).get("url"), 90)
    check("L15 acordar pelo site: a sessão sobe com a configuração guardada, sem o atalho",
          reply["state"] == "requested" and up and (state()["session"]["id"] == reply["id"]), str(reply))
    code, out = launcher("desligar")
    check("L16 e desliga como as outras", code == 0 and len(state().get("usage") or []) == 2, out.strip()[-60:])
    wait_for(lambda: not responds(STUDIO + "/"), 20)

    code, out = launcher("--ocioso", "0.03", "--gpu", "L40S")
    asked = load("calls", [])[-1]
    gone = wait_for(lambda: (state().get("session") or {}).get("ended"), 60)
    last = (state().get("usage") or [{}])[-1]
    check("L17 outra placa e ociosidade curta: a sessão sobe na L40S e se desliga sozinha",
          code == 0 and asked["options"] == {"gpu": "L40S"} and "Ligando uma L40S (US$ 2.40 por hora" in out
          and gone and last.get("gpu") == "L40S" and last.get("reason") == "desligada", out.strip()[-120:])
    wait_for(lambda: not responds(STUDIO + "/"), 20)

    published = WORK / "casa" / ".publicado.json"
    published.write_text(json.dumps({"hash": "outro", "when": 1}), encoding="utf-8")
    before = len(load("cli", []))
    code, out = launcher("modelos")
    check("L18 código mudado desde a última publicação: publica de novo; modelos já no volume: não baixa",
          code == 0 and len(load("cli", [])) == before + 1 and "já estão no volume" in out
          and json.loads(published.read_text(encoding="utf-8"))["hash"] != "outro", out.strip()[-80:])

    (HOME / "refuse.json").write_text(json.dumps("Payment method required to run GPU Functions"), encoding="utf-8")
    code, out = launcher()
    check("L19 o Modal recusa a máquina (conta sem cartão): diz onde resolver e não deixa sessão pendurada",
          code == 1 and "Payment method required" in out and "modal.com/settings/usage" in out
          and not (state().get("session") or {}).get("url"), out.strip()[-140:])
    (HOME / "refuse.json").unlink()

    (HOME / "falha_sessao").write_text("", encoding="utf-8")
    code, out = launcher()
    check("L20 a sessão para ao ligar: o atalho mostra o motivo e onde ver o registro",
          code == 1 and "A sessão parou ao ligar" in out and "modelos" in out and "registro" in out, out.strip()[-140:])
    (HOME / "falha_sessao").unlink()


def main():
    prepare()
    units()
    cloud = subprocess.Popen([sys.executable, str(HERE / "nuvem_cloud.py")], env=ENV)
    try:
        scenario()
    finally:
        (HOME / "parar").write_text("", encoding="utf-8")
        try:
            cloud.wait(timeout=20)
        except subprocess.TimeoutExpired:
            cloud.kill()
    print(f"\n{len(failures)} falha(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
