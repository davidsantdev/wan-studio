"""Liga o Wan Studio dentro de um notebook gratuito (Kaggle ou Colab).

O notebook desempacota o projeto e chama, em ordem: install(), start() e keepalive().

Como o site acha a sessao: a cada execucao o tunel ganha um endereco novo e uma senha nova.
start() publica os dois, cifrados, num topico do ntfy.sh que so quem tem `pairing.json`
conhece; o site (pareado uma vez pelo codigo de conexao) le o anuncio e conecta sozinho.
"""

import base64
import json
import os
import re
import secrets
import signal
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / "logs"
PAIRING_FILE = Path(__file__).resolve().parent / "pairing.json"
COMFY_PORT = 8188
STUDIO_PORT = 8080
STUDIO_URL = f"http://127.0.0.1:{STUDIO_PORT}"
NTFY_URL = os.environ.get("WAN_NTFY_URL", "https://ntfy.sh").rstrip("/")
CLOUDFLARED = ("https://github.com/cloudflare/cloudflared/releases/latest/download/"
               "cloudflared-linux-amd64")
ANNOUNCE_EVERY = 300
TUNNEL_CHECK_EVERY = 120
REPORT_EVERY = 300
POSIX = os.name == "posix"

SESSION = {"procs": {}, "url": None, "password": None, "site": "", "idle_minutes": 0,
           "announced": 0.0, "announce_failed": False, "workers": 1, "args": {}}


