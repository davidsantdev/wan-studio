"""Testa free/ligar.py contra uma API do Kaggle simulada (so biblioteca padrao).

  python tests/ligar_test.py

O simulador responde nas mesmas rotas e com os mesmos campos do cliente oficial
(kagglesdk 0.1.37): security.OAuthService/IntrospectToken e kernels.KernelsApiService/
{GetKernelSessionStatus, SaveKernel, GetAcceleratorQuotaStatistics}. Nada sai para a internet
e o navegador nao e aberto.
"""
import base64
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FREE = ROOT / "free"
TOKEN = "KGAT_token_de_teste"
failures = []

# Estado do Kaggle de mentira; cada cenario ajusta antes de rodar o script.
INSTALLING = ["Pasta de trabalho: /content/wan-studio (1069 GB livres)", "Tesla T4, 15360 MiB"]
NO_INTERNET = ["Pasta de trabalho: /content/wan-studio (1069 GB livres)",
               "SystemExit: Sem internet. No Kaggle: Session options > Internet > On (exige telefone verificado na conta)."]
kaggle = {"status": None, "requests": [], "push_error": "", "reject_machine": False, "log": INSTALLING,
          "outage_after_push": 0, "outage": 0}


class FakeKaggle(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        """Registro ao vivo, no formato do Kaggle: eventos SSE e um marcador de fim."""
        if "/kernels/logs/stream/maria/wan-studio" not in self.path or not self.headers.get("Authorization"):
            return self.reply(404, {"error": {"code": 404, "message": "Not found"}})
        lines = [f"data: {json.dumps({'stream_name': 'stdout', 'data': text + chr(10)})}" for text in kaggle["log"]]
        data = ("\n\n".join(lines + ["data: END_OF_LOG"]) + "\n\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        method = self.path.rsplit("/", 1)[-1]
        auth = self.headers.get("Authorization", "")
        kaggle["requests"].append((self.path, auth, body))
        legacy = "Basic " + base64.b64encode(b"maria:chave-antiga").decode()
        if auth not in (f"Bearer {TOKEN}", legacy):
            return self.reply(401, {"error": {"code": 401, "message": "Unauthenticated", "status": "UNAUTHENTICATED"}})
        if method == "IntrospectToken":
            return self.reply(200, {"active": body.get("token") == TOKEN, "username": "maria"})
        if method == "GetAcceleratorQuotaStatistics":
            return self.reply(200, {"gpuQuota": {"timeUsed": "16200s", "totalTimeAllowed": "108000s"}})
        if method == "GetKernelSessionStatus":
            if kaggle["outage"] > 0:  # o Kaggle (ou a rede) falha por algumas consultas
                kaggle["outage"] -= 1
                return self.reply(503, {"error": {"code": 503, "message": "Service Unavailable"}})
            if kaggle["status"] is None:
                return self.reply(404, {"error": {"code": 404, "message": "Not found", "status": "NOT_FOUND"}})
            # Como a API real: o valor padrao do campo ("na fila") vem omitido.
            return self.reply(200, {} if kaggle["status"] == "QUEUED" else {"status": kaggle["status"]})
        if method == "SaveKernel":
            if kaggle["reject_machine"] and "machineShape" in body:
                return self.reply(400, {"error": {"code": 400, "message": "Invalid machine shape", "status": "INVALID_ARGUMENT"}})
            if kaggle["push_error"]:
                return self.reply(200, {"error": kaggle["push_error"]})
            kaggle["status"] = "RUNNING"
            kaggle["outage"] = kaggle["outage_after_push"]
            return self.reply(200, {"ref": "/maria/wan-studio", "url": "https://www.kaggle.com/code/maria/wan-studio", "versionNumber": 3})
        return self.reply(404, {"error": {"code": 404, "message": "Not found"}})


def check(label, condition, detail=""):
    print(("ok    " if condition else "FALHA ") + label + (f"  [{detail}]" if detail else ""))
    if not condition:
        failures.append(label)


def run(*args, token=TOKEN, home=None):
    """Roda o script com a API simulada. `home` isola as credenciais da maquina; o token e a
    pagina de abertura ficam numa pasta do teste, e os de verdade nem sao lidos."""
    env = {**os.environ, "KAGGLE_API_URL": f"http://127.0.0.1:{PORT}/v1", "WAN_POLL_SECONDS": "0.1",
           "PYTHONIOENCODING": "utf-8", "USERPROFILE": str(home or EMPTY_HOME), "HOME": str(home or EMPTY_HOME),
           "WAN_TOKEN_FILE": str(TOKEN_FILE), "WAN_OPENER_FILE": str(OPENER_FILE)}
    env.pop("KAGGLE_API_TOKEN", None)
    if token:
        env["KAGGLE_API_TOKEN"] = token
    result = subprocess.run([sys.executable, str(FREE / "ligar.py"), *args, "--sem-navegador"], env=env,
                            capture_output=True, text=True, encoding="utf-8", input="", timeout=120)
    return result.returncode, result.stdout + result.stderr


def pushes():
    return [body for path, _, body in kaggle["requests"] if path.endswith("/SaveKernel")]


def opener_data():
    """O que a pagina de abertura entrega ao site (o codigo depois de "#c=")."""
    opener = (OPENER_FILE).read_text(encoding="utf-8") if (OPENER_FILE).exists() else ""
    if "#c=" not in opener:
        return {}
    return json.loads(base64.urlsafe_b64decode(opener.split("#c=")[1].split('"')[0] + "=="))


def main():
    # A credencial e a pagina de abertura de verdade ficam intocadas: o script as procura na
    # pasta do teste (WAN_TOKEN_FILE e WAN_OPENER_FILE em `run`).
    OPENER_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.unlink(missing_ok=True)
    OPENER_FILE.unlink(missing_ok=True)
    scenarios()
    print(f"\n{len(failures)} falha(s)" + (": " + "; ".join(failures) if failures else ""))
    return 1 if failures else 0


def scenarios():
    # ---- 1. primeira vez: o notebook ainda nao existe na conta
    kaggle.update(status=None, requests=[], push_error="", reject_machine=False)
    code, out = run()
    sent = pushes()
    check("1a liga e termina bem", code == 0 and "Pronto." in out, out.strip().splitlines()[-3][:90] if out.strip() else "")
    check("1b mostra a conta e a cota", "Conta: maria" in out and "4.5 h usadas de 30 h" in out)
    check("1c um unico envio", len(sent) == 1)
    body = sent[0] if sent else {}
    check("1d pedido como o do cliente oficial", body.get("slug") == "maria/wan-studio" and body.get("newTitle") == "Wan Studio"
          and body.get("language") == "python" and body.get("kernelType") == "notebook" and body.get("isPrivate") is True
          and body.get("enableGpu") is True and body.get("enableInternet") is True and body.get("machineShape") == "NvidiaTeslaT4",
          json.dumps({k: v for k, v in body.items() if k != "text"}))
    notebook = json.loads(body.get("text", "{}"))
    cells = notebook.get("cells", [])
    check("1e notebook com celulas em texto unico e sem saidas", len(cells) == 4 and all(isinstance(c["source"], str) for c in cells)
          and all(c.get("outputs") == [] for c in cells if c["cell_type"] == "code") and "launcher.install()" in cells[1]["source"])
    opener = (OPENER_FILE).read_text(encoding="utf-8") if (OPENER_FILE).exists() else ""
    link = opener.split('location.replace("')[1].split('")')[0] if "location.replace" in opener else ""
    data = json.loads(base64.urlsafe_b64decode(link.split("#c=")[1] + "==")) if "#c=" in link else {}
    pairing = json.loads((FREE / "pairing.json").read_text(encoding="utf-8"))
    check("1f pagina de abertura leva ao site ja pareado", link.startswith("../site/index.html#c=") and data.get("t") == pairing["topic"]
          and data.get("k") == pairing["key"] and isinstance(data.get("s"), int) and "u" not in data, link[:40])
    check("1g toda chamada vai autenticada", all(auth == f"Bearer {TOKEN}" for _, auth, _ in kaggle["requests"]))

    # ---- 2. ja ligada: nao envia de novo (um segundo envio abriria outra sessao)
    (OPENER_FILE).unlink(missing_ok=True)
    kaggle.update(status="RUNNING", requests=[])
    code, out = run()
    check("2 ja ligada: so abre o site", code == 0 and "já está ligada" in out and not pushes())
    check("2b e o site abre mostrando que esta ligando, ate conectar", isinstance(opener_data().get("s"), int))

    # ---- 3. na fila (o Kaggle pode segurar a sessao por mais de meia hora)
    kaggle.update(status="QUEUED", requests=[])
    code, out = run()
    check("3 na fila: nao reenvia e diz que e so esperar", code == 0 and not pushes() and "na fila do Kaggle" in out
          and "já está ligada" not in out, out.strip().splitlines()[-1][:90])

    # ---- 4. desligada (execucao anterior terminou): liga de novo
    kaggle.update(status="COMPLETE", requests=[])
    code, out = run()
    check("4 sessao anterior encerrada: envia de novo", code == 0 and len(pushes()) == 1 and "Pronto." in out)

    # ---- 5. o Kaggle recusa (ex.: telefone nao verificado)
    kaggle.update(status="COMPLETE", requests=[], push_error="Phone verification is required to enable internet")
    code, out = run()
    check("5 recusa do Kaggle e explicada", code == 1 and "Phone verification" in out and "telefone" in out)

    # ---- 6. conta que nao aceita escolher a placa: tenta de novo com a GPU padrao
    kaggle.update(status="COMPLETE", requests=[], push_error="", reject_machine=True)
    code, out = run()
    sent = pushes()
    check("6 sem escolha de placa: reenvia com a GPU padrao", code == 0 and len(sent) == 2 and "machineShape" in sent[0]
          and "machineShape" not in sent[1] and sent[1].get("enableGpu") is True)

    # ---- 7. comando status
    kaggle.update(status="COMPLETE", requests=[], reject_machine=False)
    code, out = run("status")
    check("7 status nao liga nada", code == 0 and "GPU: desligada" in out and not pushes(), out.strip().splitlines()[-1])

    # ---- 7b. conta sem telefone verificado: o Kaggle aceita, mas a sessao sobe sem internet
    # e o notebook para em segundos (foi o que aconteceu na primeira execucao real)
    (OPENER_FILE).unlink(missing_ok=True)
    kaggle.update(status="COMPLETE", requests=[], log=NO_INTERNET)
    code, out = run()
    check("7b sem internet: avisa o motivo e como verificar o telefone", code == 1 and "parou logo no começo: Sem internet" in out
          and "Phone Verification" in out and "Pronto." not in out, out.strip().splitlines()[-1][:70])
    check("7c e nao abre o site dizendo que esta ligando", not (OPENER_FILE).exists())
    kaggle.update(log=INSTALLING)

    # ---- 7d. comando registro
    code, out = run("registro", "--segundos", "5")
    check("7d registro mostra o que a GPU esta fazendo", code == 0 and "Tesla T4, 15360 MiB" in out)

    # ---- 8. token invalido e nenhuma outra credencial: pede o token (sem terminal, encerra)
    kaggle.update(requests=[])
    code, out = run(token="KGAT_errado")
    check("8 token recusado: explica e pede outro", code != 0 and "não é mais aceito" in out and "Generate New Token" in out
          and not pushes(), out.strip().splitlines()[-1][:80])

    # ---- 9. kaggle.json antigo na pasta do usuario
    home = WORK / "home_legado"
    (home / ".kaggle").mkdir(parents=True, exist_ok=True)
    (home / ".kaggle" / "kaggle.json").write_text(json.dumps({"username": "maria", "key": "chave-antiga"}), encoding="utf-8")
    kaggle.update(status="COMPLETE", requests=[])
    code, out = run(token="", home=home)
    check("9 credencial antiga (kaggle.json) funciona", code == 0 and len(pushes()) == 1
          and all(auth.startswith("Basic ") for _, auth, _ in kaggle["requests"]), out.strip().splitlines()[1] if out.strip() else "")

    # ---- 10. a conexao cai logo depois do envio (aconteceu de verdade): a GPU liga do mesmo
    # jeito, entao o script continua tentando em vez de dizer que nao deu certo
    kaggle.update(status="COMPLETE", requests=[], outage_after_push=3)
    code, out = run()
    check("10 queda de conexao depois do envio: continua tentando e termina bem", code == 0 and len(pushes()) == 1
          and "continuo tentando" in out and "a máquina ligou" in out and "Pronto." in out and "Não deu certo" not in out,
          " | ".join(line.strip() for line in out.strip().splitlines()[5:9])[:150])
    kaggle.update(outage_after_push=0, outage=0)


if __name__ == "__main__":
    WORK = Path(__file__).resolve().parent / ".work"
    EMPTY_HOME = WORK / "home_vazio"
    EMPTY_HOME.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE = WORK / "credenciais" / "kaggle_token.txt"
    OPENER_FILE = WORK / "credenciais" / "abrir.html"
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeKaggle)
    PORT = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    sys.exit(main())
