#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

PORT=8003

# Free the port if a stale uvicorn (e.g. orphaned --reload worker) still holds it.
pids=$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null || true)
if [ -n "$pids" ]; then
  echo ">> port $PORT held by PID(s): $pids — killing"
  echo "$pids" | xargs kill 2>/dev/null || true
  sleep 1
  pids=$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null || true)
  [ -n "$pids" ] && echo "$pids" | xargs kill -9 2>/dev/null || true
fi

exec ./.venv/bin/python -m uvicorn app:app --reload --host 0.0.0.0 --port "$PORT"
