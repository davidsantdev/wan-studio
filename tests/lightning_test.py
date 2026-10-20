"""Testa lightning/ligar.py e lightning/boot.py contra uma API do Lightning AI simulada (so
biblioteca padrao).

  python tests/lightning_test.py

O simulador responde nas rotas e com os campos do cliente oficial (lightning-sdk 2026.10.1):
/v1/auth/user, /v1/memberships, /v1/core/accelerators e, em /v1/projects/<id>/cloudspaces,
criar, codestatus, start, stop, keep-alive e execute (com a saida em linhas JSON). Os comandos
que o script manda para a "maquina" sao executados de verdade, por um bash, numa pasta
temporaria: o envio do pacote em pedacos, a conferencia e o desempacotamento sao os reais. So
o que depende de GPU e simulado. Nada sai para a internet e o navegador nao e aberto.
Precisa de um bash (no Windows, o do Git).
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
LIGHTNING = ROOT / "lightning"
FREE = ROOT / "free"
WORK = Path(__file__).resolve().parent / ".work" / "lightning"
HOME = WORK / "home"                 # a "maquina": a pasta do usuario dentro do Studio
USER_ID, API_KEY = "usuario-de-teste-0001", "chave-de-teste-0001"
GOOD = "Basic " + base64.b64encode(f"{USER_ID}:{API_KEY}".encode()).decode()
ALONE = f"Bearer {API_KEY}"          # a chave pessoal sem o id do usuario
ORG_KEY = "chave-da-organizacao/0001=="  # uma chave da pagina Keys: sem usuario, presa a um espaco
SCOPED = f"Bearer {ORG_KEY}"
PYTHON = sys.executable.replace("\\", "/")
# "Navegadores" de mentira, pela variavel BROWSER que o Python respeita: um que segue o
# redirecionamento do login ate o servidor local do script (um arquivo, porque a variavel e
# separada por ";" no Windows), e um que nao faz nada.
FAKE_BROWSER = WORK / "navegador.py"
BROWSER_OK = f"{PYTHON} {FAKE_BROWSER.as_posix()} %s"
BROWSER_DEAD = f"{PYTHON} -c pass %s"
PROJECT, SPACE = "proj-1", "cs-1"
BASE = f"/v1/projects/{PROJECT}/cloudspaces"
PHASE = "CLOUD_SPACE_INSTANCE_STATE_"
GIT_BASH = Path(r"C:\Program Files\Git\bin\bash.exe")
BASH = str(GIT_BASH) if GIT_BASH.exists() else shutil.which("bash")
failures = []

INSTALLING = ["Modelos no disco do Studio (até 95 GB): ficam guardados entre as sessões.", "NVIDIA L40S, 46068 MiB",
              "baixando text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors"]
BROKEN = ["SystemExit: A instalação falhou: veja a mensagem acima."]
# A lista de placas da nuvem em que o Studio de teste fica, no formato do Lightning de verdade
# (conferido na rota publica em 12/10/2026): o identificador de algumas traz a memoria.
ACCELERATORS = [
    {"slugMultiCloud": "lit-l40s-1", "family": "L40S", "resources": {"gpu": 1}, "cost": 2.14, "spotPrice": 0, "enabled": True},
    {"slugMultiCloud": "lit-l4-1", "family": "L4", "resources": {"gpu": 1}, "cost": 0.48, "spotPrice": 0.31, "enabled": True},
    {"slugMultiCloud": "lit-t4-1", "family": "T4", "resources": {"gpu": 1}, "cost": 0.19, "spotPrice": 0.11, "enabled": True},
    {"slugMultiCloud": "lit-l4-4", "family": "L4", "resources": {"gpu": 4}, "cost": 1.9, "enabled": True},
    {"slugMultiCloud": "lit-a100-80gb-1", "family": "A100", "resources": {"gpu": 1}, "cost": 5.68, "enabled": True},
    {"slugMultiCloud": "lit-a100-40gb-1", "family": "A100", "resources": {"gpu": 1}, "cost": 2.19, "enabled": True},
    {"slugMultiCloud": "lit-h100-80gb-1", "family": "H100", "resources": {"gpu": 1}, "cost": 5.2, "enabled": True,
     "isTierRestricted": True},
]
# A da nuvem padrao (AWS), em que a mesma placa e mais cara: o script tem de comparar as nuvens
# e ficar com a mais barata.
DEFAULT_CLOUD = [{"slugMultiCloud": "lit-l40s-1", "family": "L40S", "resources": {"gpu": 1}, "cost": 3.54, "enabled": True}]
# Um Studio que ja existe na nuvem mais barata (um Studio nao muda de nuvem: ha um por nuvem).
NEBIUS_STUDIO = {"id": SPACE, "name": "wan-studio-nebius", "clusterId": "cluster-1"}
# Estado do Lightning de mentira; cada cenario ajusta antes de rodar o script.
cloud = {}


def reset(**changes):
    cloud.update(studio=None, phase="STOPPED", machine="", pending=0, requests=[], commands=[], sessions={},
                 polls={}, booted=[], alive=False, gpu=True, log=INSTALLING, start_error="", drop_chunk=False,
                 outage=0, accelerators=ACCELERATORS, default=DEFAULT_CLOUD, free_gb=100, dies=False)
    if cloud["studio"] is NEBIUS_STUDIO:
        cloud["studio"] = dict(NEBIUS_STUDIO)
    cloud.update(changes)


def run_shell(command):
    """Roda de verdade, na pasta temporaria, o comando que a maquina receberia."""
    result = subprocess.run([BASH, "-c", command], capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=120, env={**os.environ, "HOME": HOME.as_posix()})
    return result.stdout


def machine_runs(command):
    """A saida do comando na "maquina". O que depende de GPU ou de processos de Linux e
    simulado; o resto roda num bash de verdade."""
    mark = command.rsplit("echo ", 1)[1].replace("$?", "")
    if "nvidia-smi" in command:
        return ("GPU 0: NVIDIA L40S (UUID: GPU-0000)\n" if cloud["gpu"] else "SEM_GPU\n") + mark + "0\n"
    if "boot.pid" in command:
        return ("SIM\n" if cloud["alive"] else "NAO\n") + mark + "0\n"
    if "lightning/boot.py" in command:
        cloud["booted"].append(command)
        cloud["alive"] = not cloud["dies"]
        logs = HOME / "wan-studio" / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "boot.log").write_text("\n".join(cloud["log"]) + "\n", encoding="utf-8", newline="\n")
        return "ligando\n" + mark + "0\n"
    if cloud["drop_chunk"] and "printf %s" in command:  # um pedaco do pacote se perde no caminho
        cloud["drop_chunk"] = False
        return mark + "0\n"
    return run_shell(command)


class FakeLightning(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, code, body, lines=False):
        data = ("\n".join(json.dumps(item) for item in body) + "\n" if lines else json.dumps(body)).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def refuse(self, code, message):
        self.reply(code, {"code": code, "message": message, "details": []})

    def handle_request(self, method):
        url = urlparse(self.path)
        query = {key: values[0] for key, values in parse_qs(url.query).items()}
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}") if length else None
        auth = self.headers.get("Authorization", "")
        route = (method, url.path)
        if route == ("GET", "/sign-in"):  # a pagina de login: devolve o navegador ao script, com a credencial
            target = f"{query['redirectTo']}?token=jwt-de-teste&key={API_KEY}&userID={USER_ID}"
            self.send_response(302)
            self.send_header("Location", target)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        cloud["requests"].append((method, url.path, auth, body))
        if auth not in (GOOD, ALONE, SCOPED):
            return self.refuse(401, "Unauthenticated")
        scoped = auth == SCOPED

        if route == ("GET", "/v1/auth/whoami"):
            return self.reply(200, {"apiKeyId": "key-1", "orgId": "org-1", "projectId": PROJECT, "authType": "API_KEY"}
                              if scoped else {"userId": USER_ID, "username": "maria", "authType": "USER"})
        if route == ("GET", "/v1/auth/user"):
            return self.refuse(403, "permission denied") if scoped else self.reply(200, {"id": USER_ID, "username": "maria"})
        if route == ("GET", "/v1/memberships") and scoped and query.get("organizationId") != "org-1":
            return self.refuse(403, "permission denied")  # uma chave sem usuario nao tem "os meus"
        if route == ("GET", "/v1/memberships") and scoped:
            return self.reply(200, {"memberships": [
                {"projectId": PROJECT, "name": "default-project", "ownerType": "OWNER_TYPE_ORGANIZATION", "ownerId": "org-1",
                 "freeStorageBytes": str(cloud["free_gb"] * 2**30), "currentStorageBytes": str(12 * 2**30)}]})
        if route == ("GET", "/v1/memberships"):
            return self.reply(200, {"memberships": [
                {"projectId": "proj-0", "name": "outro", "isDefault": False},
                {"projectId": PROJECT, "name": "default-project", "isDefault": True, "balance": 0,
                 "ownerType": "OWNER_TYPE_ORGANIZATION", "ownerId": "org-1",
                 "freeStorageBytes": str(cloud["free_gb"] * 2**30), "currentStorageBytes": str(12 * 2**30),
                 "nextFreeCreditsGrant": "2026-11-01T00:00:00Z"}]} if query.get("filterByUserId") == "true" else {})
        # Como numa conta nova de verdade: o espaco de trabalho e de uma organizacao, e e nela
        # que os creditos estao.
        if route == ("GET", f"/v1/projects/{PROJECT}/billing/balance"):
            return self.reply(200, {"projectId": PROJECT, "balance": 0})
        if route == ("GET", "/v1/orgs/org-1/billing/balance"):
            return self.reply(200, {"balance": 14.25})
        if route == ("GET", "/v1/users/billing/balance"):
            return self.reply(200, {"balance": 0, "totalSpent": 0})
        if route == ("GET", f"/v1/projects/{PROJECT}/clusters"):
            return self.reply(200, {"clusters": []})  # as nuvens proprias da conta: nenhuma
        if route == ("GET", "/v1/core/clusters"):
            return self.reply(200, {"clusters": [{"id": "cluster-0", "spec": {"driver": "AWS"}},
                                                 {"id": "cluster-1", "spec": {"driver": "NEBIUS"}},
                                                 # sem maquinas proprias: repete a lista da padrao
                                                 {"id": "cluster-2", "spec": {"driver": "CLOUDFLARE"}}]})
        if route == ("GET", "/v1/core/accelerators"):
            listing = cloud["accelerators"] if query.get("cloudProvider") == "NEBIUS" else cloud["default"]
            return self.reply(200, {"accelerator": listing} if query.get("projectId") == PROJECT else {})
        if route == ("GET", BASE):
            found = [cloud["studio"]] if cloud["studio"] and query.get("name") in (None, cloud["studio"]["name"]) else []
            # Como numa conta de verdade: ha outros Studios, que nao sao do Wan Studio.
            return self.reply(200, {"cloudspaces": [{"id": "cs-0", "name": "scratch-studio-devbox", "clusterId": "cluster-0"}] + found})
        if route == ("POST", BASE):
            cloud["studio"] = {"id": SPACE, "name": body["name"], "displayName": body.get("displayName"),
                               "clusterId": body.get("clusterId") or "cluster-0", "projectId": PROJECT}
            return self.reply(200, cloud["studio"])
        if not url.path.startswith(f"{BASE}/{SPACE}/") or cloud["studio"] is None:
            return self.refuse(404, "Not found")
        action = url.path[len(f"{BASE}/{SPACE}/"):]

        if route == ("POST", f"{BASE}/{SPACE}/runs"):
            return self.reply(200, {"id": "run-1"})
        if (method, action) == ("GET", "codestatus"):
            if cloud["outage"] > 0:  # o Lightning (ou a rede) falha por algumas consultas
                cloud["outage"] -= 1
                return self.refuse(503, "Service Unavailable")
            if cloud["phase"] == "PENDING":
                cloud["pending"] -= 1
                if cloud["pending"] <= 0:
                    cloud["phase"] = "RUNNING"
            elif cloud["phase"] == "STOPPING":
                cloud["phase"] = "STOPPED"
            if cloud["phase"] == "STOPPED":
                return self.reply(200, {})  # como a API real: sem maquina, nada em "inUse"
            status = {"phase": PHASE + cloud["phase"], "computeConfig": {"name": cloud["machine"], "spot": False},
                      "price": 2.14, "startupStatus": {"topUpRestoreFinished": cloud["phase"] == "RUNNING"}}
            return self.reply(200, {"inUse": status})
        if (method, action) == ("POST", "start"):
            if scoped:  # a chave da organizacao le, mas nao liga maquinas
                return self.refuse(403, "permission denied")
            if cloud["start_error"]:
                return self.refuse(400, cloud["start_error"])
            cloud.update(phase="PENDING", pending=2, machine=body["computeConfig"]["name"], alive=False)
            return self.reply(200, {})
        if (method, action) == ("POST", "stop"):
            cloud.update(phase="STOPPING", alive=False)
            return self.reply(200, {})
        if (method, action) == ("POST", "keep-alive"):
            return self.reply(200, {})
        if (method, action) == ("POST", "execute"):
            if cloud["phase"] != "RUNNING":
                return self.refuse(400, "cloudspace is not running")
            name = f"sessao-{len(cloud['sessions']) + 1}"
            cloud["commands"].append(body["command"])
            cloud["sessions"][name] = machine_runs(body["command"]) if body.get("detached") else ""
            return self.reply(200, {"sessionName": name})
        if method == "GET" and action.startswith("execute/") and action.endswith("/stream"):
            name = action.split("/")[1]
            if name not in cloud["sessions"]:
                return self.refuse(404, "session not found")
            cloud["polls"][name] = cloud["polls"].get(name, 0) + 1
            if cloud["polls"][name] == 1:  # na primeira consulta o comando ainda esta rodando
                return self.reply(200, [{"result": {"output": "", "exitCode": -1}}], lines=True)
            output = cloud["sessions"][name]
            half = len(output) // 2  # a saida chega em mais de uma linha
            return self.reply(200, [{"result": {"output": output[:half], "exitCode": 0}},
                                    {"result": {"output": output[half:], "exitCode": 0}}], lines=True)
        return self.refuse(404, "Not found")

    def do_GET(self):
        self.handle_request("GET")

    def do_POST(self):
        self.handle_request("POST")


def check(label, condition, detail=""):
    print(("ok    " if condition else "FALHA ") + label + (f"  [{detail}]" if detail else ""), flush=True)
    if not condition:
        failures.append(label)


def environment(keys=True, home=None):
    env = {**os.environ, "LIGHTNING_CLOUD_URL": f"http://127.0.0.1:{PORT}", "WAN_POLL_SECONDS": "0.1",
           "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1", "WAN_LOGIN_SECONDS": "20",
           "USERPROFILE": str(home or EMPTY_HOME), "HOME": str(home or EMPTY_HOME)}
    for name in ("LIGHTNING_USER_ID", "LIGHTNING_API_KEY", "LIGHTNING_CLOUD_PROJECT_ID", "LIGHTNING_CLOUD_SPACE_ID",
                 "WAN_PERSIST_GB", "WAN_KEEP_ON", "MODELS_DIR", "DISK_BUDGET_GB", "BROWSER"):
        env.pop(name, None)
    if keys == "org":
        env.update(LIGHTNING_API_KEY=ORG_KEY)
    elif keys:
        env.update(LIGHTNING_USER_ID=USER_ID, LIGHTNING_API_KEY=API_KEY)
    return env


def run(*args, keys=True, typed="", home=None, browser=None, **extra):
    """Roda o script com a API simulada. `home` isola as credenciais da maquina; `browser` e o
    comando que faz as vezes de navegador (sem ele, o script roda com --sem-navegador)."""
    env = {**environment(keys, home), **extra, **({"BROWSER": browser} if browser else {})}
    result = subprocess.run([sys.executable, str(LIGHTNING / "ligar.py"), *args, *([] if browser else ["--sem-navegador"])],
                            env=env, capture_output=True, text=True, encoding="utf-8", input=typed, timeout=300)
    return result.returncode, result.stdout + result.stderr


def sent(method, action):
    return [body for verb, path, _, body in cloud["requests"] if verb == method and path == f"{BASE}/{SPACE}/{action}"]


def opener_data():
    """O que a pagina de abertura entrega ao site (o codigo depois de "#c=")."""
    opener = (FREE / "abrir.html").read_text(encoding="utf-8") if (FREE / "abrir.html").exists() else ""
    if "#c=" not in opener:
        return {}
    return json.loads(base64.urlsafe_b64decode(opener.split("#c=")[1].split('"')[0] + "=="))


def last(out, count=1):
    return " | ".join(line.strip() for line in out.strip().splitlines()[-count:])[:160]


def scenarios():
    installed = HOME / "wan-studio"

    # ---- 1. primeira vez: a conta ainda nao tem o Studio
    reset()
    code, out = run("--gpu", "L40S")
    check("L1a liga e termina bem", code == 0 and "Pronto." in out, last(out, 3))
    check("L1b mostra a conta, o saldo e o disco", "Conta: maria" in out and "Créditos: 14.25" in out
          and "recarga gratuita em 2026-11-01" in out and "12 GB usados de 100 GB" in out, last(out.split("Ligando")[0], 3))
    created = [body for verb, path, _, body in cloud["requests"] if (verb, path) == ("POST", BASE)]
    check("L1c cria o Studio na nuvem em que a placa é mais barata, e a primeira execução dele",
          len(created) == 1 and created[0]["name"] == "wan-studio-nebius" and created[0].get("clusterId") == "cluster-1"
          and sent("POST", "runs") == [{"clusterId": "cluster-1", "localSource": True}], str(created)[:120])
    check("L1d liga na placa pedida, sem máquina interrompível, com o preço da nuvem do Studio",
          sent("POST", "start") == [{"computeConfig": {"name": "lit-l40s-1", "spot": False}}] and "2.14 créditos por hora" in out
          and "3.54" not in out, str(sent("POST", "start")))
    same = [name for name in ("app/server.py", "app/static/app.js", "vm/setup.sh", "vm/nodes/wan_studio/__init__.py",
                              "free/launcher.py", "free/pairing.json", "lightning/boot.py")
            if (installed / name).exists() and (installed / name).read_bytes() == (ROOT / name).read_bytes()]
    leftovers = [path.name for path in installed.iterdir() if path.name.startswith("pacote")] if installed.exists() else ["?"]
    check("L1e o pacote chega inteiro à máquina, em pedaços, e é desempacotado", len(same) == 7 and not leftovers
          and sum("printf %s" in command for command in cloud["commands"]) >= 2, f"{len(same)} arquivos iguais; sobras {leftovers}")
    boot = cloud["booted"][0] if cloud["booted"] else ""
    check("L1f inicia o Wan Studio solto do comando, com o disco e os ids, sem a chave da conta",
          len(cloud["booted"]) == 1 and "WAN_PERSIST_GB=95 " in boot and f"LIGHTNING_CLOUD_PROJECT_ID={PROJECT} " in boot
          and f"LIGHTNING_CLOUD_SPACE_ID={SPACE} " in boot and "$S nohup" in boot and "WAN_IDLE_MINUTES=15 " in boot
          and not any(API_KEY in command for command in cloud["commands"]), boot[-150:])
    pairing = json.loads((FREE / "pairing.json").read_text(encoding="utf-8"))
    data = opener_data()
    check("L1g página de abertura leva ao site já pareado", data.get("t") == pairing["topic"] and data.get("k") == pairing["key"]
          and isinstance(data.get("s"), int) and "u" not in data)
    check("L1h toda chamada vai autenticada", bool(cloud["requests"]) and all(auth == GOOD for _, _, auth, _ in cloud["requests"]))

    # ---- 2. ja ligada e com o Wan Studio rodando: nao liga nem envia nada de novo
    (FREE / "abrir.html").unlink(missing_ok=True)
    cloud.update(requests=[], commands=[], booted=[])
    code, out = run()
    check("L2 já ligada: só abre o site", code == 0 and "já está ligada (L40S)" in out and not sent("POST", "start")
          and not cloud["booted"] and isinstance(opener_data().get("s"), int), last(out))

    # ---- 3. ligada, mas o Wan Studio nao esta rodando (caiu): reenvia e inicia, sem religar a maquina
    cloud.update(requests=[], commands=[], booted=[], alive=False)
    code, out = run()
    check("L3 máquina ligada sem o Wan Studio: reenvia e inicia", code == 0 and not sent("POST", "start")
          and len(cloud["booted"]) == 1 and "Pronto." in out, last(out))

    # ---- 4. sem escolher a placa e sem terminal: a primeira disponivel na ordem de preferencia
    reset(studio=NEBIUS_STUDIO, default=[],
          accelerators=[{**item, "outOfCapacity": item["family"] == "L40S"} for item in ACCELERATORS])
    code, out = run()
    check("L4 sem escolha: a L40S está sem máquinas, liga a L4", code == 0
          and sent("POST", "start") == [{"computeConfig": {"name": "lit-l4-1", "spot": False}}]
          and "placa L4 (0.48 créditos por hora)" in out, last(out.split("Enviando")[0]))

    # ---- 4a. a placa so tem vaga na nuvem mais cara: vai nela, num Studio dessa nuvem
    reset(studio=NEBIUS_STUDIO, accelerators=[{**item, "outOfCapacity": item["family"] == "L40S"} for item in ACCELERATORS])
    code, out = run("--gpu", "L40S")
    created = [body for verb, path, _, body in cloud["requests"] if (verb, path) == ("POST", BASE)]
    check("L4a placa sem vaga na nuvem barata: liga na outra, com o preço dela, num Studio de lá", code == 0
          and "placa L40S (3.54 créditos por hora)" in out and len(created) == 1 and created[0]["name"] == "wan-studio"
          and created[0].get("clusterId") == "cluster-0", f"{[item.get('name') for item in created]} {last(out.split('Enviando')[0])}")

    # ---- 4b. identificador que traz a memoria, e a maquina interrompivel
    reset(studio=NEBIUS_STUDIO)
    code, out = run("--gpu", "A100")
    check("L4b A100: usa o identificador que a nuvem dá, da mais barata entre as de uma placa", code == 0
          and sent("POST", "start") == [{"computeConfig": {"name": "lit-a100-40gb-1", "spot": False}}]
          and "placa A100 (2.19 créditos por hora)" in out, str(sent("POST", "start")))
    code, out = run("status")
    check("L4c status reconhece a placa pelo identificador", "GPU: ligada (A100" in out, last(out))
    reset(studio=NEBIUS_STUDIO)
    code, out = run("--gpu", "L4", "--interrompivel")
    check("L4d interrompível a pedido: máquina e preço da versão interrompível", code == 0
          and sent("POST", "start") == [{"computeConfig": {"name": "lit-l4-1", "spot": True}}]
          and "placa L4 (0.31 créditos por hora)" in out, str(sent("POST", "start")))
    reset(studio=NEBIUS_STUDIO)
    code, out = run("--gpu", "L40S", "--interrompivel")
    check("L4e placa sem versão interrompível: explica e não liga", code == 1 and "sem versão interrompível" in out
          and not sent("POST", "start"), last(out))

    # ---- 5. placa pedida que o plano nao inclui
    reset(studio=NEBIUS_STUDIO)
    code, out = run("--gpu", "H100")
    check("L5 placa fora do plano: explica e não liga", code == 1 and "não incluída no seu plano" in out
          and not sent("POST", "start"), last(out))
    code, out = run("--gpu", "RTX9090")
    check("L5b placa que não existe: lista as opções", code != 0 and "L40S, L4, T4, RTX6000, A100, H100" in out
          and not sent("POST", "start"), last(out))

    # ---- 6. o Lightning recusa ligar (ex.: creditos insuficientes)
    reset(studio=NEBIUS_STUDIO, start_error="insufficient balance to start")
    code, out = run("--gpu", "L4")
    check("L6 recusa do Lightning é mostrada, com o que conferir", code == 1 and "insufficient balance" in out
          and "saldo de créditos" in out and not cloud["booted"], last(out, 2))

    # ---- 7. comando status: nao liga nada
    reset(studio=NEBIUS_STUDIO)
    code, out = run("status")
    check("L7 status com a máquina desligada", code == 0 and "GPU: desligada." in out and "Créditos: 14.25" in out
          and not sent("POST", "start"), last(out))
    cloud.update(phase="RUNNING", machine="lit-l40s-1")
    code, out = run("status")
    check("L7b status com a máquina ligada: placa e preço", code == 0 and "GPU: ligada (L40S, 2.14 créditos por hora)." in out, last(out))
    reset()
    code, out = run("status")
    check("L7c status numa conta que nunca ligou", code == 0 and "nunca ligada por aqui" in out
          and not [1 for verb, path, _, _ in cloud["requests"] if verb == "POST"], last(out))

    # ---- 8. comando registro
    reset()
    run("--gpu", "L40S")
    code, out = run("registro")
    check("L8 registro mostra o que a máquina está fazendo", code == 0 and "NVIDIA L40S, 46068 MiB" in out
          and "baixando text_encoders" in out, last(out))

    # ---- 9. comando desligar
    cloud.update(requests=[])
    code, out = run("desligar")
    check("L9 desligar para a máquina e confirma", code == 0 and sent("POST", "stop") == [{}] and "GPU desligada." in out, last(out))
    cloud.update(requests=[])
    code, out = run("desligar")
    check("L9b desligar de novo: nada a fazer", code == 0 and "já está desligada" in out and not sent("POST", "stop"), last(out))
    code, out = run("registro")
    check("L9c registro com a máquina desligada", code == 0 and "está desligada" in out, last(out))

    # ---- 10. a instalacao para logo no comeco
    (FREE / "abrir.html").unlink(missing_ok=True)
    reset(log=BROKEN)
    code, out = run("--gpu", "L4")
    check("L10 instalação que falha: avisa o motivo, mostra o registro e não abre o site",
          code == 1 and "parou logo no começo: A instalação falhou" in out and "Fim do registro da máquina" in out
          and "se desliga sozinha" in out and not sent("POST", "stop") and "Pronto." not in out
          and not (FREE / "abrir.html").exists(), last(out, 3))

    # ---- 10b. o iniciador nao sobe (ex.: sem Python na maquina): nao diz que deu certo
    (FREE / "abrir.html").unlink(missing_ok=True)
    reset(log=["nohup: failed to run command 'python': No such file or directory"], dies=True)
    code, out = run("--gpu", "L4")
    check("L10b iniciador que nao sobe: mostra o registro, desliga a maquina e nao abre o site", code == 1
          and "não iniciou na máquina (nohup: failed to run command" in out and sent("POST", "stop") == [{}]
          and "Desliguei a máquina" in out and not (FREE / "abrir.html").exists(), last(out, 3))

    # ---- 10c. a maquina se desliga antes de o script conseguir ler o registro
    (FREE / "abrir.html").unlink(missing_ok=True)
    reset()
    original = FakeLightning.handle_request

    def vanishing(self, method):  # assim que o Wan Studio e iniciado, a maquina some
        if cloud["booted"] and cloud["phase"] == "RUNNING":
            cloud["phase"] = "STOPPED"
        return original(self, method)
    FakeLightning.handle_request = vanishing
    try:
        code, out = run("--gpu", "L4")
    finally:
        FakeLightning.handle_request = original
    check("L10c maquina que se desliga logo depois de ligar: nao diz que deu certo", code == 1
          and "se desligou logo depois de ligar" in out and "Pronto." not in out and not (FREE / "abrir.html").exists(), last(out, 3))

    # ---- 11. Studio ligado pelo site do Lightning numa maquina sem placa de video
    reset(studio=NEBIUS_STUDIO, phase="RUNNING", machine="cpu-4", gpu=False)
    code, out = run()
    check("L11 máquina sem GPU: explica como resolver", code == 1 and "sem placa de vídeo" in out and "desligar" in out
          and not cloud["booted"], last(out, 2))

    # ---- 12. um pedaco do pacote se perde: a conferencia barra antes de desempacotar
    reset(drop_chunk=True)
    code, out = run("--gpu", "L4")
    check("L12 pacote corrompido no caminho: não inicia nada e desliga a máquina", code == 1 and "não consegui enviar" in out
          and not cloud["booted"] and sent("POST", "stop") == [{}] and "Desliguei a máquina" in out, last(out, 2))

    # ---- 13. a conexao cai enquanto a maquina liga: continua tentando
    reset(outage=0)
    cloud["studio"] = NEBIUS_STUDIO
    original = FakeLightning.handle_request

    def flaky(self, method):  # derruba as tres consultas de estado seguintes ao pedido de ligar
        if method == "POST" and self.path.endswith("/start"):
            cloud["outage"] = 3
        return original(self, method)
    FakeLightning.handle_request = flaky
    try:
        code, out = run("--gpu", "L4")
    finally:
        FakeLightning.handle_request = original
    check("L13 queda de conexão enquanto liga: continua tentando e termina bem", code == 0 and "continuo tentando" in out
          and "a máquina ligou" in out and "Pronto." in out, last(out.split("Enviando")[0], 3))

    # ---- 14. sem chaves: pede as duas, aceita a linha inteira colada e guarda
    reset()
    code, out = run("status", keys=False, typed=f"export LIGHTNING_USER_ID={USER_ID}\nLIGHTNING_API_KEY=\"{API_KEY}\"\n")
    saved = json.loads((LIGHTNING / "lightning_chave.txt").read_text(encoding="utf-8")) if (LIGHTNING / "lightning_chave.txt").exists() else {}
    check("L14 sem chaves: explica onde achar, aceita o que foi colado e guarda", code == 0 and "Global Settings" in out
          and "Autorização aceita" in out and saved == {"user_id": USER_ID, "api_key": API_KEY}, last(out, 2))
    code, out = run("status", keys=False)
    check("L14b na vez seguinte usa as chaves guardadas", code == 0 and "Conta: maria" in out and "Global Settings" not in out, last(out))
    (LIGHTNING / "lightning_chave.txt").write_text(json.dumps({"user_id": USER_ID, "api_key": "errada"}), encoding="utf-8")
    code, out = run("status", keys=False)
    check("L14c chaves recusadas: avisa e pede de novo (sem terminal, encerra)", code != 0 and "não é mais aceita" in out
          and "Global Settings" in out, last(out))
    (LIGHTNING / "lightning_chave.txt").unlink()

    # ---- 14d. so a chave, sem o id do usuario (e o que a pagina Keys mostra)
    reset()
    code, out = run("status", keys=False, typed=f"{API_KEY}\n")
    saved = json.loads((LIGHTNING / "lightning_chave.txt").read_text(encoding="utf-8")) if (LIGHTNING / "lightning_chave.txt").exists() else {}
    used = {auth for _, _, auth, _ in cloud["requests"]}
    check("L14d só a chave, sem o id do usuário: vale como portador", code == 0 and "Conta: maria" in out
          and saved == {"user_id": "", "api_key": API_KEY} and used == {ALONE}, last(out, 2))
    (LIGHTNING / "lightning_chave.txt").unlink(missing_ok=True)
    reset()
    code, out = run("status", keys=False, typed="um texto qualquer com espaços\n\n")
    check("L14e texto que não é chave: avisa e pede de novo", code != 0 and "Não entendi o que foi colado" in out
          and not cloud["requests"], last(out))

    # ---- 14f. chave de organizacao (sem usuario, presa a um espaco de trabalho): le a conta...
    reset()
    code, out = run("status", keys="org")
    check("L14f chave de organização: acha o espaço de trabalho por ela e mostra o saldo", code == 0
          and "Conta: chave de acesso, sem usuário" in out and "Créditos: 14.25" in out and "nunca ligada por aqui" in out, last(out, 2))
    # ...mas, se o Lightning nao a deixar ligar maquinas, o script diz o que fazer
    reset(studio=NEBIUS_STUDIO)
    code, out = run("--gpu", "L4", keys="org")
    check("L14g chave sem permissão para ligar: orienta a autorizar pelo navegador", code == 1
          and "não tem permissão" in out and "autorização pelo navegador" in out and not cloud["booted"], last(out, 2))

    # ---- 16. login pelo navegador, como o do cliente oficial: nada para copiar
    (LIGHTNING / "lightning_chave.txt").unlink(missing_ok=True)
    reset()
    code, out = run("status", keys=False, browser=BROWSER_OK)
    saved = json.loads((LIGHTNING / "lightning_chave.txt").read_text(encoding="utf-8")) if (LIGHTNING / "lightning_chave.txt").exists() else {}
    check("L16 login pelo navegador: recebe o id e a chave pessoal e guarda", code == 0 and "Autorização aceita" in out
          and "Conta: maria" in out and saved == {"user_id": USER_ID, "api_key": API_KEY}
          and "Global Settings" not in out and API_KEY not in out, last(out, 2))
    (LIGHTNING / "lightning_chave.txt").unlink(missing_ok=True)
    reset()
    code, out = run("status", keys=False, browser=BROWSER_DEAD, typed=f"{API_KEY}\n", WAN_LOGIN_SECONDS="2")
    check("L16b navegador que não devolve nada: depois da espera, pede a chave", code == 0
          and "não chegou" in out and "Global Settings" in out and "Conta: maria" in out, last(out, 2))
    (LIGHTNING / "lightning_chave.txt").unlink(missing_ok=True)

    # ---- 15. as chaves que a ferramenta oficial guardou na pasta do usuario
    home = WORK / "home_sdk"
    (home / ".lightning").mkdir(parents=True, exist_ok=True)
    (home / ".lightning" / "credentials.json").write_text(json.dumps({"user_id": USER_ID, "api_key": API_KEY, "auth_token": ""}),
                                                          encoding="utf-8")
    code, out = run("status", keys=False, home=home)
    check("L15 credencial da ferramenta oficial funciona", code == 0 and "Conta: maria" in out, last(out))

    boot_scenarios()


BOOT_PROBE = """
import os, sys
sys.path.insert(0, sys.argv[1])
import boot
calls = []



