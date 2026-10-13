"""Liga a GPU do Wan Studio no Lightning AI e abre o site ja pareado. No Windows: dois
cliques em "Ligar GPU (Lightning).bat".

  python lightning/ligar.py              liga (ou avisa que ja esta ligada) e abre o site
  python lightning/ligar.py --gpu L4     escolhe a placa sem perguntar (L40S, L4, T4, RTX6000, A100, H100)
  python lightning/ligar.py --interrompivel   maquina mais barata, que o Lightning pode tomar de volta
  python lightning/ligar.py status       mostra o estado, o saldo de creditos e o disco
  python lightning/ligar.py registro     mostra o que a maquina esta fazendo
  python lightning/ligar.py desligar     desliga a maquina (para de gastar creditos)

O Lightning AI da creditos gratuitos e cobra, deles, o tempo de maquina ligada: por isso este
script mostra o saldo e o preco por hora, e a maquina se desliga sozinha depois de alguns
minutos sem uso (lightning/boot.py). O que ele faz: cria na conta um Studio chamado
"wan-studio" (um por nuvem: a mesma placa custa diferente em cada uma, e ele usa a mais
barata), liga-o na placa escolhida, envia o Wan Studio para la e o inicia.

Usa apenas a biblioteca padrao do Python. As chamadas sao as do cliente oficial (lightning-sdk
2026.10.1): /v1/auth/whoami, /v1/memberships e /v1/projects/<id>/cloudspaces/...

Credencial, na ordem: variaveis LIGHTNING_API_KEY (e LIGHTNING_USER_ID, se houver), lightning/
lightning_chave.txt, ou o que a ferramenta oficial guardou em ~/.lightning/credentials.json.
Sem nenhuma, o script faz o login do cliente oficial: abre o Lightning no navegador, que
devolve o id do usuario e a chave pessoal a um servidor local; se isso nao der certo, pede a
chave. O que vier fica em lightning/lightning_chave.txt. Nao compartilhe esse arquivo.

Ha dois tipos de chave: a pessoal, que vai com o id do usuario em autenticacao basica, e as de
organizacao ou de espaco de trabalho (as da pagina Keys), que vao sozinhas, como portador, e
podem nao ter permissao para ligar maquinas.
"""

import argparse
import base64
import hashlib
import importlib.util
import io
import json
import os
import re
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

API = os.environ.get("LIGHTNING_CLOUD_URL", "https://lightning.ai").rstrip("/")
STUDIO = "wan-studio"
HOME_PAGE = "https://lightning.ai/"

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
KEY_FILE = HERE / "lightning_chave.txt"
SDK_KEYS = Path.home() / ".lightning" / "credentials.json"
BUNDLED = ("app", "vm", "free/launcher.py", "free/pairing.json", "lightning/boot.py")

# Placas oferecidas, na ordem de preferencia: (nome, identificador no Lightning, familia como
# ele a chama, GB de memoria, para que serve). O preco, a disponibilidade e o identificador
# exato vem do proprio Lightning, que tem uma lista por nuvem (conferido em 12/10/2026: a
# mesma L40S custava 3,54 creditos por hora numa e 2,14 em outra).
# Nuvens que ele lista mas que nao tem maquinas proprias: a lista delas e a da nuvem padrao.
SKIP_CLOUDS = ("CLOUDFLARE", "LIGHTNING_AGGREGATE")
GPUS = (
    ("L40S", "lit-l40s-1", "L40S", 48, "rápida; libera a qualidade Ultra (720p direto)"),
    ("L4", "lit-l4-1", "L4", 24, "mais barata por hora e mais lenta; também libera a Ultra"),
    ("T4", "lit-t4-1", "T4", 16, "a placa do Kaggle (aqui, uma só); sem a Ultra"),
    ("RTX6000", "lit-rtx-6000-pro-1", "RTXP", 96, "mais nova e com mais memória que a L40S"),
    ("A100", "lit-a100-1", "A100", 40, "velocidade parecida com a da L40S"),
    ("H100", "lit-h100-1", "H100", 80, "bem mais potente e mais cara"),
    ("H200", "lit-h200-1", "H200", 141, "a mais potente"),
)
PHASE = "CLOUD_SPACE_INSTANCE_STATE_"
IDLE_MINUTES = os.environ.get("WAN_IDLE_MINUTES", "15")
RESERVE_GB = 5        # do disco gratuito da conta, o que fica de fora da conta dos modelos
# Caracteres do pacote enviados por comando. Pequeno de proposito: nao se sabe o tamanho maximo
# de comando que a plataforma aceita, e ha interpretadores que cortam perto de 8 mil.
CHUNK = 7_000
MARK = "__WAN_FIM__"  # fim da saida de um comando remoto, seguido do codigo de saida
# Onde o Wan Studio fica na maquina: a pasta do Studio, que o Lightning guarda entre sessoes.
REMOTE = 'D=/teamspace/studios/this_studio; [ -d "$D" ] || D="$HOME"; D="$D/wan-studio"'
POLL = float(os.environ.get("WAN_POLL_SECONDS", "5"))
TRANSIENT = (0, 429, 500, 502, 503, 504)
# Linhas do registro que so aparecem depois que a instalacao engrenou, e as que dizem que parou.
PROGRESS_MARKS = ("baixando ", "ok  ", "Instalação concluída", "Iniciando o ComfyUI", "Wan Studio está no ar")
FAILURE_MARKS = ("SystemExit:", "Traceback (most recent call last)")
LOGIN_SECONDS = float(os.environ.get("WAN_LOGIN_SECONDS", "300"))  # espera pelo login no navegador
# O que a credencial em uso diz de si: o espaco de trabalho e a organizacao a que esta presa
# (so as chaves que nao sao pessoais informam isso). `credentials` preenche.
WHO = {"project": "", "org": ""}


