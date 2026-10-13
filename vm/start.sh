#!/usr/bin/env bash
# Sobe (ou reinicia) o ComfyUI e o Wan Studio em segundo plano.
# Os dois escutam so em 127.0.0.1; o acesso e feito por tunel SSH (vm/tunnel.ps1).
# Para expor direto na rede: HOST=0.0.0.0 APP_PASSWORD=suasenha bash vm/start.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/venv/bin/python"
[ -x "$PY" ] || PY="${PYTHON:-python3}"
LOGS="$ROOT/logs"
mkdir -p "$LOGS"

bash "$ROOT/vm/stop.sh"

cd "$ROOT/ComfyUI"
nohup "$PY" main.py --listen 127.0.0.1 --port 8188 >"$LOGS/comfyui.log" 2>&1 </dev/null &
echo $! >"$LOGS/comfyui.pid"

cd "$ROOT/app"
HOST="${HOST:-127.0.0.1}" PORT="${PORT:-8080}" APP_PASSWORD="${APP_PASSWORD:-}" COMFY_DIR="$ROOT/ComfyUI" \
  nohup "$PY" server.py >"$LOGS/studio.log" 2>&1 </dev/null &
echo $! >"$LOGS/studio.pid"

echo "Aguardando o ComfyUI..."
for _ in $(seq 1 90); do
  if curl -sf http://127.0.0.1:8188/system_stats >/dev/null 2>&1; then
    echo "Pronto. Wan Studio em http://127.0.0.1:${PORT:-8080} (logs em $LOGS)"
    exit 0
  fi
  sleep 2
done
echo "O ComfyUI nao respondeu em 3 minutos. Veja $LOGS/comfyui.log" >&2
tail -n 30 "$LOGS/comfyui.log" >&2
exit 1
