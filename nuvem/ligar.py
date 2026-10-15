"""Liga o Wan Studio numa GPU grande do Modal, que faz a troca de pessoa em minutos, e abre o
site ja pareado. No Windows: dois cliques em "Ligar GPU rapida.bat".

  python nuvem/ligar.py              liga (ou avisa que ja esta ligada) e abre o site
  python nuvem/ligar.py status       a sessao, a placa e o credito gasto no mes
  python nuvem/ligar.py registro     o que a maquina esta fazendo
  python nuvem/ligar.py desligar     desliga a sessao (para de gastar credito)
  python nuvem/ligar.py modelos      baixa os modelos que faltam (--tudo: tambem o 14B)
  python nuvem/ligar.py publicar     envia o codigo de novo ao Modal
  python nuvem/ligar.py sessao       endereco e senha da sessao, em JSON (para os testes)

  --gpu H100          a placa: L40S, A100, H100 (padrao), H200, B200...
  --ocioso 5          minutos sem uso ate a sessao se desligar sozinha
  --sem-navegador     nao abre o site

O Modal (https://modal.com) da 30 dolares de credito por mes e cobra por segundo de maquina
ligada. Ele exige um cartao cadastrado (modal.com/settings/usage); com o limite de gastos
("spend limit") no minimo, as maquinas param quando o credito acaba em vez de o cartao ser
cobrado. A autorizacao da conta fica em ~/.modal.toml: quem tiver esse arquivo usa a conta.

Na primeira vez este script cria nuvem/.venv e instala nele o cliente do Modal.
"""

import argparse
import base64
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
import warnings
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
# Onde ficam o ambiente do cliente do Modal e a marca do que foi publicado; os testes usam
# outra pasta (e outro pareamento), para nao mexer no que e de verdade.
KEPT = Path(os.environ.get("WAN_NUVEM_HOME", HERE))
VENV = KEPT / ".venv"
PUBLISHED = KEPT / ".publicado.json"
PAIRING = Path(os.environ.get("WAN_PAIRING_FILE", ROOT / "free" / "pairing.json"))
MODAL_VERSION = "1.6.1"
NAME = os.environ.get("WAN_NUVEM_APP", "wan-studio")
BILLING_PAGE = "https://modal.com/settings/usage"
FREE_CREDIT = 30.0
# Dolares por hora de cada placa (modal.com/pricing em 13/10/2026). Fora a placa, a maquina
# cobra processador e memoria: uns 0,45 por hora com o que a sessao pede.
PRICES = {"T4": 0.59, "L4": 0.80, "A10": 1.10, "L40S": 1.95, "A100": 2.10, "A100-40GB": 2.10,
          "A100-80GB": 2.50, "RTX-PRO-6000": 3.03, "H100": 3.95, "H100!": 3.95, "H200": 4.54,
          "B200": 6.25, "B200+": 6.25, "B300": 7.10}
MACHINE_OVERHEAD = 0.45
DEFAULT_GPU = "H100"
# O que entra na publicacao: mudou um destes, publica de novo.
SOURCES = ("nuvem/wan_nuvem.py", "nuvem/sessao.py", "free/launcher.py", "vm", "app")
WAIT_UP = 15 * 60

sys.path.insert(0, str(HERE))


# ---------------------------------------------------------------- cliente do Modal

