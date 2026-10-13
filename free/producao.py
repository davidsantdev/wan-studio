"""Producao em massa de trocas de pessoa: uma pasta de videos entra, uma pasta de videos prontos
sai. No Windows: dois cliques em "Produzir em massa.bat".

  producao/pessoa/    a imagem da pessoa (uma so)
  producao/videos/    os videos de origem, quantos quiser
  producao/prontos/   onde os resultados aparecem
  producao/usados/    para onde cada video de origem vai depois de pronto
  producao/opcoes.txt qualidade, duracao, alta definicao, formato do Instagram...

O script liga a GPU do Kaggle se ela estiver desligada, espera a troca de pessoa ficar
instalada, envia os videos (dois rodam ao mesmo tempo, os outros esperam na fila), baixa cada um
quando fica pronto, converte para o formato do Instagram e, no fim, desliga a GPU. Pode ser
interrompido e rodado de novo: continua de onde parou.

  python free/producao.py            produz tudo o que estiver em producao/videos
  python free/producao.py desligar   desliga a GPU (se nao houver video em andamento)

Usa a mesma API que o site, com os videos marcados para o site nao os levar para a galeria do
navegador. So biblioteca padrao; o ffmpeg, se existir, e usado para encurtar os videos antes do
envio e para a conversao final.
"""

import base64
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

FREE = Path(__file__).resolve().parent
ROOT = FREE.parent
sys.path.insert(0, str(FREE))
import ligar  # noqa: E402

BASE = Path(os.environ.get("WAN_PRODUCAO_DIR") or ROOT / "producao")
PERSON, VIDEOS, READY, USED = BASE / "pessoa", BASE / "videos", BASE / "prontos", BASE / "usados"
OPTIONS, LEDGER, ERRORS = BASE / "opcoes.txt", BASE / ".andamento.json", BASE / "erros.txt"
IMAGES = (".png", ".jpg", ".jpeg", ".webp")
CLIPS = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi")
MAX_UPLOAD = 100 * 1024 * 1024  # o teto do servidor (e do tunel gratuito)
BUSY = ("preparing", "queued", "running")
POLL = float(os.environ.get("WAN_POLL_SECONDS", "10"))
DEFAULTS = """\
# Opções da produção em massa. Mude o que vem depois do sinal de igual e salve.

qualidade = boa          # rapida (uns 13 min por 10 s), boa (uns 28 min) ou maxima (uns 55 min)
segundos = 10            # quanto de cada vídeo usar (de 1 a 60)
comecar_em = 0           # segundos a pular no começo de cada vídeo (ex.: 1.5, se ele começa de costas)
alta_definicao = nao     # "sim": bem mais nitidez (720p restaurado), mas cada vídeo leva o dobro do tempo
fluido = sim             # movimento suave: os quadros entre os gerados são calculados (segundos a mais)
instagram = sim          # entrega no formato do Instagram (1080 de largura, 30 quadros por segundo)
manter_cenario = sim     # "nao": a pessoa só copia os movimentos, no cenário da própria imagem
desligar_no_fim = sim    # desliga a GPU quando a lista acabar, para poupar a sua cota
"""
README = """\
Produção em massa do Wan Studio

1. Ponha em "pessoa" a imagem da pessoa (uma só).
2. Ponha em "videos" os vídeos de origem.
3. Na pasta do Wan Studio, dê dois cliques em "Produzir em massa".
4. Os resultados aparecem em "prontos". Cada vídeo de origem, depois de feito, vai para "usados".

Para sair bom (medido na GPU em 12/10/2026):
- A imagem: em pé, como os vídeos, e com o mesmo enquadramento deles (meio corpo se os vídeos
  mostram meio corpo, corpo inteiro se mostram o corpo inteiro). Rosto bem visível.
- Os vídeos: uma pessoa só, e o trecho usado deve começar com ela de frente e com um pouco de
  cenário aparecendo em volta. Começando de costas e enchendo a tela, o modelo inventa coisas.

As opções (qualidade, duração, alta definição...) ficam em "opcoes.txt".
Dois vídeos são feitos ao mesmo tempo; os outros esperam na fila. Pode fechar a janela e abrir
de novo depois: ela continua de onde parou. O computador precisa estar ligado para os
resultados serem baixados.
"""


def stamp():
    return time.strftime("%H:%M")


def say(text=""):
    print(text, flush=True)


# ---------------------------------------------------------------- pastas e opcoes

