#!/usr/bin/env bash
# Para o ComfyUI e o Wan Studio.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for name in comfyui studio; do
  pidfile="$ROOT/logs/$name.pid"
  if [ -f "$pidfile" ]; then
    kill "$(cat "$pidfile")" 2>/dev/null || true
    rm -f "$pidfile"
  fi
done
sleep 1