def python_of(venv):
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ensure_modal():
    """Garante o cliente do Modal: se este Python nao o tem, cria nuvem/.venv, instala nele e
    roda este mesmo script de la."""
    try:
        import modal  # noqa: F401
        return
    except ImportError:
        pass
    if os.environ.get("WAN_NUVEM_VENV"):  # ja e o ambiente proprio e ainda falta: nao insiste
        raise SystemExit("Não consegui instalar o cliente do Modal. Tente: pip install modal")
    python = python_of(VENV)
    if not python.exists():
        print("Preparando o cliente do Modal (só na primeira vez, um minuto ou dois)…", flush=True)
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
    if subprocess.run([str(python), "-c", "import modal"], capture_output=True).returncode != 0:
        code = subprocess.run([str(python), "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                               f"modal=={MODAL_VERSION}"]).returncode
        if code:
            raise SystemExit("Não consegui instalar o cliente do Modal (o computador está sem internet?).")
    env = {**os.environ, "WAN_NUVEM_VENV": "1", "PYTHONIOENCODING": "utf-8"}
    sys.exit(subprocess.call([str(python), str(Path(__file__).resolve()), *sys.argv[1:]], env=env))


def modal_cli(*args):
    """Roda um comando do cliente do Modal neste mesmo Python, com a saida na tela."""
    env = {**os.environ, "PYTHONWARNINGS": "ignore", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    return subprocess.run([sys.executable, "-m", "modal", *args], env=env).returncode


def logged_in():
    from modal.config import config

    return bool(config.get("token_id") and config.get("token_secret"))


def login():
    print("Para usar a GPU rápida preciso da autorização da sua conta do Modal (só nesta vez).\n")
    print("  1. Vou abrir o Modal no navegador: entre com o Google ou o GitHub (cria a conta na hora)")
    print('  2. Clique em "Approve" quando ele pedir para autorizar este computador')
    print(f"  3. Cadastre um cartão em {BILLING_PAGE} (o Modal exige para ligar máquinas) e, na mesma")
    print('     página, ponha o "spend limit" no mínimo: assim ele para quando o crédito gratuito')
    print("     do mês acaba, em vez de cobrar\n")
    if modal_cli("token", "new") != 0:
        raise SystemExit("\nSem a autorização do Modal não consigo ligar a GPU.")
    print()


def explain(error):
    """Uma falha do Modal em palavras que ajudem: a mais comum e a conta sem cartao."""
    text = str(error)
    low = text.lower()
    if any(word in low for word in ("payment", "billing", "credit card", "spend limit", "usage limit", "budget")):
        return (f"{text}\n\nO Modal só liga máquinas com um cartão cadastrado e dentro do limite de gastos. "
                f"Confira em {BILLING_PAGE}.")
    if "not found" in low or "notfounderror" in type(error).__name__.lower():
        return f"{text}\n\nO Wan Studio ainda não está publicado no Modal. Rode: python nuvem\\ligar.py publicar"
    return text


# ---------------------------------------------------------------- publicacao

def source_hash():
    digest = hashlib.sha256()
    for entry in SOURCES:
        source = ROOT / entry
        paths = sorted(source.rglob("*")) if source.is_dir() else [source]
        for path in paths:
            if path.is_file() and "__pycache__" not in path.parts:
                digest.update(path.relative_to(ROOT).as_posix().encode())
                # Sem os fins de linha: o mesmo arquivo vale igual no Windows e fora dele.
                digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def published():
    try:
        return json.loads(PUBLISHED.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def deployed():
    """O app esta publicado na conta? (pode ter sido apagado por la)"""
    import modal

    try:
        modal.Function.from_name(NAME, "sessao").hydrate()
        return True
    except modal.exception.NotFoundError:
        return False


def publish(force=False):
    """Publica o app se o codigo mudou desde a ultima vez (ou se nao esta na conta)."""
    current = source_hash()
    if not force and published().get("hash") == current and deployed():
        return False
    first = not published().get("hash")
    print("Publicando o Wan Studio no Modal" + (" (na primeira vez ele monta a imagem: uns 8 minutos)…"
                                                 if first else "…"), flush=True)
    if modal_cli("deploy", str(HERE / "wan_nuvem.py")) != 0:
        raise SystemExit("\nA publicação no Modal falhou: veja a mensagem acima.")
    PUBLISHED.parent.mkdir(parents=True, exist_ok=True)
    PUBLISHED.write_text(json.dumps({"hash": current, "when": int(time.time())}) + "\n", encoding="utf-8",
                         newline="\n")
    print()
    return True


# ---------------------------------------------------------------- modelos

def missing_models():
    """O que falta no volume para a troca de pessoa (a mesma lista que a sessao confere)."""
    import modal
    import sessao

    volume = modal.Volume.from_name(f"{NAME}-modelos", create_if_missing=True)
    try:
        listed = {entry.path.strip("/") for entry in volume.listdir("/models", recursive=True)}
    except (modal.exception.NotFoundError, FileNotFoundError):
        listed = set()
    missing = [f"{folder}/{name}" for folder, name in sessao.ESSENTIAL if f"models/{folder}/{name}" not in listed]
    if not any(path.startswith("models/diffusion_models/") and "Animate" in path for path in listed):
        missing.append("diffusion_models/(Wan 2.2 Animate)")
    return missing


def interesting(line):
    """As linhas do instalador que valem mostrar: o que esta baixando e o andamento."""
    line = line.strip()
    return line.startswith(("baixando ", "ok  ", "aviso", "pulando", "modelos ", "downloads ")) or "ETA:" in line


def download_models(everything=False):
    import modal

    what = "todos os modelos (uns 85 GB)" if everything else "os modelos da troca de pessoa e do 5B (uns 48 GB)"
    print(f"Baixando {what} para o seu volume no Modal. É uma vez só; leva de 5 a 15 minutos e")
    print("não usa GPU nem a internet deste computador.\n", flush=True)
    call = modal.Function.from_name(NAME, "baixar").spawn(everything)
    try:
        for entry in call.logs.stream(timeout=600):
            for line in str(entry.message).splitlines():
                if interesting(line):
                    print("  " + line.strip()[:110], flush=True)
    except Exception as error:  # o registro e so para acompanhar: o resultado vem abaixo
        print(f"  (parei de receber o registro: {error}; o download continua)")
    files = call.get()
    print(f"\nModelos prontos: {len(files)} arquivos no volume.\n")


# ---------------------------------------------------------------- sessao

def state_dict():
    import modal

    return modal.Dict.from_name(f"{NAME}-estado", create_if_missing=True)


def load_pairing():
    """O segredo que liga o site a sessao e o mesmo do Kaggle (free/build.py o cria)."""
    if not PAIRING.exists():
        subprocess.run([sys.executable, str(ROOT / "free" / "build.py")], check=True, capture_output=True)
    return json.loads(PAIRING.read_text(encoding="utf-8"))


def kaggle_launcher():
    """free/ligar.py, de onde vem o jeito de abrir o site ja pareado (o nome do arquivo e o
    mesmo deste: por isso e carregado pelo caminho)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("ligar_kaggle", ROOT / "free" / "ligar.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.PAIRING = PAIRING
    if os.environ.get("WAN_OPENER_FILE"):
        module.OPENER = Path(os.environ["WAN_OPENER_FILE"])
    return module


def wake_info(state, config):
    """Guarda no Modal a configuracao com que o site liga a sessao sozinho, e devolve o que o
    site precisa para isso: o endereco do `acordar` e a chave (criada uma vez so)."""
    import modal

    saved = state.get("wake") or {}
    key = saved.get("key") or secrets.token_urlsafe(24)
    state["wake"] = {"key": key, "config": config}
    try:
        url = modal.Function.from_name(NAME, "acordar").get_web_url()
    except Exception:
        url = None
    return {"w": url, "wk": key} if url else {}


def price(gpu):
    return PRICES.get(gpu, PRICES[DEFAULT_GPU]) + MACHINE_OVERHEAD


def month_usage(state, now=None):
    """Dolares gastos neste mes (pelo calendario), pela conta das sessoes registradas, e os
    segundos de maquina. E uma estimativa: a fatura de verdade esta no site do Modal."""
    import sessao

    now = now or time.time()
    month = time.gmtime(now)[:2]
    entries = [entry for entry in state.get("usage") or [] if time.gmtime(entry["started"])[:2] == month]
    current = state.get("session")
    if sessao.alive(current, now):
        entries.append({"gpu": current.get("gpu", ""), "seconds": now - current["started"]})
    seconds = sum(entry["seconds"] for entry in entries)
    return sum(entry["seconds"] / 3600 * price(entry.get("gpu")) for entry in entries), seconds


def usage_line(state):
    spent, seconds = month_usage(state)
    left = max(0.0, FREE_CREDIT - spent)
    return (f"Crédito deste mês (estimativa): US$ {spent:.2f} usados de {FREE_CREDIT:.0f} "
            f"({seconds / 3600:.1f} h de máquina); o que resta paga umas {left / price(DEFAULT_GPU) * 60:.0f} min "
            f"de {DEFAULT_GPU}.")


def api(record, path, method="GET", data=None, timeout=30):
    """Uma chamada a API do Wan Studio da sessao."""
    token = base64.b64encode(f"wan:{record['password']}".encode()).decode()
    request = urllib.request.Request(record["url"] + path, data=data, method=method, headers={
        "Authorization": f"Basic {token}", "User-Agent": "wan-studio", "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read() or b"null")


def wait_up(state, session_id, seconds=WAIT_UP):
    """Acompanha a subida: devolve o registro da sessao quando ela anuncia o endereco, ou o
    que houver quando ela para ou o tempo acaba."""
    import sessao

    deadline, shown = time.time() + seconds, None
    while time.time() < deadline:
        record = state.get("session") or {}
        if record.get("id") != session_id:
            return record
        stage = record.get("stage")
        if stage != shown:
            shown = stage
            print({"pedida": "  pedindo a máquina ao Modal…", "iniciando": "  a máquina ligou; subindo o ComfyUI…",
                   "no ar": "  no ar."}.get(stage, f"  {stage}"), flush=True)
        if record.get("url") or record.get("ended") or not sessao.alive(record):
            return record
        time.sleep(float(os.environ.get("WAN_POLL_SECONDS", "3")))
    return state.get("session") or {}


def start(args):
    import sessao
    import wan_nuvem

    kaggle = kaggle_launcher()

    publish()
    missing = missing_models()
    if missing:
        download_models()
    state = state_dict()
    pairing = load_pairing()
    config = {"gpu": args.gpu, "idle_minutes": args.ocioso, "site": pairing.get("site", ""), "pairing": pairing,
              "comfy_args": os.environ.get("WAN_COMFY_ARGS", ""), "price": round(price(args.gpu), 2)}
    extra = wake_info(state, config)
    print(usage_line(state))

    reply = wan_nuvem.request_session(config)
    if reply["state"] == "on":
        print(f"\nA GPU já está ligada ({reply['gpu']}). Abrindo o site…")
        kaggle.open_site(starting=int(time.time()), browser=not args.sem_navegador, extra=extra)
        return 0
    started = int(time.time())
    print(f"\nLigando uma {reply['gpu']} (US$ {price(reply['gpu']):.2f} por hora, cobrados por segundo)…", flush=True)
    kaggle.open_site(starting=started, browser=not args.sem_navegador, extra=extra)
    record = wait_up(state, reply["id"])
    if record.get("url"):
        took = int(time.time() - started)
        print(f"\nPronto em {took // 60} min {took % 60} s. O site já conectou (ou conecta em instantes).")
        print(f"Ela se desliga sozinha após {args.ocioso:g} min sem uso; para desligar antes: no site, o "
              "indicador verde → Desligar GPU.")
        return 0
    if record.get("ended"):
        print(f"\nA sessão parou ao ligar: {record.get('stage')}")
        print("Veja o registro com: python nuvem\\ligar.py registro")
        return 1
    if sessao.alive(record):
        print("\nO Modal ainda não entregou a máquina (a placa pode estar disputada). Pode fechar esta janela: "
              "quando ela ligar, o site conecta sozinho. Outra placa: python nuvem\\ligar.py --gpu L40S")
        return 0
    print("\nA sessão não deu sinal. Veja o registro com: python nuvem\\ligar.py registro")
    return 1


def show_status(_args):
    import sessao

    state = state_dict()
    record = state.get("session")
    if sessao.alive(record):
        minutes = (time.time() - record["started"]) / 60
        doing = "gerando" if record.get("busy") else "ociosa" if record.get("url") else "ligando"
        print(f"GPU: ligada ({record.get('gpu')}), {doing}, há {minutes:.0f} min.")
    elif record:
        print(f"GPU: desligada. Última sessão: {record.get('stage', '?')}.")
    else:
        print("GPU: nunca ligada por aqui.")
    print(usage_line(state))
    return 0


def show_log(_args):
    import modal

    for entry in modal.Function.from_name(NAME, "sessao").logs.tail(200):
        print(str(entry.message).rstrip("\n"))
    return 0


def show_session(_args):
    import sessao

    record = state_dict().get("session")
    if not (sessao.alive(record) and record.get("url")):
        raise SystemExit("a sessão não está no ar (python nuvem\\ligar.py status)")
    print(json.dumps({"url": record["url"], "password": record["password"], "gpu": record.get("gpu")}))
    return 0


def shut_down(_args):
    import modal
    import sessao

    state = state_dict()
    record = state.get("session")
    if not sessao.alive(record):
        print("A GPU já está desligada.")
        return 0
    if record.get("url"):
        try:
            api(record, "/api/shutdown", "POST", b"{}")
        except (OSError, ValueError) as error:
            print(f"(a sessão não respondeu ao pedido: {error}; cancelando pelo Modal)")
            record = {**record, "url": None}
    if not record.get("url") and record.get("call"):
        modal.FunctionCall.from_id(record["call"]).cancel(terminate_containers=True)
    print("Desligamento pedido…", flush=True)
    deadline = time.time() + 120
    while time.time() < deadline:
        if not sessao.alive(state.get("session")):
            print("GPU desligada.")
            print(usage_line(state))
            return 0
        time.sleep(float(os.environ.get("WAN_POLL_SECONDS", "3")))
    print("A sessão ainda não confirmou. Confira com: python nuvem\\ligar.py status")
    return 1


def main():
    parser = argparse.ArgumentParser(description="Liga a GPU rápida do Wan Studio (Modal).")
    parser.add_argument("comando", nargs="?", default="ligar",
                        choices=("ligar", "status", "registro", "desligar", "modelos", "publicar", "sessao"))
    parser.add_argument("--gpu", default=DEFAULT_GPU, help="a placa (L40S, A100, H100, H200, B200…)")
    parser.add_argument("--ocioso", type=float, default=5, help="minutos sem uso até desligar sozinha")
    parser.add_argument("--tudo", action="store_true", help="modelos: baixa também o 14B")
    parser.add_argument("--forcar", action="store_true", help="publicar: mesmo sem mudança no código")
    parser.add_argument("--sem-navegador", action="store_true", help="não abre o site ao terminar")
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):  # o console do Windows nao escreve tudo o que o registro traz
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    ensure_modal()
    import modal

    if args.comando != "sessao":
        print("Wan Studio · GPU rápida (Modal)\n")
    if not logged_in():
        login()
    try:
        if args.comando == "status":
            return show_status(args)
        if args.comando == "registro":
            return show_log(args)
        if args.comando == "sessao":
            return show_session(args)
        if args.comando == "desligar":
            return shut_down(args)
        if args.comando == "publicar":
            publish(force=True)
            print("Publicado.")
            return 0
        if args.comando == "modelos":
            publish()
            if args.tudo or missing_models():
                download_models(everything=args.tudo)
            else:
                print("Os modelos da troca de pessoa já estão no volume.")
            return 0
        return start(args)
    except modal.exception.Error as error:
        print(f"\nNão deu certo: {explain(error)}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
