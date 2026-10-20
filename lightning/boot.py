"""Liga o Wan Studio dentro de um Studio do Lightning AI. Quem o chama e lightning/ligar.py,
pelo comando remoto; a saida vai para logs/boot.log.

Faz o mesmo que o notebook do Kaggle (free/launcher.py: instalar, ligar, manter no ar) e, no
fim, desliga a maquina: no Lightning o tempo ligado gasta creditos, e um servidor no ar impede
o desligamento automatico da plataforma.

  WAN_IDLE_MINUTES   minutos sem uso ate desligar (padrao 15)
  WAN_PERSIST_GB     quanto se pode gravar no disco do Studio, que e guardado entre sessoes
                     (0: desconhecido). Com 60 ou mais os modelos ficam nele e nao sao baixados
                     de novo; com menos, vao para um disco temporario da maquina, se houver.
  WAN_KEEP_ON=1      nao desliga a maquina no fim (para investigar um problema)
  WAN_FAILURE_GRACE  segundos ligada depois de uma falha, antes de desligar (padrao 120): e o
                     tempo que lightning/ligar.py tem para ler o registro e mostrar o motivo
"""

import base64
import json
import os
import shutil
import sys
import threading
import time
import traceback
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "logs" / "boot.pid"
API = os.environ.get("LIGHTNING_CLOUD_URL", "https://lightning.ai").rstrip("/")
MODELS_GB = 60        # o que os modelos da troca de pessoa e do 5B ocupam, com folga
KEEP_AWAKE_EVERY = 30
TEMPORARY = ("/tmp", "/cache", "/var/tmp", "/mnt", "/home/zeus")

sys.path.insert(0, str(ROOT / "free"))
import launcher  # noqa: E402


def credentials():
    """O cabecalho de autenticacao da conta dona do Studio, ou None: o Lightning deixa o id do
    usuario e a chave no ambiente; se nao, estao no arquivo em que a ferramenta oficial os
    guarda. Com o id, autenticacao basica; so com a chave, como portador."""
    user, key = os.environ.get("LIGHTNING_USER_ID"), os.environ.get("LIGHTNING_API_KEY")
    if not key:
        try:
            saved = json.loads((Path.home() / ".lightning" / "credentials.json").read_text(encoding="utf-8"))
            user, key = saved.get("user_id"), saved.get("api_key")
        except (OSError, ValueError, AttributeError):
            return None
    if not key:
        return None
    return "Basic " + base64.b64encode(f"{user}:{key}".encode()).decode() if user else f"Bearer {key}"


def studio_call(action):
    """POST numa rota do proprio Studio ("stop", "keep-alive"). Devolve None se deu certo, ou
    o motivo."""
    project, space = os.environ.get("LIGHTNING_CLOUD_PROJECT_ID"), os.environ.get("LIGHTNING_CLOUD_SPACE_ID")
    auth = credentials()
    if not (project and space and auth):
        return "esta sessão não informa a conta nem o Studio"
    request = urllib.request.Request(
        f"{API}/v1/projects/{project}/cloudspaces/{space}/{action}", data=b"{}", method="POST",
        headers={"Authorization": auth, "Content-Type": "application/json", "User-Agent": "wan-studio/1.0"})
    try:
        urllib.request.urlopen(request, timeout=30).close()
    except OSError as error:
        return str(error)
    return None


def keep_awake(finished):
    """Enquanto o Wan Studio decide que ainda ha uso, avisa a plataforma: assim quem desliga
    por ociosidade e so o proprio Wan Studio, que sabe se ha video sendo gerado."""
    while not finished.wait(KEEP_AWAKE_EVERY):
        studio_call("keep-alive")


def power_off():
    if os.environ.get("WAN_KEEP_ON"):
        return print("WAN_KEEP_ON: a máquina continua ligada.")
    problem = studio_call("stop")
    if problem is None:
        return print("Máquina desligada.")
    try:  # a ferramenta oficial, que vem instalada nos Studios
        from lightning_sdk import Studio

        Studio().stop()
        print("Máquina desligada.")
    except Exception as error:
        print(f"Não consegui desligar a máquina ({problem}; {error}). Sem nada rodando, o Lightning "
              "a desliga sozinho em uns 10 minutos.")