def install():
    calls.append("install")
    if os.environ.get("QUEBRA"):
        raise SystemExit("falhou")


boot.launcher.install = install
boot.launcher.start = lambda idle_minutes: calls.append(f"start {idle_minutes:g}")
boot.launcher.keepalive = lambda: calls.append("keepalive")
boot.launcher.stop = lambda quiet=False: calls.append("stop")
boot.LOCK = __import__("pathlib").Path(sys.argv[2]) / "boot.pid"
code = boot.main()
print("CHAMADAS", code, ",".join(calls), "ORCAMENTO", os.environ.get("DISK_BUDGET_GB"),
      "MODELOS", os.environ.get("MODELS_DIR"), "ROSTO", os.environ.get("SO_ROSTO"))
"""


def boot_scenarios():
    """lightning/boot.py, com o launcher trocado por anotacoes: o que importa aqui e a ordem e o
    desligamento da maquina no fim."""
    def boot(**env):
        full = environment()
        # Os testes de disco abaixo são da sessão completa (modelos do Wan, uns 60 GB): WAN_FULL=1
        # para não cair na sessão enxuta de troca de rosto (uns 9 GB), que tem a sua própria conta.
        full.update(LIGHTNING_CLOUD_PROJECT_ID=PROJECT, LIGHTNING_CLOUD_SPACE_ID=SPACE, WAN_IDLE_MINUTES="20",
                    WAN_FAILURE_GRACE="0.2", WAN_FULL="1", **env)
        result = subprocess.run([sys.executable, "-c", BOOT_PROBE, str(LIGHTNING), str(WORK)], env=full,
                                capture_output=True, text=True, encoding="utf-8", timeout=120)
        return result.stdout + result.stderr

    reset(studio=NEBIUS_STUDIO, phase="RUNNING", machine="lit-l4-1")
    out = boot(WAN_PERSIST_GB="95")
    check("B1 inicialização: instala, liga, mantém e, ao sair, desliga a máquina pela API",
          "CHAMADAS 0 install,start 20,keepalive,stop ORCAMENTO 95 MODELOS None" in out and sent("POST", "stop") == [{}]
          and "Máquina desligada." in out and "guardados entre as sessões" in out, last(out))

    reset(studio=NEBIUS_STUDIO, phase="RUNNING", machine="lit-l4-1")
    out = boot(WAN_PERSIST_GB="95", QUEBRA="1")
    check("B2 instalação que falha: registra o motivo e desliga a máquina do mesmo jeito",
          "SystemExit: falhou" in out and "será desligada em 0 segundos" in out and "CHAMADAS 1 install,stop" in out
          and sent("POST", "stop") == [{}], last(out, 2))

    reset(studio=NEBIUS_STUDIO, phase="RUNNING", machine="lit-l4-1")
    out = boot(WAN_PERSIST_GB="0", WAN_KEEP_ON="1")
    check("B3 disco da conta desconhecido e pedido para não desligar: teto de 45 GB e a máquina fica ligada",
          "ORCAMENTO 45" in out and not sent("POST", "stop") and "continua ligada" in out, last(out))

    # Sem WAN_FULL, o Lightning sobe a sessão enxuta: só a troca de rosto em vídeo (SO_ROSTO), e
    # 15 GB da conta já bastam para guardar (não precisa dos 60 da sessão completa).
    reset(studio=NEBIUS_STUDIO, phase="RUNNING", machine="lit-l4-1")
    full = environment()
    full.update(LIGHTNING_CLOUD_PROJECT_ID=PROJECT, LIGHTNING_CLOUD_SPACE_ID=SPACE, WAN_IDLE_MINUTES="20",
                WAN_FAILURE_GRACE="0.2", WAN_PERSIST_GB="15")
    out = subprocess.run([sys.executable, "-c", BOOT_PROBE, str(LIGHTNING), str(WORK)], env=full,
                         capture_output=True, text=True, encoding="utf-8", timeout=120).stdout
    check("B4 por padrão a sessão é enxuta (só troca de rosto em vídeo): SO_ROSTO ligado e 15 GB da conta já guardam",
          "ROSTO 1" in out and "ORCAMENTO 15" in out and "guardados entre as sessões" in out, last(out))


def main():
    if not BASH:
        print("FALHA este teste precisa de um bash (no Windows, instale o Git)")
        return 1
    shutil.rmtree(WORK, ignore_errors=True)
    HOME.mkdir(parents=True)
    EMPTY_HOME.mkdir(parents=True)
    FAKE_BROWSER.write_text("import sys\nimport urllib.request\n\nurllib.request.urlopen(sys.argv[1], timeout=30).read()\n",
                            encoding="utf-8")
    key_file = LIGHTNING / "lightning_chave.txt"
    saved = key_file.read_text(encoding="utf-8") if key_file.exists() else None
    if saved is not None:  # o teste nao pode usar nem estragar a credencial de verdade
        key_file.unlink()
    try:
        scenarios()
    finally:
        key_file.unlink(missing_ok=True)
        if saved is not None:
            key_file.write_text(saved, encoding="utf-8")
        (FREE / "abrir.html").unlink(missing_ok=True)
    print(f"\n{len(failures)} falha(s)" + (": " + "; ".join(failures) if failures else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    EMPTY_HOME = WORK / "home_vazio"
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeLightning)
    PORT = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    sys.exit(main())