def load_pairing():
    try:
        return json.loads(PAIRING_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


PAIRING = load_pairing()


# ---------------------------------------------------------------- processos

def spawn(name, cmd, cwd, **extra_env):
    LOGS.mkdir(parents=True, exist_ok=True)
    path = LOGS / f"{name}.log"
    if path.exists():  # o registro da execucao anterior fica para quem for investigar a queda
        try:
            path.replace(LOGS / f"{name}.anterior.log")
        except OSError:
            pass
    log = open(path, "w", encoding="utf-8")
    # PYTHONFAULTHANDLER: se o processo morrer por falha nativa (segfault), o Python escreve
    # no registro em que ponto cada thread estava.
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONFAULTHANDLER": "1", **extra_env}
    # Sessao propria: o processo sobrevive a celula e pode ser encerrado com os filhos.
    proc = subprocess.Popen([str(part) for part in cmd], cwd=str(cwd), env=env, stdout=log,
                            stderr=subprocess.STDOUT, start_new_session=POSIX)
    SESSION["procs"][name] = proc
    return proc


def kill(proc):
    if proc.poll() is not None:
        return
    try:
        if POSIX:
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
    except OSError:
        pass


def alive(name):
    proc = SESSION["procs"].get(name)
    return proc is not None and proc.poll() is None


def tail(name, lines=25):
    try:
        text = (LOGS / f"{name}.log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "(sem log)"
    # Sem as barras de progresso, que tomariam todas as linhas.
    kept = [line for line in text.splitlines() if "it/s]" not in line and "s/it]" not in line]
    return "\n".join(kept[-lines:])


def exit_text(name):
    """Como o processo terminou: o codigo de saida ou o sinal que o matou (SIGKILL costuma ser
    falta de memoria RAM; SIGSEGV e SIGABRT, falha em biblioteca nativa)."""
    code = SESSION["procs"][name].returncode if name in SESSION["procs"] else None
    if code is not None and code < 0:
        try:
            return f"sinal {signal.Signals(-code).name}"
        except ValueError:
            return f"sinal {-code}"
    return f"código {code}"


def http_ok(url, password=None, timeout=5):
    request = urllib.request.Request(url, headers={"User-Agent": "wan-studio"})
    if password:
        token = base64.b64encode(f"wan:{password}".encode()).decode()
        request.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read() or b"null")
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- etapas

def internet_problem():
    """None se a sessao tem internet; senao, o ultimo erro de conexao encontrado.

    Um servidor que responde com erro (403, 429...) tambem prova que ha internet: sites
    costumam recusar requisicoes vindas de datacenters, e isso nao e falta de rede.
    """
    problem = None
    for url in ("https://huggingface.co/api/models/Comfy-Org/Wan_2.2_ComfyUI_Repackaged",
                "https://github.com", "https://pypi.org/simple/pip/"):
        request = urllib.request.Request(url, headers={"User-Agent": "wan-studio"})
        try:
            urllib.request.urlopen(request, timeout=15).close()
            return None
        except urllib.error.HTTPError:
            return None
        except OSError as error:
            problem = error
    return problem


def install():
    """Instala o ComfyUI e baixa o conjunto minimo (Wan 2.2 5B). Leva uns 4 minutos no Kaggle."""
    problem = internet_problem()
    if problem is not None:
        raise SystemExit(
            f"Sem internet ({problem}). No Kaggle: Session options > Internet > On "
            "(exige telefone verificado na conta).")

    env = {**os.environ, "MODELS": "free", "PHASE": "core", "NO_VENV": "1",
           "PYTHON": sys.executable}
    proc = subprocess.Popen(["bash", str(ROOT / "vm" / "setup.sh")], env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for line in proc.stdout:
        print(line, end="")
    if proc.wait() != 0:
        raise SystemExit("A instalação falhou: veja a mensagem acima.")
    print("\nInstalação concluída.")


def gpu_count():
    """Quantas GPUs ha: cada uma ganha um ComfyUI, e os videos saem em paralelo. WAN_WORKERS
    fixa o numero (testes, ou para deixar uma GPU de fora)."""
    forced = os.environ.get("WAN_WORKERS")
    if forced:
        return max(1, int(forced))
    try:
        listing = subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return 1
    return max(1, sum(1 for line in listing.stdout.splitlines() if line.startswith("GPU ")))


def worker_name(index):
    return f"comfyui{index or ''}"


def worker_url(index):
    return f"http://127.0.0.1:{COMFY_PORT + index}"


def launch_comfy(index):
    """Inicia o ComfyUI da GPU `index`, sem esperar que responda."""
    command = [sys.executable, "main.py", "--listen", "127.0.0.1", "--port", COMFY_PORT + index]
    env = {}
    if SESSION["workers"] > 1:
        env["CUDA_VISIBLE_DEVICES"] = str(index)
    if index:
        # Os processos dividem a instalacao e os modelos, mas cada um precisa do proprio banco
        # de dados (fica na pasta de usuario) e da propria pasta de temporarios.
        user = ROOT / "ComfyUI" / f"user{index}"
        user.mkdir(parents=True, exist_ok=True)
        command += ["--user-directory", user, "--temp-directory", ROOT / "ComfyUI" / f"temp{index}"]
    extra = (os.environ.get("WAN_COMFY_ARGS", "").split()
             + os.environ.get(f"WAN_COMFY_ARGS_{index}", "").split())
    spawn(worker_name(index), command + extra + SESSION["args"].get(index, []), ROOT / "ComfyUI", **env)


def wait_comfy(index, fatal=True):
    """Espera o ComfyUI `index` responder. Se nao subir: encerra tudo (`fatal`) ou avisa e
    devolve False."""
    name = worker_name(index)
    for _ in range(150):
        if http_ok(f"{worker_url(index)}/system_stats", timeout=3) is not None:
            return True
        if not alive(name):
            break
        time.sleep(2)
    problem = f"O ComfyUI da GPU {index} não subiu:\n" + tail(name)
    if fatal:
        raise SystemExit(problem)
    print(problem)
    return False


def start_comfy():
    """Um ComfyUI por GPU. Sem o primeiro nada funciona; sem os outros, so fica mais lento."""
    SESSION["workers"] = gpu_count()
    for index in range(SESSION["workers"]):
        launch_comfy(index)
    for index in range(SESSION["workers"]):
        if not wait_comfy(index, fatal=index == 0):
            for late in range(index, SESSION["workers"]):
                kill(SESSION["procs"].pop(worker_name(late)))
            SESSION["workers"] = index
            break
    if SESSION["workers"] > 1:
        print(f"{SESSION['workers']} GPUs em uso.")


def enhancer_gpu():
    """A GPU em que o melhorador de prompt vai rodar (a mesma regra de app/enhancer.py), ou
    -1. O servidor precisa saber: o ComfyUI dessa GPU e o melhorador se revezam nela."""
    choice = os.environ.get("ENHANCER_DEVICE", "auto")
    if choice.startswith("cuda:"):
        return int(choice[5:])
    return 1 if choice == "auto" and SESSION["workers"] >= 2 else -1


def start_studio():
    spawn("studio", [sys.executable, "server.py"], ROOT / "app",
          APP_PASSWORD=SESSION["password"], IDLE_MINUTES=str(SESSION["idle_minutes"]),
          WAN_MANAGED="1", PORT=str(STUDIO_PORT), ENHANCER_WORKER=str(enhancer_gpu()),
          COMFY_DIR=str(ROOT / "ComfyUI"),
          COMFY_URLS=",".join(worker_url(index) for index in range(SESSION["workers"])))
    for _ in range(30):
        if http_ok(f"{STUDIO_URL}/api/presets", SESSION["password"], timeout=3) is not None:
            return
        if not alive("studio"):
            raise SystemExit("O Wan Studio parou ao iniciar:\n" + tail("studio"))
        time.sleep(1)
    raise SystemExit("O Wan Studio não respondeu:\n" + tail("studio"))


def start_extras():
    """Baixa em segundo plano o que nao e essencial; o estudio ja pode ser usado."""
    if os.environ.get("WAN_SKIP_EXTRAS"):
        return
    spawn("extras", ["bash", ROOT / "vm" / "setup.sh"], ROOT, MODELS="free", PHASE="extras",
          NO_VENV="1", PYTHON=sys.executable)


def start_tunnel():
    """Abre o tunel publico e devolve o endereco https."""
    override = os.environ.get("WAN_TUNNEL_CMD")  # so para testes
    if override:
        cmd, pattern = json.loads(override), r"https?://[^\s\"']+"
    else:
        binary = ROOT / "cloudflared"
        if not binary.exists():
            urllib.request.urlretrieve(CLOUDFLARED, binary)
            binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
        cmd = [binary, "tunnel", "--no-autoupdate", "--url", STUDIO_URL]
        pattern = r"https://[a-z0-9-]+\.trycloudflare\.com"

    spawn("tunnel", cmd, ROOT)
    for _ in range(90):
        found = re.search(pattern, (LOGS / "tunnel.log").read_text(encoding="utf-8", errors="replace"))
        if found:
            SESSION["url"] = found.group(0).rstrip("/")
            return SESSION["url"]
        if not alive("tunnel"):
            break
        time.sleep(1)
    raise SystemExit("O túnel não abriu:\n" + tail("tunnel"))


# ---------------------------------------------------------------- anuncio

def connection_code():
    data = {"u": SESSION["url"], "p": SESSION["password"]}
    if PAIRING.get("topic") and PAIRING.get("key"):
        data.update(t=PAIRING["topic"], k=PAIRING["key"])
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")


def encrypt(payload):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = base64.urlsafe_b64decode(PAIRING["key"] + "=" * (-len(PAIRING["key"]) % 4))
    nonce = secrets.token_bytes(12)
    sealed = AESGCM(key).encrypt(nonce, json.dumps(payload).encode(), None)
    return base64.urlsafe_b64encode(nonce + sealed).decode()


def announce(off=False):
    """Publica onde a sessao esta (ou que acabou). Falhar aqui nao impede o uso por codigo."""
    SESSION["announced"] = time.time()
    if not (PAIRING.get("topic") and PAIRING.get("key")):
        return False
    payload = {"u": SESSION["url"], "p": SESSION["password"], "ts": int(time.time()), "off": off}
    try:
        request = urllib.request.Request(
            f"{NTFY_URL}/{PAIRING['topic']}", data=encrypt(payload).encode(), method="POST",
            headers={"User-Agent": "wan-studio"})
        urllib.request.urlopen(request, timeout=15).close()
    except Exception as exc:  # rede, servico fora do ar, biblioteca ausente
        if not SESSION["announce_failed"]:
            print(f"(aviso: não consegui anunciar a sessão para o site: {exc}. "
                  "Use o código de conexão.)")
        SESSION["announce_failed"] = True
        return False
    SESSION["announce_failed"] = False
    return True


def banner():
    code = connection_code()
    site = SESSION["site"]
    line = "=" * 72
    print("\n" + line)
    print("  Wan Studio está no ar.\n")
    if site:
        print("  Abra este link (ele já conecta o site a esta sessão):")
        print(f"    {site.rstrip('/')}/#c={code}\n")
    print("  Código de conexão (cole no site se ele não conectar sozinho):")
    print(f"    {code}\n")
    print("  Acesso direto, sem o site:")
    print(f"    {SESSION['url']}   usuário: wan   senha: {SESSION['password']}")
    print(line + "\n")


def start(site_url="", idle_minutes=60):
    """Sobe ComfyUI, Wan Studio e tunel, e mostra como conectar."""
    stop(quiet=True)
    SESSION.update(password=secrets.token_urlsafe(12), site=site_url or PAIRING.get("site", ""),
                   idle_minutes=idle_minutes, url=None)
    print("Iniciando o ComfyUI...")
    start_comfy()
    start_studio()
    start_extras()
    print("Abrindo o túnel...")
    start_tunnel()
    announce()
    banner()


def restart_worker(request):
    """Reinicia um ComfyUI com outras opcoes de linha de comando, a pedido do servidor
    (POST /api/worker/N/restart). Serve para medir na GPU o que cada opcao rende."""
    index, args = int(request["worker"]), [str(arg) for arg in request.get("args", [])]
    if not 0 <= index < SESSION["workers"]:
        return
    print(f"Reiniciando o ComfyUI da GPU {index} com: {' '.join(args) or '(sem opções)'}")
    proc = SESSION["procs"][worker_name(index)]
    kill(proc)
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
    SESSION["args"][index] = args
    launch_comfy(index)
    wait_comfy(index, fatal=False)
    token = base64.b64encode(f"wan:{SESSION['password']}".encode()).decode()
    confirm = urllib.request.Request(
        f"{STUDIO_URL}/api/worker/{index}/restart", data=b'{"done": true}', method="POST",
        headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(confirm, timeout=10).close()
    except OSError as error:
        print(f"(aviso: não consegui confirmar o reinício ao servidor: {error})")


def stop(reason="", quiet=False):
    if SESSION["url"]:
        announce(off=True)
    for proc in SESSION["procs"].values():
        kill(proc)
    SESSION["procs"].clear()
    SESSION["url"] = None
    if not quiet:
        print(f"\nSessão encerrada{': ' + reason if reason else ''}.")
        if os.path.isdir("/kaggle"):
            print("Se esta sessão for interativa, desligue-a no Kaggle (botão de energia) "
                  "para não gastar cota.")


def keepalive():
    """Mantem a sessao viva: religa o que cair, reanuncia e desliga por ociosidade."""
    if not SESSION["url"]:
        raise SystemExit("Rode a célula anterior primeiro.")
    last_report = last_tunnel_check = time.time()
    tunnel_misses = 0
    extras_reported = False
    while True:
        time.sleep(int(os.environ.get("WAN_KEEPALIVE_STEP", "20")))

        for index in range(SESSION["workers"]):
            if not alive(worker_name(index)):
                print(f"O ComfyUI da GPU {index} caiu ({exit_text(worker_name(index))}); reiniciando...\n"
                      + tail(worker_name(index), 40))
                launch_comfy(index)
                wait_comfy(index, fatal=False)
        if not alive("studio"):
            print("O Wan Studio caiu; reiniciando...\n" + tail("studio", 8))
            start_studio()

        if time.time() - last_tunnel_check > TUNNEL_CHECK_EVERY:
            last_tunnel_check = time.time()
            reachable = http_ok(f"{SESSION['url']}/api/presets", SESSION["password"], timeout=15)
            tunnel_misses = 0 if reachable is not None else tunnel_misses + 1
        if not alive("tunnel") or tunnel_misses >= 3:
            print("O túnel caiu; abrindo outro...")
            if "tunnel" in SESSION["procs"]:
                kill(SESSION["procs"]["tunnel"])
            tunnel_misses = 0
            start_tunnel()
            announce()
            banner()

        extras = SESSION["procs"].get("extras")
        if extras and extras.poll() is not None and not extras_reported:
            extras_reported = True
            print("Downloads extras: " + tail("extras", 6).replace("\n", "\n  "))

        info = http_ok(f"{STUDIO_URL}/api/status", SESSION["password"])
        if info:
            idle = info["idle"]
            if info.get("shutdown"):
                return stop("desligada pelo site")
            if info.get("restart"):
                restart_worker(info["restart"])
            if idle["limit"] and not idle["busy"] and idle["seconds"] >= idle["limit"]:
                return stop(f"{idle['limit'] // 60} min sem uso")

        if time.time() - SESSION["announced"] > ANNOUNCE_EVERY:
            announce()
        if time.time() - last_report > REPORT_EVERY:
            last_report = time.time()
            state = "em uso" if info and info["idle"]["busy"] else "ociosa"
            print(time.strftime("%H:%M"), "no ar ·", state, "·", SESSION["url"])