class LightningError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def call(auth, method, path, body=None, query=None, text=False, timeout=60):
    """Uma chamada a API do Lightning. Erros viram LightningError com a mensagem do servidor."""
    url = API + path + ("?" + urllib.parse.urlencode(query) if query else "")
    headers = {"User-Agent": "wan-studio/1.0", "Accept": "application/json", **auth}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        try:
            detail = json.loads(error.read())
            message = detail.get("message") or detail.get("error") if isinstance(detail, dict) else None
        except ValueError:
            message = None
        raise LightningError(error.code, str(message or f"erro {error.code}")) from None
    except OSError as error:
        raise LightningError(0, f"sem conexão com o Lightning AI ({error})") from None
    if text:
        return raw.decode("utf-8", "replace")
    try:
        return json.loads(raw or b"{}")
    except ValueError:
        raise LightningError(0, "o Lightning AI respondeu algo que não entendi") from None


# ---------------------------------------------------------------- credencial

USER_LABELS = ("LIGHTNING_USER_ID", "user-id", "user_id", "userID", "User ID")
KEY_LABELS = ("LIGHTNING_API_KEY", "api-key", "api_key", "API Key")


def headers_for(user_id, api_key):
    """As formas de apresentar a credencial, na ordem em que valem a pena: com o id do usuario,
    autenticacao basica (a chave pessoal); sempre, tambem como portador (as outras chaves)."""
    basic = {"Authorization": "Basic " + base64.b64encode(f"{user_id}:{api_key}".encode()).decode()}
    return ([basic] if user_id else []) + [{"Authorization": f"Bearer {api_key}"}]


def identify(user_id, api_key):
    """Quem o Lightning diz que esta credencial e: (cabecalho que ele aceitou, {"name",
    "project", "org"}), ou None se nao a reconhecer. /v1/auth/whoami vale para qualquer tipo
    de chave; /v1/auth/user, so para a pessoal."""
    for auth in headers_for(user_id, api_key):
        for path in ("/v1/auth/whoami", "/v1/auth/user"):
            try:
                info = call(auth, "GET", path)
            except LightningError as error:
                if error.status in (400, 401, 403, 404):
                    continue
                raise
            name = (info.get("username") or info.get("email") or info.get("userId") or info.get("id")
                    or ("chave de acesso, sem usuário" if info.get("apiKeyId") or info.get("orgId") else ""))
            if name:
                return auth, {"name": name, "project": info.get("projectId") or "", "org": info.get("orgId") or ""}
    return None


