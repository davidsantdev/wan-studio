"""Liga a GPU do Wan Studio sem abrir o Kaggle: envia o notebook pela API oficial, que o
executa em segundo plano, e abre o site ja pareado. No Windows: dois cliques em "Ligar GPU.bat".

  python free/ligar.py            liga (ou avisa que ja esta ligada) e abre o site
  python free/ligar.py status     so mostra o estado e a cota de GPU da semana
  python free/ligar.py registro   mostra o que a GPU esta fazendo agora

Usa apenas a biblioteca padrao do Python. As chamadas sao as mesmas do cliente oficial
(kaggle 2.2 / kagglesdk): POST {API}/kernels.KernelsApiService/<Metodo> com JSON.

Credencial, na ordem: variavel KAGGLE_API_TOKEN, free/kaggle_token.txt, ~/.kaggle/access_token,
ou um kaggle.json antigo (nesta pasta, na raiz do projeto ou em ~/.kaggle). Sem nenhuma, o
script pede o token uma vez e o guarda em free/kaggle_token.txt. Nao compartilhe esse arquivo.
"""

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

API = os.environ.get("KAGGLE_API_URL", "https://api.kaggle.com/v1").rstrip("/")
TITLE = "Wan Studio"
SLUG = "wan-studio"  # o Kaggle deriva o endereco do notebook do titulo
MACHINE = "NvidiaTeslaT4"  # no Kaggle, a opcao "GPU T4 x2"
TOKEN_PAGE = "https://www.kaggle.com/settings/api"

FREE = Path(__file__).resolve().parent
ROOT = FREE.parent
NOTEBOOK = FREE / "wan_studio_free.ipynb"
PAIRING = FREE / "pairing.json"
TOKEN_FILE = FREE / "kaggle_token.txt"
OPENER = FREE / "abrir.html"  # fora de site/: contem o segredo de pareamento
SITE = ROOT / "site" / "index.html"
ON = ("RUNNING", "QUEUED")
TRANSIENT = (0, 429, 500, 502, 503, 504)  # 0: sem conexao; o resto, o Kaggle fora do ar por instantes
OFFLINE = "SEM_CONEXAO"


class KaggleError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def call(auth, service, method, body):
    """Uma chamada a API do Kaggle. Erros viram KaggleError com a mensagem do servidor."""
    request = urllib.request.Request(
        f"{API}/{service}/{method}", data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "wan-studio/1.0", **auth})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        try:
            detail = json.loads(error.read()).get("error", {})
            message = detail.get("message") if isinstance(detail, dict) else str(detail)
        except ValueError:
            message = None
        raise KaggleError(error.code, message or f"erro {error.code}") from None
    except OSError as error:
        raise KaggleError(0, f"sem conexão com o Kaggle ({error})") from None


# ---------------------------------------------------------------- credencial

def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def username_of(token):
    """Nome do dono do token, ou None se o Kaggle nao o reconhecer."""
    try:
        info = call(bearer(token), "security.OAuthService", "IntrospectToken", {"token": token})
    except KaggleError as error:
        if error.status in (400, 401, 403, 404):
            return None
        raise
    return info.get("username") if info.get("active") else None


def read(path):
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def legacy_credentials():
    """Um kaggle.json do formato antigo: {"username": ..., "key": ...}."""
    for path in (FREE / "kaggle.json", ROOT / "kaggle.json", Path.home() / ".kaggle" / "kaggle.json"):
        try:
            data = json.loads(read(path) or "{}")
            pair = f"{data['username']}:{data['key']}".encode()
        except (ValueError, KeyError, TypeError):
            continue
        return {"Authorization": "Basic " + base64.b64encode(pair).decode()}, data["username"]
    return None


def ask_token(browser=True):
    print("Para ligar a GPU daqui preciso de um token da sua conta do Kaggle (só nesta vez).\n")
    print(f"  1. Vou abrir {TOKEN_PAGE}")
    print('  2. Em "API", clique em "Generate New Token" e copie o token que aparecer')
    print("  3. Volte para esta janela, cole o token e aperte Enter\n")
    if browser:
        webbrowser.open(TOKEN_PAGE)
    while True:
        try:
            token = input("Token: ").strip().strip('"')
        except EOFError:
            raise SystemExit("\nSem token não consigo ligar a GPU.") from None
        if not token:
            continue
        user = username_of(token)
        if user:
            TOKEN_FILE.write_text(token + "\n", encoding="utf-8")
            print(f"Token aceito e guardado em {TOKEN_FILE.relative_to(ROOT)}.\n")
            return bearer(token), user
        print("O Kaggle não reconheceu esse token. Copie de novo, inteiro, e tente outra vez.\n")