def prepare_folders():
    for folder in (PERSON, VIDEOS, READY, USED):
        folder.mkdir(parents=True, exist_ok=True)
    if not OPTIONS.exists():
        OPTIONS.write_text(DEFAULTS, encoding="utf-8")
    if not (BASE / "LEIA-ME.txt").exists():
        (BASE / "LEIA-ME.txt").write_text(README, encoding="utf-8")


def options():
    """As opcoes do arquivo, com os padroes para o que faltar ou estiver errado."""
    values = {}
    for text in (DEFAULTS, OPTIONS.read_text(encoding="utf-8", errors="replace") if OPTIONS.exists() else ""):
        for line in text.splitlines():
            line = line.split("#", 1)[0]
            if "=" in line:
                key, value = line.split("=", 1)
                values[key.strip().lower()] = value.strip().lower()

    def yes(key):
        return values[key] in ("sim", "s", "yes", "1", "true")
    try:
        seconds = min(60.0, max(1.0, float(values["segundos"].replace(",", "."))))
    except ValueError:
        seconds = 10.0
    try:
        start = min(3600.0, max(0.0, float(values["comecar_em"].replace(",", "."))))
    except ValueError:
        start = 0.0
    quality = values["qualidade"].replace("á", "a").replace("ú", "u")
    return {"quality": quality if quality in ("rapida", "boa", "maxima") else "boa", "seconds": seconds, "start": start,
            "hd": yes("alta_definicao"), "fluid": yes("fluido"), "instagram": yes("instagram"),
            "replace": yes("manter_cenario"), "shutdown": yes("desligar_no_fim")}


def inputs():
    """(imagem da pessoa ou None, videos em ordem alfabetica)."""
    people = sorted(path for path in PERSON.iterdir() if path.suffix.lower() in IMAGES)
    clips = sorted(path for path in VIDEOS.iterdir() if path.suffix.lower() in CLIPS)
    return (people[0] if people else None), clips, len(people)