def saved_keys(path):
    """(id do usuario ou "", chave) guardados num arquivo, ou None."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return str(data.get("user_id") or ""), str(data["api_key"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def labeled(text, labels):
    """O valor que vem depois de um dos rotulos (ex.: "export LIGHTNING_API_KEY=..." ou
    "--api-key ..."), ou ""."""
    for label in labels:
        found = re.search(re.escape(label) + r"""["']?\s*[=:\s]\s*["']?([^\s"']{6,})""", text, re.IGNORECASE)
        if found:
            return found.group(1)
    return ""


def bare(text):
    """O texto colado, se for um valor so (sem espacos nem rotulo), ou ""."""
    text = text.strip().strip("\"'")
    return text if re.fullmatch(r"[^\s\"'=]{8,}=*", text) else ""


def browser_login(seconds):
    """O login do cliente oficial (`lightning login`): o site do Lightning, com a conta aberta
    no navegador, redireciona para um servidor local com o id do usuario e a chave pessoal.
    O servidor so existe durante a espera e so escuta nesta maquina. Devolve (id, chave) ou None.
    """
    received = {}

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            url = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(url.query)
            done = url.path == "/login-complete" and query.get("key")
            if done:
                received.update(user_id=(query.get("userID") or [""])[0], api_key=query["key"][0])
            page = ("<!doctype html><meta charset=\"utf-8\"><title>Wan Studio</title>"
                    "<body style=\"font-family: sans-serif; margin: 3em\"><h2>"
                    + ("Pronto. Pode fechar esta aba e voltar para a janela do Wan Studio." if done
                       else "Esta página só serve para concluir o login do Wan Studio.") + "</h2>").encode()
            self.send_response(200 if done else 404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

    server = HTTPServer(("127.0.0.1", 0), Callback)
    server.timeout = 1
    back = f"http://localhost:{server.server_address[1]}/login-complete"
    url = f"{API}/sign-in?" + urllib.parse.urlencode({"redirectTo": back})
    # Numa linha de execucao propria: ha navegadores que so devolvem o controle ao fechar.
    threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
    deadline = time.time() + seconds
    try:
        while time.time() < deadline and "api_key" not in received:
            server.handle_request()
    finally:
        server.server_close()
    return (received["user_id"], received["api_key"]) if "api_key" in received else None


def accept(user_id, api_key):
    """Confere a credencial e a guarda. Devolve (cabecalho, identidade) ou None."""
    found = identify(user_id, api_key)
    if found:
        KEY_FILE.write_text(json.dumps({"user_id": user_id, "api_key": api_key}) + "\n", encoding="utf-8")
        print(f"Autorização aceita e guardada em {KEY_FILE.relative_to(ROOT)}.\n")
    return found


def ask_keys(browser=True):
    print("Para ligar a GPU daqui preciso da autorização da sua conta do Lightning AI (só nesta vez).\n")
    if browser:
        print("Vou abrir o Lightning no seu navegador. Se ele pedir, entre na sua conta; quando a página")
        print(f"disser \"Pronto\", volte para esta janela. (Espero até {LOGIN_SECONDS / 60:.0f} minutos.)\n")
        pair = browser_login(LOGIN_SECONDS)
        found = accept(*pair) if pair else None
        if found:
            return found
        print("A autorização pelo navegador não chegou. Vamos pelo outro caminho, a chave da conta.\n")
    print("  1. No site do Lightning, clique na sua foto (canto de cima, à direita), em Global Settings e em Keys")
    print("  2. Copie a API Key e cole aqui (se a página mostrar também um User ID, cole os dois, um de cada vez)\n")
    user_id = ""
    while True:
        try:
            text = input("API Key: ")
        except EOFError:
            raise SystemExit("\nSem a chave não consigo ligar a GPU.") from None
        user_id = labeled(text, USER_LABELS) or user_id
        api_key = labeled(text, KEY_LABELS) or bare(text)
        if not api_key:
            if not labeled(text, USER_LABELS):
                print("Não entendi o que foi colado. Copie a chave inteira e cole de novo.\n")
            continue
        found = accept(user_id, api_key)
        if found:
            return found
        print("O Lightning AI não reconheceu essa chave. Copie de novo, inteira, e tente outra vez.\n")


def credentials(browser=True):
    """Devolve (cabecalho de autenticacao, nome da conta) e anota em WHO a que a credencial
    esta presa."""
    env = (os.environ.get("LIGHTNING_USER_ID", ""), os.environ.get("LIGHTNING_API_KEY", ""))
    found = None
    for pair in (env if env[1] else None, saved_keys(KEY_FILE), saved_keys(SDK_KEYS)):
        if not pair:
            continue
        found = identify(*pair)
        if found:
            break
        print("(a chave guardada não é mais aceita pelo Lightning AI; ignorando)")
    auth, who = found or ask_keys(browser)
    WHO.update(project=who["project"], org=who["org"])
    return auth, who["name"]


# ---------------------------------------------------------------- conta

def gigabytes(value):
    try:
        return float(value) / 2**30
    except (TypeError, ValueError):
        return None


def teamspace(auth):
    """O espaco de trabalho em que o Studio fica: aquele a que a chave esta presa, se for o
    caso; senao, o padrao da conta."""
    def memberships(query):
        try:
            return call(auth, "GET", "/v1/memberships", query=query).get("memberships") or []
        except LightningError as error:
            if error.status in TRANSIENT:
                raise
            return []  # uma chave sem usuario nao pode perguntar "os meus"

    listing = memberships({"filterByUserId": "true"})
    if not listing and WHO["org"]:
        listing = memberships({"organizationId": WHO["org"]})
    if WHO["project"]:
        return next((item for item in listing if item.get("projectId") == WHO["project"]), {"projectId": WHO["project"]})
    if not listing:
        raise LightningError(404, "a conta não tem nenhum espaço de trabalho (teamspace)")
    wanted = os.environ.get("WAN_LIGHTNING_TEAMSPACE", "").lower()
    named = [item for item in listing if wanted and wanted in (str(item.get("name", "")).lower(),
                                                              str(item.get("displayName", "")).lower())]
    default = [item for item in listing if item.get("isDefault")]
    return (named or default or listing)[0]


def balance(auth, space):
    """Creditos disponiveis para o espaco de trabalho, ou None se a conta nao informar.

    Eles podem estar no proprio espaco, na organizacao dona dele (uma conta nova ja nasce com
    uma) ou no usuario: vale o primeiro lugar que tiver saldo."""
    places = [f"/v1/projects/{space['projectId']}/billing/balance"]
    if "org" in str(space.get("ownerType", "")).lower() and space.get("ownerId"):
        places.append(f"/v1/orgs/{space['ownerId']}/billing/balance")
    places.append("/v1/users/billing/balance")
    found = []
    for path in places:
        try:
            found.append(float(call(auth, "GET", path).get("balance")))
        except (LightningError, TypeError, ValueError):
            continue
    try:
        found.append(float(space.get("balance")))
    except (TypeError, ValueError):
        pass
    return next((value for value in found if value > 0), found[0] if found else None)


def disk(space):
    """(GB usados, GB gratuitos) do disco da conta; None no que ela nao informar."""
    # A cota vem em dois lugares, e numa conta de verdade um deles veio zerado ("0").
    quotas = [gigabytes(space.get("freeStorageBytes")), gigabytes((space.get("quotas") or {}).get("freeStorageBytes"))]
    return gigabytes(space.get("currentStorageBytes")), max((value for value in quotas if value), default=None)


def clouds(auth, project):
    """As nuvens em que a conta pode ligar maquinas, como [(id, provedor)]. Sem a lista, so a
    padrao da conta: [(None, None)]."""
    for path, query in ((f"/v1/projects/{project}/clusters", None), ("/v1/core/clusters", {"projectId": project})):
        try:
            listing = call(auth, "GET", path, query=query).get("clusters") or []
        except LightningError:
            continue
        found = [(item.get("id"), (item.get("spec") or {}).get("driver")) for item in listing]
        found = [(cluster, provider) for cluster, provider in found if cluster and provider and provider not in SKIP_CLOUDS]
        if found:
            return found
    return [(None, None)]


def offers(auth, project, spot=False):
    """Para cada placa de GPUS, a oferta mais barata entre as nuvens da conta: {nome: {"price",
    "slug", "cluster", "provider", "available", "why"}}. Vazio se o Lightning nao informar (ai
    vale a lista fixa, sem preco, na nuvem padrao)."""
    def rank(offer):  # disponivel antes de indisponivel; depois, a mais barata
        return (not offer["available"], offer["price"] or float("inf"))

    found = {}
    for cluster, provider in clouds(auth, project):
        query = {"projectId": project, **({"cloudProvider": provider} if provider else {})}
        try:
            listing = call(auth, "GET", "/v1/core/accelerators", query=query).get("accelerator") or []
        except LightningError:
            continue
        single = [item for item in listing if str((item.get("resources") or {}).get("gpu", "")) == "1"]
        for name, slug, family, _, _ in GPUS:
            # Primeiro pelo identificador; depois pela familia (ha nuvens em que o identificador
            # traz a memoria: "lit-a100-40gb-1"), a mais barata.
            exact = [item for item in single if slug in (item.get("slugMultiCloud"), item.get("slug"))]
            same = sorted((item for item in single if str(item.get("family", "")).upper() == family),
                          key=lambda item: item.get("cost") or 0)
            for item in (exact or same)[:1]:
                why = ("não incluída no seu plano" if item.get("isTierRestricted") or item.get("enabled") is False
                       else "sem máquinas livres agora" if item.get("outOfCapacity")
                       else "sem versão interrompível" if spot and not item.get("spotPrice") else "")
                offer = {"price": (item.get("spotPrice") if spot else item.get("cost")) or None,
                         "slug": item.get("slugMultiCloud") or item.get("slug") or slug, "cluster": cluster,
                         "provider": provider, "available": not why, "why": why}
                if name not in found or rank(offer) < rank(found[name]):
                    found[name] = offer
    return found


def describe(name, offer, credits):
    """Uma linha do menu de placas."""
    _, _, _, memory, purpose = next(gpu for gpu in GPUS if gpu[0] == name)
    parts = [name, f"{memory} GB"]
    price = (offer or {}).get("price")
    if price:
        parts.append(f"{price:.2f} créditos por hora")
        if credits:
            parts.append(f"~{credits / price:.0f} h com o seu saldo")
    if offer and not offer["available"]:
        parts.append(offer["why"])
    return " · ".join(parts) + f" — {purpose}"


def pick_machine(wanted, found, credits, ask):
    """Escolhe a placa: a pedida, ou a que o usuario marcar no menu, ou a primeira disponivel."""
    names = [name for name, *_ in GPUS]
    if wanted:
        wanted = wanted.upper()
        if wanted not in names:
            raise SystemExit(f"Placa desconhecida: {wanted}. Opções: {', '.join(names)}.")
        return wanted
    # Sem informacao da conta, so as tres de costume; com ela, as que estao disponiveis.
    usable = [name for name in names if name in found and found[name]["available"]] if found else names[:3]
    if not usable:
        raise SystemExit("Nenhuma placa de vídeo está disponível para a sua conta agora:\n  "
                         + "\n  ".join(describe(name, found.get(name), credits) for name in names))
    if not ask or len(usable) == 1:
        return usable[0]
    print("Qual placa ligar? O tempo ligado gasta créditos.")
    for number, name in enumerate(usable, 1):
        print(f"  {number}. {describe(name, found.get(name), credits)}")
    while True:
        try:
            answer = input("Digite o número e aperte Enter (só Enter = 1): ").strip() or "1"
        except EOFError:
            return usable[0]
        if answer.isdigit() and 1 <= int(answer) <= len(usable):
            print()
            return usable[int(answer) - 1]


# ---------------------------------------------------------------- studio

def studio_name(provider):
    """Um Studio fica sempre na nuvem em que foi criado; por isso ha um por nuvem. O da nuvem
    padrao se chama "wan-studio"; os outros levam o nome da nuvem."""
    return STUDIO if provider in (None, "AWS") else f"{STUDIO}-{provider.lower().replace('_', '-')}"


def our_studios(auth, project):
    listing = call(auth, "GET", f"/v1/projects/{project}/cloudspaces").get("cloudspaces") or []
    return [item for item in listing if item.get("name") == STUDIO or str(item.get("name", "")).startswith(STUDIO + "-")]


def create_studio(auth, project, name, cluster=None):
    """Cria o Studio como o cliente oficial: o Studio e, em seguida, a primeira "execucao" dele."""
    body = {"name": name, "displayName": name,
            "seedFiles": [{"path": "LEIA-ME.txt", "contents": "Studio criado pelo Wan Studio (lightning/ligar.py).\n"}]}
    if cluster:
        body["clusterId"] = cluster
    studio = call(auth, "POST", f"/v1/projects/{project}/cloudspaces", body)
    call(auth, "POST", f"/v1/projects/{project}/cloudspaces/{studio['id']}/runs",
         {"clusterId": studio.get("clusterId"), "localSource": True})
    return studio


def survey(auth, project):
    """Os Studios do Wan Studio na conta, com o estado da maquina de cada um, como [(studio,
    base das rotas, estado, maquina)]: o que estiver ligado (ou ligando) vem primeiro."""
    order = ("RUNNING", "PENDING", "IMAGE_BUILDING", "STOPPING")
    found = []
    for studio in our_studios(auth, project):
        base = f"/v1/projects/{project}/cloudspaces/{studio['id']}"
        found.append((studio, base, *machine_state(auth, base)))
    return sorted(found, key=lambda item: order.index(item[2]) if item[2] in order else len(order))


def machine_state(auth, base):
    """(estado, dados da maquina): RUNNING, PENDING, STOPPING, STOPPED, FAILED...; STOPPED
    quando nao ha maquina nenhuma."""
    status = call(auth, "GET", f"{base}/codestatus")
    in_use = status.get("inUse") or {}
    if not in_use:
        return ("PENDING" if status.get("requested") else "STOPPED"), status.get("requested") or {}
    return str(in_use.get("phase") or "PENDING").removeprefix(PHASE), in_use


def patient_state(auth, base):
    """machine_state que nao desiste numa falha passageira de conexao."""
    try:
        return machine_state(auth, base)
    except LightningError as error:
        if error.status not in TRANSIENT:
            raise
        return "SEM_CONEXAO", {}


def ready(state, machine):
    """Ligada e com os arquivos do Studio ja no lugar (e o que o cliente oficial espera)."""
    startup = machine.get("startupStatus")
    return state == "RUNNING" and (startup is None or bool(startup.get("topUpRestoreFinished")))


def wait_machine(auth, base, wanted, seconds, labels):
    """Acompanha a maquina ate `wanted(estado, maquina)` valer. Devolve o ultimo estado visto."""
    deadline, shown = time.time() + seconds, None
    while True:
        state, machine = patient_state(auth, base)
        if state != shown and state in labels:
            print(labels[state])
        shown = state
        if wanted(state, machine) or state == "FAILED" or time.time() > deadline:
            return state, machine
        time.sleep(POLL)


def machine_name(machine):
    """O nome da placa a partir do identificador da maquina ("lit-a100-40gb-1" e uma A100)."""
    slug = (machine.get("computeConfig") or {}).get("name") or ""
    stem = lambda known: known.rsplit("-", 1)[0] + "-"  # noqa: E731
    return next((name for name, known, *_ in GPUS if slug == known or slug.startswith(stem(known))), slug)


# ---------------------------------------------------------------- comandos na maquina

def command_output(auth, base, session):
    """(saida ate agora, codigo de saida ou None) de um comando remoto. A resposta vem em
    linhas JSON; como no cliente oficial, o codigo -1 quer dizer que ainda esta rodando."""
    output, code = "", None
    for line in call(auth, "GET", f"{base}/execute/{session}/stream", text=True).splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        result = item.get("result", item) if isinstance(item, dict) else {}
        output += result.get("output") or ""
        try:
            code = int(result["exitCode"])
        except (KeyError, TypeError, ValueError):
            continue
        if code == -1:
            code = None
            break
    return output, code


def remote(auth, base, script, wait=120):
    """Roda `script` (shell) na maquina e devolve (saida, codigo de saida); o codigo e None se
    o tempo acabou antes. O fim e reconhecido pela marca que o proprio comando escreve."""
    reply = call(auth, "POST", f"{base}/execute",
                 {"command": f"{REMOTE}; {{ {script} ; }} 2>&1; echo {MARK}$?", "detached": True})
    session, output, code = reply.get("sessionName"), reply.get("output") or "", reply.get("exitCode")
    deadline = time.time() + wait
    while True:
        if session:
            try:
                output, code = command_output(auth, base, session)
            except LightningError as error:
                if error.status not in TRANSIENT + (404,):  # 404: a sessao ainda nao apareceu
                    raise
        found = re.search(re.escape(MARK) + r"(\d+)", output)
        if found:
            return output[:found.start()], int(found.group(1))
        if not session or code is not None:
            return output, code
        if time.time() > deadline:
            return output, None
        time.sleep(min(1.0, POLL))


def bundle():
    """O Wan Studio empacotado, como no notebook do Kaggle, mais o iniciador deste servico."""
    if not (ROOT / "free" / "pairing.json").exists():
        raise SystemExit("Não achei free\\pairing.json. Rode: python free\\build.py")
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for entry in BUNDLED:
            source = ROOT / entry
            for path in (sorted(source.rglob("*")) if source.is_dir() else [source]):
                if path.is_file() and "__pycache__" not in path.parts:
                    tar.add(path, arcname=path.relative_to(ROOT).as_posix())
    return buffer.getvalue()


def deploy(auth, base):
    """Envia o pacote em pedacos, por comandos, e o desempacota conferindo a integridade."""
    data = bundle()
    encoded = base64.b64encode(data).decode()
    steps = ['mkdir -p "$D" && rm -f "$D/pacote.b64"']
    steps += [f"printf %s '{encoded[start:start + CHUNK]}' >> \"$D/pacote.b64\""
              for start in range(0, len(encoded), CHUNK)]
    steps.append(f'cd "$D" && base64 -d pacote.b64 > pacote.tgz && echo "{hashlib.sha256(data).hexdigest()}  pacote.tgz" '
                 "| sha256sum -c - >/dev/null && rm -rf app vm free lightning && tar -xzf pacote.tgz "
                 "&& rm -f pacote.b64 pacote.tgz && echo instalado")
    for step in steps:
        output, code = remote(auth, base, step)
        if code != 0:
            detail = output.strip().splitlines()[-1][:200] if output.strip() else "sem resposta da máquina"
            raise LightningError(0, f"não consegui enviar o Wan Studio para a máquina ({detail})")


def boot(auth, base, project, studio_id, persist):
    """Inicia lightning/boot.py solto do comando: ele segue rodando depois que este script sai."""
    env = (f"WAN_IDLE_MINUTES={IDLE_MINUTES} WAN_PERSIST_GB={persist} "
           f"LIGHTNING_CLOUD_PROJECT_ID={project} LIGHTNING_CLOUD_SPACE_ID={studio_id}")
    # setsid tira o processo da sessao do comando; onde ele nao existir, o nohup sozinho serve.
    # O caminho do Python vem dele mesmo: no terminal do Studio (zsh), "python" e um atalho que
    # prepara o ambiente, e `command -v python` devolve o texto do atalho, nao um arquivo.
    script = ('cd "$D" && mkdir -p logs && '
              "PY=\"$(python -c 'import sys; print(sys.executable)' 2>/dev/null "
              "|| python3 -c 'import sys; print(sys.executable)')\" && [ -x \"$PY\" ] && "
              'S="$(command -v setsid || true)" && '
              f'({env} $S nohup "$PY" -u lightning/boot.py > logs/boot.log 2>&1 < /dev/null &) && echo ligando')
    output, code = remote(auth, base, script)
    if code != 0:
        raise LightningError(0, f"não consegui iniciar o Wan Studio na máquina ({output.strip()[-200:]})")


def booting(auth, base):
    """O iniciador ja esta rodando nesta maquina? O numero do processo fica num arquivo que
    sobrevive a um desligamento: so vale se o processo com esse numero for mesmo o iniciador."""
    output, _ = remote(auth, base, '[ -f "$D/logs/boot.pid" ] && grep -qs boot.py "/proc/$(cat "$D/logs/boot.pid")/cmdline" '
                                   "&& echo SIM || echo NAO", wait=60)
    return "SIM" in output


def log_tail(auth, base, lines=40):
    output, _ = remote(auth, base, f'tail -n {int(lines)} "$D/logs/boot.log"', wait=60)
    return output


def early_outcome(auth, base, seconds=120):
    """Acompanha o comeco da inicializacao, que e quando aparecem os problemas de instalacao.
    Devolve (resultado, motivo, fim do registro); o resultado e "ok", "erro" ou, se nada
    conclusivo, "?"."""
    deadline = time.time() + seconds
    misses, text = 0, ""
    while time.time() < deadline:
        try:
            text, alive = log_tail(auth, base), booting(auth, base)
        except LightningError:
            # Quando a instalacao falha a maquina se desliga, e ai os comandos sao recusados.
            if patient_state(auth, base)[0] in ("STOPPING", "STOPPED", "FAILED", "DELETED"):
                return "erro", "a máquina se desligou logo depois de ligar", text
            alive = True
        for line in text.splitlines():
            if any(mark in line for mark in FAILURE_MARKS):
                return "erro", line.split("SystemExit:", 1)[-1].strip(), text
        if any(mark in text for mark in PROGRESS_MARKS):
            return "ok", "", text
        # O iniciador anota o proprio processo logo ao subir: se ele nao esta la, nao subiu.
        misses = 0 if alive else misses + 1
        if misses >= 3:
            shown = text.strip().splitlines()[-1][:200] if text.strip() else "o registro está vazio"
            return "erro", f"o Wan Studio não iniciou na máquina ({shown})", text
        time.sleep(POLL)
    return "?", "", text


# ---------------------------------------------------------------- site

def kaggle_script():
    """free/ligar.py como modulo: a abertura do site ja pareado e a mesma nos dois servicos."""
    spec = importlib.util.spec_from_file_location("wan_ligar_kaggle", ROOT / "free" / "ligar.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def open_site(started, browser):
    return kaggle_script().open_site(starting=started, browser=browser)


# ---------------------------------------------------------------- comandos

def session(auth):
    """O que todo comando precisa saber da conta: (espaco de trabalho, Studio, base das rotas
    dele, estado, maquina) do Studio mais "ligado" entre os do Wan Studio; sem nenhum, o
    Studio e a base sao None."""
    space = teamspace(auth)
    found = survey(auth, space["projectId"])
    return (space, *found[0]) if found else (space, None, None, "STOPPED", {})


def account_lines(auth, space):
    lines = []
    credits = balance(auth, space)
    if credits is not None:
        refill = str(space.get("nextFreeCreditsGrant") or "")[:10]
        lines.append(f"Créditos: {credits:.2f}" + (f" (próxima recarga gratuita em {refill})" if refill else ""))
    used, free = disk(space)
    if used is not None:
        lines.append(f"Disco da conta: {used:.0f} GB usados" + (f" de {free:.0f} GB gratuitos" if free else ""))
    return lines, credits


def power_down(auth, base):
    """Pede o desligamento da maquina e diz, numa linha, no que deu. E o que impede que uma
    falha no meio do caminho deixe a maquina ligada, gastando creditos sem fazer nada."""
    try:
        call(auth, "POST", f"{base}/stop", {})
    except LightningError as error:
        return f"Não consegui desligar a máquina ({error}). Desligue com: python lightning\\ligar.py desligar"
    return "Desliguei a máquina, para não gastar créditos à toa."


def start(auth, args):
    space, studio, base, state, machine = session(auth)
    project = space["projectId"]
    lines, credits = account_lines(auth, space)
    print("\n".join(lines + [""]))
    browser = not args.sem_navegador

    if state == "STOPPING":
        print("A máquina está terminando de desligar; esperando…")
        state, machine = wait_machine(auth, base, lambda now, _: now == "STOPPED", 300, {})
    if state in ("RUNNING", "PENDING", "IMAGE_BUILDING"):
        if state != "RUNNING":
            print("A máquina já foi pedida e está ligando; esperando…")
            state, machine = wait_machine(auth, base, ready, 900, {})
        if state == "RUNNING" and booting(auth, base):
            print(f"A GPU já está ligada ({machine_name(machine) or 'placa não informada'}). Abrindo o site…")
            open_site(int(time.time()), browser)
            return 0
    started = int(time.time())
    if state != "RUNNING":
        found = offers(auth, project, spot=args.interrompivel)
        name = pick_machine(args.gpu, found, credits, ask=sys.stdin.isatty() and not args.sem_perguntas)
        offer = found.get(name)
        if offer and not offer["available"]:
            print(f"A {name} não está disponível: {offer['why']}. Escolha outra com --gpu.")
            return 1
        # O Studio da nuvem em que a placa sai mais barata (parado, um Studio nao gasta creditos).
        wanted = studio_name(offer["provider"] if offer else None)
        studio = next((item for item in our_studios(auth, project) if item.get("name") == wanted), None)
        if studio is None:
            print(f'Criando o Studio "{wanted}" na sua conta…')
            studio = create_studio(auth, project, wanted, offer["cluster"] if offer else None)
        base = f"/v1/projects/{project}/cloudspaces/{studio['id']}"
        price = f" ({offer['price']:.2f} créditos por hora)" if offer and offer["price"] else ""
        print(f"Ligando a máquina com a placa {name}{price}…")
        slug = offer["slug"] if offer else next(known for gpu, known, *_ in GPUS if gpu == name)
        try:
            call(auth, "POST", f"{base}/start", {"computeConfig": {"name": slug, "spot": args.interrompivel}})
        except LightningError as error:
            if error.status in TRANSIENT + (401, 403):  # 401 e 403: problema da chave, explicado em main
                raise
            print(f"\nO Lightning AI não aceitou ligar a máquina: {error}")
            print(f"Confira o saldo de créditos e se o telefone da conta foi verificado, em {HOME_PAGE}")
            return 1
        state, machine = wait_machine(auth, base, ready, 900, {
            "PENDING": "  o Lightning está separando a máquina (pode levar alguns minutos)…",
            "RUNNING": "  a máquina ligou.", "SEM_CONEXAO": "  sem conexão com o Lightning agora; continuo tentando…"})
        if not ready(state, machine):
            print(f"\nA máquina não ligou ({state}). {machine.get('statusMessage') or ''}".rstrip())
            print(power_down(auth, base))  # o pedido pode estar de pe: sem isto ela ligaria depois, sozinha
            print("Veja o saldo e o estado com: python lightning\\ligar.py status")
            return 1

    try:
        probe, _ = remote(auth, base, "command -v nvidia-smi >/dev/null && nvidia-smi -L | head -1 || echo SEM_GPU", wait=60)
        if "SEM_GPU" in probe:
            print("\nO Studio está ligado numa máquina sem placa de vídeo. Desligue-o e ligue de novo por aqui:")
            print("  python lightning\\ligar.py desligar")
            return 1
        print("Enviando o Wan Studio para a máquina…")
        deploy(auth, base)
        used, free = disk(space)
        persist = max(0, int(free - RESERVE_GB)) if free else 0
        boot(auth, base, project, studio["id"], persist)
    except LightningError as error:
        print(f"\nNão deu certo: {error}.")
        print(power_down(auth, base))
        return 1
    print("  conferindo o início da instalação…")
    outcome, reason, tail = early_outcome(auth, base)
    if outcome == "erro":
        print(f"\nA instalação parou logo no começo: {reason}")
        if tail.strip():
            print("\nFim do registro da máquina:\n  " + "\n  ".join(tail.strip().splitlines()[-15:]))
        # Para nao gastar creditos: quem desliga e o iniciador; se ele nem subiu, este script.
        try:
            if machine_state(auth, base)[0] == "RUNNING" and not booting(auth, base):
                print("\n" + power_down(auth, base))
            else:
                print("\nA máquina se desliga sozinha em seguida.")
        except LightningError:
            print("\nConfira se a máquina desligou: python lightning\\ligar.py status")
        return 1
    open_site(started, browser)
    print("\nPronto. O site conecta sozinho em alguns minutos: na primeira vez a máquina baixa os modelos.")
    print(f"Ligada, ela gasta créditos; por isso se desliga sozinha após {IDLE_MINUTES} minutos sem uso.")
    print("Para desligar antes: no site, clique no indicador verde e em Desligar GPU.")
    return 0


def show_status(auth):
    space, studio, base, state, machine = session(auth)
    lines, _ = account_lines(auth, space)
    print("\n".join(lines))
    if studio is None:
        print("GPU: nunca ligada por aqui.")
        return 0
    names = {"RUNNING": "ligada", "PENDING": "ligando", "IMAGE_BUILDING": "ligando", "STOPPING": "desligando",
             "STOPPED": "desligada", "FAILED": "parou com erro", "DELETED": "desligada"}
    price = machine.get("price")
    detail = [machine_name(machine)] if state == "RUNNING" and machine_name(machine) else []
    if state == "RUNNING" and price:
        detail.append(f"{float(price):.2f} créditos por hora")
    print(f"GPU: {names.get(state, state)}" + (f" ({', '.join(detail)})" if detail else "") + ".")
    return 0


def show_log(auth, lines):
    _, studio, base, state, _ = session(auth)
    if studio is None or state != "RUNNING":
        print("A GPU está desligada: não há registro para mostrar.")
        return 0
    print(log_tail(auth, base, lines).rstrip() or "(o registro ainda está vazio)")
    return 0


def turn_off(auth):
    _, studio, base, state, _ = session(auth)
    if studio is None or state in ("STOPPED", "DELETED", "FAILED"):
        print("A GPU já está desligada.")
        return 0
    if state != "STOPPING":
        call(auth, "POST", f"{base}/stop", {})
    print("Desligando a máquina…")
    state, _ = wait_machine(auth, base, lambda now, _: now in ("STOPPED", "DELETED"), 180, {})
    print("GPU desligada." if state in ("STOPPED", "DELETED") else
          "O pedido foi aceito; a máquina ainda está desligando. Confira com: python lightning\\ligar.py status")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Liga a GPU do Wan Studio no Lightning AI.")
    parser.add_argument("comando", nargs="?", default="ligar", choices=("ligar", "status", "registro", "desligar"))
    parser.add_argument("--gpu", help="placa: " + ", ".join(name for name, *_ in GPUS))
    parser.add_argument("--interrompivel", action="store_true",
                        help="máquina mais barata, que o Lightning pode tomar de volta no meio de um vídeo")
    parser.add_argument("--sem-navegador", action="store_true", help="não abre o site ao terminar")
    parser.add_argument("--sem-perguntas", action="store_true", help="não pergunta a placa: usa a primeira disponível")
    parser.add_argument("--linhas", type=int, default=40, help="quantas linhas do registro mostrar")
    args = parser.parse_args()

    print("Wan Studio · GPU do Lightning AI\n")
    try:
        auth, user = credentials(browser=not args.sem_navegador)
        print(f"Conta: {user}")
        if args.comando == "status":
            return show_status(auth)
        if args.comando == "registro":
            return show_log(auth, args.linhas)
        if args.comando == "desligar":
            return turn_off(auth)
        return start(auth, args)
    except LightningError as error:
        hint = ""
        if error.status in (401, 403):  # a chave nao vale mais, ou nao pode fazer isto
            hint = ("\nEssa chave não tem permissão para isso. Apague lightning\\lightning_chave.txt e rode de novo: "
                    "o atalho pede a autorização pelo navegador, que é a que pode ligar máquinas.")
        print(f"\nNão deu certo: {error}.{hint}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