def credentials(browser=True):
    """Devolve (cabecalho de autenticacao, nome de usuario)."""
    candidates = [os.environ.get("KAGGLE_API_TOKEN", ""), read(TOKEN_FILE),
                  read(Path.home() / ".kaggle" / "access_token"),
                  read(Path.home() / ".kaggle" / "access_token.txt")]
    for token in filter(None, candidates):
        user = username_of(token)
        if user:
            return bearer(token), user
        print("(um token guardado não é mais aceito pelo Kaggle; ignorando)")
    return legacy_credentials() or ask_token(browser)


# ---------------------------------------------------------------- notebook

def notebook_text():
    """O notebook como o servidor espera: celulas com o texto em uma so string, sem saidas."""
    if not NOTEBOOK.exists():
        raise SystemExit(f"Não achei {NOTEBOOK.relative_to(ROOT)}. Rode: python free\\build.py")
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    for cell in notebook.get("cells", []):
        if isinstance(cell.get("source"), list):
            cell["source"] = "".join(cell["source"])
        if cell.get("cell_type") == "code":
            cell["outputs"] = []
    return json.dumps(notebook)


def session_status(auth, user):
    """Estado da ultima execucao (RUNNING, QUEUED, COMPLETE, ERROR...), ou None se o
    notebook ainda nao existe na conta."""
    try:
        reply = call(auth, "kernels.KernelsApiService", "GetKernelSessionStatus",
                     {"userName": user, "kernelSlug": SLUG})
    except KaggleError as error:
        if error.status in (403, 404):
            return None, ""
        raise
    # A API omite campos com o valor padrao, e o padrao deste e "na fila".
    return str(reply.get("status") or "QUEUED").upper(), reply.get("failureMessage") or ""


def patient_status(auth, user):
    """session_status que nao desiste numa falha passageira (a internet do computador caiu, o
    Kaggle engasgou): devolve OFFLINE. Depois que o notebook foi enviado a GPU liga de qualquer
    jeito; so falta acompanhar."""
    try:
        return session_status(auth, user)
    except KaggleError as error:
        if error.status not in TRANSIENT:
            raise
        return OFFLINE, ""


def push(auth, user):
    """Cria ou atualiza o notebook e manda executar tudo, com GPU e internet."""
    body = {
        "slug": f"{user}/{SLUG}", "newTitle": TITLE, "text": notebook_text(),
        "language": "python", "kernelType": "notebook", "isPrivate": True,
        "enableGpu": True, "enableInternet": True, "machineShape": MACHINE,
    }
    try:
        reply = call(auth, "kernels.KernelsApiService", "SaveKernel", body)
    except KaggleError as error:
        if error.status != 400:
            raise
        reply = {"error": str(error)}
    if reply.get("error") and "machine" in reply["error"].lower():
        # A conta nao aceita escolher a placa: deixa o Kaggle usar a GPU padrao.
        del body["machineShape"]
        reply = call(auth, "kernels.KernelsApiService", "SaveKernel", body)
    return reply


def hours(duration):
    """Duracoes vem no formato do protobuf: "3600s"."""
    try:
        return float(str(duration).rstrip("s")) / 3600
    except ValueError:
        return None


def quota_line(auth):
    try:
        gpu = call(auth, "kernels.KernelsApiService", "GetAcceleratorQuotaStatistics", {}).get("gpuQuota") or {}
    except KaggleError:
        return None
    used, total = hours(gpu.get("timeUsed", "0s")), hours(gpu.get("totalTimeAllowed"))
    if used is None or not total:
        return None
    return f"Cota de GPU desta semana: {used:.1f} h usadas de {total:.0f} h (restam {max(0.0, total - used):.1f} h)."


# ---------------------------------------------------------------- site

