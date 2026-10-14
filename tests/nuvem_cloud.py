"""Faz o papel do Modal nos testes de nuvem/ (tests/nuvem_test.py): executa as chamadas que o
atalho e o `acordar` "pedem" ao modal de mentira (tests/fake_modal).

  python tests/nuvem_cloud.py               atende as chamadas ate aparecer o arquivo "parar"
  python tests/nuvem_cloud.py --sessao ID   roda uma sessao (e como o laco acima a executa)

  baixar   grava no volume de mentira os arquivos que a troca de pessoa exige
  sessao   roda nuvem/sessao.py de verdade, com o launcher e o servidor de verdade, numa copia
           do projeto em que o ComfyUI e o simulador (tests/fake_comfy.py). So o tunel (o
           endereco local) e a pasta de modelos sao de mentira.

Arquivos na pasta FAKE_MODAL_HOME que mudam o comportamento, para os cenarios de falha:
"falha_download" (o download termina com erro) e "falha_sessao" (a sessao nao acha os modelos).
"""
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "fake_modal"))
import modal  # noqa: E402

HOME = modal.HOME
ROOT = Path(os.environ.get("NUVEM_ROOT", HERE / ".work" / "nuvem" / "root"))
MODELS = ("text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors", "vae/wan_2.1_vae.safetensors",
          "clip_vision/clip_vision_h.safetensors",
          "loras/lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors",
          "diffusion_models/Wan2_2-Animate-14B_fp8_e4m3fn_scaled_KJ.safetensors")


def log(name, text):
    path = HOME / "logs" / f"{name}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text + "\n")


def download(call):
    """O que `baixar` deixaria no volume, com as linhas que o instalador escreve."""
    modal.update_call(call["id"], status="running")
    volume = HOME / "volumes" / f"{call['app']}-modelos" / "models"
    for name in MODELS:
        log(call["id"], f"baixando {name}")
        log(call["id"], "[#a1b2c3 1.2GiB/6.3GiB(19%) CN:16 DL:95MiB ETA:53s]")
        log(call["id"], "uma linha qualquer do aria2 que nao interessa")
        time.sleep(0.05)
        if (HOME / "falha_download").exists():
            return modal.update_call(call["id"], status="failed", error="o download dos modelos falhou")
        (volume / name).parent.mkdir(parents=True, exist_ok=True)
        (volume / name).write_bytes(b"modelo de teste")
    log(call["id"], "modelos da troca de pessoa prontos")
    modal.update_call(call["id"], status="done", result=[f"models/{name}" for name in MODELS])


def session(call_id):
    """Uma sessao de verdade, com o ComfyUI simulado. A saida deste processo e o "registro"."""
    call = modal.find_call(call_id)
    os.environ.update(
        FAKE_ASSETS=str(HERE / ".work"), FAKE_SETS="animate,animate_fp8", FAKE_STEP="0.05",
        FAKE_GPU="NVIDIA H100 80GB HBM3", FAKE_VRAM_GB="80",
        WAN_WORKERS="1", WARM_UP="0", WAN_KEEPALIVE_STEP="1",
        WAN_NTFY_URL="http://127.0.0.1:9",  # nenhum anuncio sai daqui para a internet
        PYTHONDONTWRITEBYTECODE="1")
    sys.path.insert(0, str(ROOT / "nuvem"))
    import sessao

    def prepare(_models_dir):
        if (HOME / "falha_sessao").exists():
            raise SystemExit("os modelos da troca de pessoa não estão no volume (teste)")
        sessao.install_nodes()

    state = modal.Dict.from_name(f"{call['app']}-estado")
    summary = sessao.run(dict(call["args"][0]), state, modal.forward, prepare=prepare)
    modal.update_call(call_id, status="done", result=summary)
    return 0


def kill_tree(proc):
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.terminate()


def serve():
    running = {}
    while not (HOME / "parar").exists():
        for call in modal.calls():
            if call["status"] == "pending" and call["function"] == "baixar":
                download(call)
            elif call["status"] == "pending" and call["function"] == "sessao":
                modal.update_call(call["id"], status="running")
                output = (HOME / "logs" / "sessao.txt")
                output.parent.mkdir(parents=True, exist_ok=True)
                handle = output.open("a", encoding="utf-8")
                running[call["id"]] = subprocess.Popen(
                    [sys.executable, str(Path(__file__).resolve()), "--sessao", call["id"]],
                    stdout=handle, stderr=subprocess.STDOUT,
                    env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"})
            elif call.get("cancel") and call["status"] == "running" and call["id"] in running:
                kill_tree(running.pop(call["id"]))
                modal.update_call(call["id"], status="cancelled")
        for call_id, proc in list(running.items()):
            if proc.poll() is not None:
                del running[call_id]
                if (modal.find_call(call_id) or {}).get("status") == "running":  # morreu sem registrar
                    modal.update_call(call_id, status="failed", error=f"a sessão caiu (código {proc.returncode})")
        time.sleep(0.1)
    for proc in running.values():
        kill_tree(proc)
    return 0


if __name__ == "__main__":
    sys.exit(session(sys.argv[2]) if sys.argv[1:2] == ["--sessao"] else serve())