def ledger():
    try:
        return json.loads(LEDGER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def remember(data):
    LEDGER.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- video local (ffmpeg)

def tool(name):
    return shutil.which(name)


def run(*command):
    return subprocess.run([str(part) for part in command], capture_output=True, text=True, errors="replace")


def shorten(source, seconds, workdir, start=0.0):
    """O trecho que sera usado (`seconds` a partir de `start`), em tamanho moderado: o envio
    fica pequeno (um video de celular tem dezenas de MB; o estudio so precisa de uns 10 s em
    baixa resolucao). Sem ffmpeg, ou se ele falhar, vai o arquivo como esta."""
    if not tool("ffmpeg"):
        return source
    target = workdir / (source.stem + ".envio.mp4")
    done = run("ffmpeg", "-nostdin", "-y", "-v", "error", "-ss", f"{start:.2f}", "-i", source, "-t", f"{seconds + 0.5:.2f}",
               "-vf", "scale='if(gt(iw,ih),-2,min(720,iw))':'if(gt(iw,ih),min(720,ih),-2)'",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", target)
    return target if done.returncode == 0 and target.exists() and target.stat().st_size else source


def size_of(path):
    done = run("ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
               "-of", "csv=p=0", path)
    try:
        width, height = (int(value) for value in done.stdout.strip().split(",")[:2])
        return width, height
    except ValueError:
        return None


def instagram(source, target):
    """Converte para o que o Instagram pede: 1080 de largura (em pe, quadrado ou deitado, o que
    estiver mais perto do video), 30 quadros por segundo, H.264 e AAC. Devolve se deu certo."""
    size = size_of(source) if tool("ffmpeg") and tool("ffprobe") else None
    if not size:
        return False
    ratio = size[0] / size[1]
    width, height = (1080, 1920) if ratio < 0.8 else (1920, 1080) if ratio > 1.25 else (1080, 1080)
    done = run("ffmpeg", "-nostdin", "-y", "-v", "error", "-i", source,
               "-vf", f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,crop={width}:{height},fps=30",
               "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-profile:v", "high", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-movflags", "+faststart", target)
    return done.returncode == 0 and target.exists() and target.stat().st_size > 0


def free_name(folder, stem, suffix=".mp4"):
    target, number = folder / f"{stem}{suffix}", 2
    while target.exists():
        target, number = folder / f"{stem} ({number}){suffix}", number + 1
    return target


# ---------------------------------------------------------------- sessao e API do estudio

class Gone(Exception):
    """A sessao nao responde mais."""


def find_session(auth, user):
    """(endereco, cabecalhos) da sessao ligada, pelo quadro que o notebook imprime no registro do
    Kaggle; (None, None) enquanto ele nao apareceu. WAN_SESSION="endereco|senha" dispensa isso."""
    fixed = os.environ.get("WAN_SESSION")
    if fixed:
        url, _, password = fixed.partition("|")
    else:
        text = "\n".join(ligar.log_lines(auth, user, 12))
        found = re.findall(r"(https://[a-z0-9-]+\.trycloudflare\.com)\s+usu.rio: wan\s+senha: (\S+)", text)
        if not found:
            return None, None
        url, password = found[-1]
    token = base64.b64encode(f"wan:{password}".encode()).decode()
    return url.rstrip("/"), ({"Authorization": f"Basic {token}"} if password else {})


def call(session, path, data=None, content_type=None, method=None, timeout=120):
    url, headers = session
    request = urllib.request.Request(url + path, data=data, method=method, headers={
        "User-Agent": "wan-studio/1.0", **headers, **({"Content-Type": content_type} if content_type else {})})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def multipart(params, files):
    boundary = "----wan" + secrets.token_hex(8)
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="params"\r\n\r\n{json.dumps(params)}\r\n').encode()
    kinds = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".png": "image/png"}
    for field, path in files.items():
        kind = kinds.get(path.suffix.lower(), "video/mp4")
        name = "arquivo" + path.suffix.lower()  # nomes com acento ou aspas quebrariam o cabecalho
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{name}"\r\n'
                 f"Content-Type: {kind}\r\n\r\n").encode() + path.read_bytes() + b"\r\n"
    return body + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def studio_status(session):
    return json.loads(call(session, "/api/status", timeout=30))


def held_jobs(session):
    return {job["id"]: job for job in json.loads(call(session, "/api/jobs?hold=1", timeout=30))["jobs"]}


def power_on(auth, user, hd=False):
    """Garante a sessao ligada e devolve (endereco, cabecalhos) quando a troca de pessoa esta
    instalada. A fila do Kaggle pode levar mais de uma hora de dia; nada disso gasta a cota.
    Com `hd`, espera tambem a alta definicao, que e o ultimo download da troca de pessoa: sem
    isso, uma producao iniciada nesse intervalo sairia toda sem ela."""
    if not os.environ.get("WAN_SESSION"):
        state, _ = ligar.session_status(auth, user)
        if state not in ligar.ON:
            say("A GPU está desligada: ligando…")
            reply = ligar.push(auth, user)
            if reply.get("error"):
                raise SystemExit(f"O Kaggle recusou ligar a GPU: {reply['error']}")
        shown = None
        while True:
            state, failure = ligar.patient_status(auth, user)
            if state not in (*ligar.ON, ligar.OFFLINE, None):
                raise SystemExit(f"A sessão do Kaggle parou sem ligar ({state}). {failure}".rstrip())
            if state != shown:
                shown = state
                say(f"{stamp()} " + {"QUEUED": "na fila do Kaggle (de dia pode passar de uma hora; a cota não é gasta)…",
                                     "RUNNING": "a máquina ligou; instalando…"}.get(state, "esperando o Kaggle…"))
            if state == "RUNNING" and find_session(auth, user)[0]:
                break
            time.sleep(max(POLL, 15))
    session, shown = find_session(auth, user), None
    deadline = time.time() + 45 * 60
    while time.time() < deadline:
        try:
            status = studio_status(session)
        except (OSError, ValueError):
            status = None
            session = find_session(auth, user) if not os.environ.get("WAN_SESSION") else session
        swap = ((status or {}).get("models") or {}).get("animate") or {}
        coming = hd and swap.get("hd") != "seedvr2" and "Animate" in str((status or {}).get("extras") or "")
        if status and status.get("comfy") and swap.get("ok") and not coming:
            say(f"{stamp()} estúdio no ar: {status.get('gpu')}; troca de pessoa instalada"
                + ("" if swap.get("hd") == "seedvr2" else "; alta definição ainda não"))
            return session, status
        line = "baixando: " + str(status.get("extras")) if status and status.get("extras") else "esperando o estúdio responder…"
        if line != shown:
            shown = line
            say(f"{stamp()} {line}")
        time.sleep(max(POLL, 10))
    raise SystemExit("A troca de pessoa não ficou pronta em 45 minutos. Veja: python free\\ligar.py registro")


# ---------------------------------------------------------------- a producao

def submit(session, person, clip, chosen, workdir):
    params = {"mode": "swap", "duration": chosen["seconds"], "quality": chosen["quality"], "hd": chosen["hd"],
              "fluid": chosen["fluid"], "replace": chosen["replace"], "start": 0, "hold": True, "prompt": ""}
    upload = shorten(clip, chosen["seconds"], workdir, chosen["start"])
    if upload == clip:  # foi o arquivo inteiro: quem pula o comeco e o estudio
        params["start"] = chosen["start"]
    if upload.stat().st_size > MAX_UPLOAD:
        raise ValueError("o vídeo passa de 100 MB (instale o ffmpeg, ou corte um trecho menor)")
    body, content_type = multipart(params, {"start_image": person, "video": upload})
    try:
        reply = json.loads(call(session, "/api/generate", data=body, content_type=content_type, method="POST", timeout=600))
    except urllib.error.HTTPError as error:
        try:
            reason = json.loads(error.read()).get("error")
        except ValueError:
            reason = None
        if error.code >= 500:
            raise
        raise ValueError(reason or f"o estúdio recusou o vídeo ({error.code})") from None
    finally:
        if upload != clip:
            upload.unlink(missing_ok=True)
    return reply["jobs"][0]


def deliver(session, job, clip, chosen):
    """Baixa o resultado, converte e arquiva o video de origem. Devolve o arquivo entregue."""
    raw = READY / f".{job['id']}.baixando.mp4"
    raw.write_bytes(call(session, job["url"], timeout=900))
    target = free_name(READY, clip.stem)
    if not (chosen["instagram"] and instagram(raw, target)):
        raw.replace(target)
    raw.unlink(missing_ok=True)
    try:
        call(session, f"/api/jobs/{job['id']}", method="DELETE", timeout=30)
    except OSError:
        pass  # o arquivo ja esta aqui; o servidor apaga o dele quando a sessao acabar
    if clip.exists():
        clip.replace(free_name(USED, clip.stem, clip.suffix))
    return target


def failed(clip, reason):
    with ERRORS.open("a", encoding="utf-8") as handle:
        handle.write(f"{time.strftime('%d/%m %H:%M')}  {clip.name}: {reason}\n")
    say(f"{stamp()} ✗ {clip.name}: {reason}")


def produce(session, status, person, clips, chosen):
    """Envia, acompanha e entrega. Devolve (prontos, com erro)."""
    book = ledger()
    workdir = BASE / ".envio"
    workdir.mkdir(exist_ok=True)
    window = 2 * max(1, int(status.get("workers") or 1))  # o que fica no estudio de cada vez
    pending = [clip for clip in clips]
    active, made, broken, started, misses, shown = {}, [], [], time.time(), 0, {}
    known = held_jobs(session)
    for clip in list(pending):  # o que uma rodada anterior deixou em andamento
        job_id = book.get(clip.name)
        if job_id in known:
            active[job_id] = clip
            pending.remove(clip)
    total = len(pending) + len(active)
    while pending or active:
        while pending and len(active) < window:
            clip = pending.pop(0)
            try:
                job = submit(session, person, clip, chosen, workdir)
            except ValueError as error:
                failed(clip, str(error))
                broken.append(clip)
                continue
            active[job["id"]] = clip
            book[clip.name] = job["id"]
            remember(book)
            say(f"{stamp()} enviado: {clip.name} ({job.get('duration')} s, {job.get('chunks')} pedaço(s))")
        if not active:
            break
        time.sleep(POLL)
        try:
            jobs, misses = held_jobs(session), 0
        except (OSError, ValueError):
            misses += 1
            if misses * POLL > 600:
                raise Gone() from None
            continue
        for job_id, clip in list(active.items()):
            job = jobs.get(job_id)
            if job is None or job["status"] == "error":
                failed(clip, (job or {}).get("error") or "o vídeo sumiu do estúdio (o site aberto o levou para a galeria "
                             "dele, ou a sessão reiniciou); feche o site durante a produção")
                broken.append(clip)
            elif job["status"] == "done":
                try:
                    target = deliver(session, job, clip, chosen)
                except OSError as error:
                    say(f"{stamp()} não consegui baixar {clip.name} agora ({error}); tento de novo")
                    continue
                made.append(target)
                pace = (time.time() - started) / len(made) / 60
                left = len(pending) + len(active) - 1
                say(f"{stamp()} ✓ {target.name}  ({len(made)} de {total}"
                    + (f"; faltam {left}, uns {left * pace:.0f} min" if left else "") + ")")
            else:
                mark = (job["status"], job.get("stage") or "", int(job.get("progress", 0) * 10))
                if shown.get(job_id) != mark:
                    shown[job_id] = mark
                    stage = job.get("stage") or {"queued": "na fila", "preparing": "preparando"}.get(job["status"], "gerando")
                    say(f"{stamp()}   {clip.name}: {stage} {job.get('progress', 0):.0%}")
                continue
            del active[job_id]
            book.pop(clip.name, None)
            remember(book)
            if job is not None and job["status"] == "error":
                try:
                    call(session, f"/api/jobs/{job_id}", method="DELETE", timeout=30)
                except OSError:
                    pass
    shutil.rmtree(workdir, ignore_errors=True)
    LEDGER.unlink(missing_ok=True)
    return made, broken


def shut_down(session, auth, user):
    """Desliga a sessao se nada mais estiver rodando nela (o site pode estar em uso)."""
    try:
        status = studio_status(session)
        if status["idle"]["busy"]:
            return say("Há outro vídeo em andamento nesta GPU (pelo site?): não desliguei.")
        call(session, "/api/shutdown", data=b"{}", content_type="application/json", method="POST", timeout=30)
    except (OSError, ValueError, KeyError) as error:
        return say(f"Não consegui pedir o desligamento ({error}). Ela se desliga sozinha após 60 minutos sem uso.")
    say("Desligando a GPU…")
    if os.environ.get("WAN_SESSION"):
        return None
    deadline = time.time() + 180
    while time.time() < deadline:
        state, _ = ligar.patient_status(auth, user)
        if state not in (*ligar.ON, ligar.OFFLINE):
            return say("GPU desligada. " + (ligar.quota_line(auth) or ""))
        time.sleep(10)
    return say("O pedido foi aceito; confira depois com: python free\\ligar.py status")


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else "produzir"
    say("Wan Studio · produção em massa\n")
    prepare_folders()
    person, clips, people = inputs()
    chosen = options()
    fixed = bool(os.environ.get("WAN_SESSION"))
    if command == "desligar":
        auth, user = (None, None) if fixed else ligar.credentials(browser=False)
        session = find_session(auth, user)
        if not session[0]:
            return say("A GPU já está desligada.") or 0
        shut_down(session, auth, user)
        return 0
    if person is None or not clips:
        say(f"Falta {'a imagem da pessoa' if person is None else 'pelo menos um vídeo'}.\n")
        say(f"  1. Ponha a imagem da pessoa em:  {PERSON}")
        say(f"  2. Ponha os vídeos em:           {VIDEOS}")
        say("  3. Dê dois cliques em \"Produzir em massa\" de novo.\n")
        say(f"As opções (qualidade, duração, alta definição) estão em {OPTIONS.name}, na mesma pasta.")
        return 1
    say(f"Pessoa: {person.name}" + (f"  (há {people} imagens na pasta; uso a primeira)" if people > 1 else ""))
    say(f"Vídeos: {len(clips)}")
    say(f"Opções: qualidade {chosen['quality']}, {chosen['seconds']:g} s de cada vídeo"
        + (f" a partir de {chosen['start']:g} s" if chosen["start"] else "")
        + (", alta definição" if chosen["hd"] else "") + (", movimento fluido" if chosen["fluid"] else "")
        + (", formato do Instagram" if chosen["instagram"] else "") + "\n")
    if chosen["instagram"] and not (tool("ffmpeg") and tool("ffprobe")):
        say("(sem o ffmpeg neste computador: os vídeos saem como o estúdio os entrega, sem a conversão)\n")

    auth, user = (None, None) if fixed else ligar.credentials(browser=False)
    if not fixed:
        say(ligar.quota_line(auth) or "")
    try:
        session, status = power_on(auth, user, chosen["hd"])
        swap = status["models"]["animate"]
        if chosen["hd"] and swap.get("hd") != "seedvr2":
            say("A alta definição não está instalada nesta sessão: sigo sem ela.")
            chosen["hd"] = False
        if chosen["fluid"] and not swap.get("rife"):
            chosen["fluid"] = False
        made, broken = produce(session, status, person, clips, chosen)
    except Gone:
        say("\nA sessão parou de responder. Os vídeos que faltam continuam na pasta; rode de novo para retomar.")
        return 1
    except KeyboardInterrupt:
        say("\nParei de acompanhar. A GPU continua trabalhando: rode de novo para buscar os prontos,")
        say("ou desligue com: python free\\producao.py desligar")
        return 1
    say(f"\nFim: {len(made)} vídeo(s) em {READY}" + (f"; {len(broken)} com erro (veja {ERRORS.name})" if broken else "") + ".")
    if chosen["shutdown"]:
        shut_down(session, auth, user)
    return 1 if broken and not made else 0


if __name__ == "__main__":
    sys.exit(main())