def choose_storage():
    """Decide onde ficam os modelos e ajusta o ambiente que o instalador le."""
    # A sessao enxuta (so a troca de rosto em video) ocupa uns 9 GB; a completa, uns 60.
    need = 12 if os.environ.get("SO_ROSTO") else MODELS_GB
    persist = int(float(os.environ.get("WAN_PERSIST_GB") or 0))
    if persist >= need:
        os.environ["DISK_BUDGET_GB"] = str(persist)
        return print(f"Modelos no disco do Studio (até {persist} GB): ficam guardados entre as sessões.")
    # O que fica na pasta do Studio e guardado pelo Lightning e conta para o disco da conta;
    # o resto da maquina e apagado quando ela desliga. Vale a pasta, nao o disco: na maquina
    # conferida em 12/10/2026 as duas coisas estavam no mesmo disco de 387 GB.
    kept = ROOT.parent.resolve()
    for folder in TEMPORARY:
        try:
            path = Path(folder).resolve()
            free = shutil.disk_usage(folder).free // 2**30
        except OSError:
            continue
        outside = kept != path and kept not in path.parents
        if outside and free >= need and os.access(folder, os.W_OK):
            os.environ["MODELS_DIR"] = f"{folder}/wan-models"
            return print(f"Modelos em {folder}/wan-models ({free} GB livres), fora do disco guardado: serão "
                         "baixados de novo a cada vez que a máquina ligar.")
    if persist:  # a cota e conhecida e nao comporta os modelos: nao gravar alem dela
        raise SystemExit(f"Não há onde pôr os modelos: o disco guardado da conta é de {persist} GB e a máquina "
                         "não tem um disco temporário grande o bastante.")
    os.environ["DISK_BUDGET_GB"] = "45"
    print("Sem disco temporário grande o bastante: modelos no disco do Studio, até 45 GB.")


def single():
    """Uma inicializacao por vez: um segundo clique em Ligar GPU nao pode subir tudo de novo.

    O arquivo com o numero do processo fica no disco do Studio e sobrevive a um desligamento;
    na sessao seguinte o mesmo numero pode ser de outro programa. Por isso so vale se o
    processo com esse numero for mesmo um iniciador."""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    try:
        other = int(LOCK.read_text(encoding="utf-8"))
        busy = b"boot.py" in Path(f"/proc/{other}/cmdline").read_bytes()
    except (OSError, ValueError):
        busy = False
    if busy:
        print(f"Já há uma inicialização em andamento (processo {other}).")
        return False
    LOCK.write_text(str(os.getpid()), encoding="utf-8")
    return True


def main():
    if not single():
        return 0
    # No Lightning a sessao sobe enxuta, so para a troca de rosto em video (ComfyUI + FaceFusion,
    # uns 9 GB): e o que cabe bem no credito gratuito. WAN_FULL=1 traz o estudio inteiro de volta.
    if not os.environ.get("WAN_FULL"):
        os.environ.setdefault("SO_ROSTO", "1")
    finished = threading.Event()
    threading.Thread(target=keep_awake, args=(finished,), daemon=True).start()
    code = 1
    try:
        choose_storage()
        launcher.install()
        launcher.start(idle_minutes=float(os.environ.get("WAN_IDLE_MINUTES") or 15))
        launcher.keepalive()
        code = 0
    except SystemExit as error:  # o launcher encerra assim quando algo essencial falha
        print(f"SystemExit: {error}")
    except Exception:
        traceback.print_exc()
    finally:
        launcher.stop(quiet=True)
        if code:  # com a maquina desligada o registro nao pode mais ser lido
            grace = float(os.environ.get("WAN_FAILURE_GRACE") or 120)
            print(f"A inicialização falhou. A máquina será desligada em {grace:.0f} segundos.", flush=True)
            time.sleep(grace)
        finished.set()
        LOCK.unlink(missing_ok=True)
        power_off()
    return code


if __name__ == "__main__":
    sys.exit(main())