def pairing_code(starting):
    """Codigo que pareia o navegador com o notebook, para o site achar a sessao sozinho.
    `starting` e a hora em que a GPU foi ligada (o site mostra "ligando" por um tempo)."""
    try:
        pairing = json.loads(PAIRING.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, ""
    data = {"t": pairing["topic"], "k": pairing["key"]}
    if starting:
        data["s"] = int(starting)
    code = base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")
    return code, pairing.get("site", "")


def open_site(starting, browser=True):
    code, site = pairing_code(starting)
    if site:
        target = f"{site.rstrip('/')}/#c={code}" if code else site
    else:
        # Abrir um arquivo local com "#..." pelo Windows perde o trecho apos o "#"; esta
        # pagina intermediaria faz o navegador ir ate o site ja com o codigo.
        link = "../site/index.html" + (f"#c={code}" if code else "")
        OPENER.write_text(
            '<!doctype html><meta charset="utf-8"><title>Wan Studio</title>\n'
            f"<script>location.replace({json.dumps(link)});</script>\n", encoding="utf-8")
        target = OPENER.as_uri()
    if browser:
        webbrowser.open(target)
    return target


# ---------------------------------------------------------------- comandos

def wait_until_running(auth, user, seconds=900):
    """Acompanha a subida da sessao (a fila do Kaggle pode levar varios minutos). Devolve o
    estado final observado."""
    deadline, shown = time.time() + seconds, "?"
    while time.time() < deadline:
        state, failure = patient_status(auth, user)
        if state != shown:
            shown = state
            print({"QUEUED": "  na fila do Kaggle (pode levar alguns minutos)…", "RUNNING": "  a máquina ligou.",
                   OFFLINE: "  sem conexão com o Kaggle agora; continuo tentando…",
                   None: "  preparando a sessão…"}.get(state, f"  estado: {state}"))
        if state not in ("QUEUED", OFFLINE, None):
            return state, failure
        time.sleep(float(os.environ.get("WAN_POLL_SECONDS", "10")))
    return (OFFLINE if shown == OFFLINE else "QUEUED"), ""


def start(auth, user, browser):
    state, _ = session_status(auth, user)
    if state in ON:
        if state == "QUEUED":
            print("A GPU já foi pedida e está na fila do Kaggle: é só esperar, o site conecta sozinho "
                  "quando ela ligar.")
        else:
            print("A GPU já está ligada. Abrindo o site…")
        # Com a hora, o site mostra "ligando" ate conectar, em vez de "desligada".
        open_site(starting=int(time.time()), browser=browser)
        return 0

    print("Enviando o notebook ao Kaggle…")
    reply = push(auth, user)
    if reply.get("error"):
        print(f"\nO Kaggle recusou: {reply['error']}")
        print("Se a mensagem falar de internet ou GPU, verifique o telefone da conta em "
              "https://www.kaggle.com/settings (isso libera os dois).")
        return 1
    print(f"Notebook enviado (versão {reply.get('versionNumber', '?')}). Ligando a GPU…")
    started = int(time.time())

    state, failure = wait_until_running(auth, user)
    if state == "RUNNING":
        print("  conferindo o início da instalação…")
        outcome, reason = early_outcome(auth, user)
        if outcome == "erro":
            print(f"\nA sessão parou logo no começo: {reason}")
            if "internet" in reason.lower() or "nvidia" in reason.lower():
                print("\nIsso acontece quando a conta do Kaggle não tem o telefone verificado: sem ele o")
                print("Kaggle não libera internet nem GPU. Para resolver:")
                print("  1. Abra https://www.kaggle.com/settings")
                print('  2. Em "Phone Verification", confirme o seu número (é gratuito)')
                print("  3. Dê dois cliques em Ligar GPU de novo")
            else:
                print("Veja o registro completo com: python free\\ligar.py registro")
            return 1
        open_site(starting=started, browser=browser)
        print("\nPronto. O site conecta sozinho em uns 5 minutos (a GPU está baixando os modelos).")
        print("Para desligar: no site, clique no indicador verde e em Desligar GPU.")
        print("Se esquecer, ela se desliga sozinha após 60 minutos sem uso.")
        return 0
    if state == "QUEUED":
        open_site(starting=started, browser=browser)
        print("\nO Kaggle ainda não liberou a máquina. Pode fechar esta janela: quando liberar, "
              "o site conecta sozinho.")
        return 0
    if state == OFFLINE:
        open_site(starting=started, browser=browser)
        print("\nFiquei sem conexão com o Kaggle, mas o notebook já foi enviado: a GPU liga do mesmo "
              "jeito e o site conecta sozinho.")
        print("Para conferir quando a internet voltar: python free\\ligar.py status")
        return 0
    print(f"\nA sessão terminou sem ligar ({state}). {failure}".rstrip())
    print(f"Veja o registro em https://www.kaggle.com/code/{user}/{SLUG}")
    return 1


def show_status(auth, user):
    state, failure = session_status(auth, user)
    names = {"RUNNING": "ligada", "QUEUED": "na fila", "COMPLETE": "desligada", "ERROR": "parou com erro",
             "CANCEL_ACKNOWLEDGED": "cancelada", "CANCEL_REQUESTED": "sendo cancelada", None: "nunca ligada por aqui"}
    print(f"GPU: {names.get(state, state)}. {failure}".rstrip())
    return 0


def log_lines(auth, user, seconds):
    """As linhas do registro da execucao, ao vivo por ate `seconds`. A cada chamada o Kaggle
    reenvia o registro desde o comeco; se a sessao ja acabou, ele vem inteiro de uma vez."""
    request = urllib.request.Request(
        f"{API}/kernels/logs/stream/{user}/{SLUG}",
        headers={"Accept": "text/event-stream, */*", "User-Agent": "wan-studio/1.0", **auth})
    deadline = time.time() + seconds
    try:
        with urllib.request.urlopen(request, timeout=max(5, seconds)) as response:
            if "text/event-stream" not in (response.headers.get("Content-Type") or "").lower():
                body = response.read().decode("utf-8", "replace")
                try:
                    events = json.loads(body)
                except ValueError:
                    events = [{"data": body}]
                for event in events if isinstance(events, list) else [events]:
                    yield from str(event.get("data", "")).rstrip("\n").split("\n")
                return
            for raw in response:
                if time.time() > deadline:
                    return
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if not line.startswith("data:"):
                    continue
                payload = line[5:].lstrip()
                if payload == "END_OF_LOG":
                    return
                try:
                    text = json.loads(payload).get("data", "")
                except (ValueError, AttributeError):
                    text = payload
                yield from str(text).rstrip("\n").split("\n")
    except OSError:
        return  # sem registro ainda, ou o tempo acabou esperando a proxima linha


def show_log(auth, user, seconds):
    for line in log_lines(auth, user, seconds):
        print(line)
    return 0


# Linhas que so aparecem depois que a instalacao engrenou (internet e GPU funcionando).
PROGRESS_MARKS = ("Tesla", "NVIDIA", "baixando ", "Reading package lists", "Instalação concluída")


def early_outcome(auth, user, seconds=120):
    """Acompanha o comeco da execucao, que e quando aparecem os problemas de conta (sem
    internet, sem GPU). Devolve ("ok", ""), ("erro", motivo) ou ("?", "") se nada conclusivo."""
    deadline = time.time() + seconds
    pause = float(os.environ.get("WAN_POLL_SECONDS", "10"))
    while time.time() < deadline:
        for line in log_lines(auth, user, min(20.0, max(1.0, deadline - time.time()))):
            if "SystemExit:" in line:
                return "erro", line.split("SystemExit:", 1)[1].strip()
            if any(mark in line for mark in PROGRESS_MARKS):
                return "ok", ""
        state, failure = patient_status(auth, user)
        if state and state not in (*ON, OFFLINE):
            return "erro", failure or f"a sessão terminou ({state})"
        time.sleep(pause)
    return "?", ""


def main():
    parser = argparse.ArgumentParser(description="Liga a GPU do Wan Studio pela API do Kaggle.")
    parser.add_argument("comando", nargs="?", default="ligar", choices=("ligar", "status", "registro"))
    parser.add_argument("--sem-navegador", action="store_true", help="não abre o site ao terminar")
    parser.add_argument("--segundos", type=float, default=30, help="por quanto tempo acompanhar o registro")
    args = parser.parse_args()

    print("Wan Studio · GPU gratuita do Kaggle\n")
    try:
        auth, user = credentials(browser=not args.sem_navegador)
        print(f"Conta: {user}")
        quota = quota_line(auth)
        if quota:
            print(quota)
        print()
        if args.comando == "status":
            return show_status(auth, user)
        if args.comando == "registro":
            return show_log(auth, user, args.segundos)
        return start(auth, user, browser=not args.sem_navegador)
    except KaggleError as error:
        hint = " Gere um token novo e apague free\\kaggle_token.txt." if error.status == 401 else ""
        print(f"\nNão deu certo: {error}.{hint}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
