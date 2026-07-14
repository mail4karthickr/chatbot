#!/usr/bin/env bash
# Start every service in the Chatbot stack for local dev.
#
# Infra (docker):   rabbitmq (apps/rabbitmq), postgres (apps/s3-sync-service)
# Python services:  s3-sync-service :8003, ingestion-service :8000,
#                   ingestion-worker (no port), agent-service :8001
# UIs (vite):       ingestion-ui, agent-ui
#
# Logs stream to ./logs/<service>.log. Ctrl+C stops everything.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
APPS="$ROOT/apps"
LOGS="$ROOT/logs"
mkdir -p "$LOGS"

PIDS=()

log() { printf '\033[1;36m[start-all]\033[0m %s\n' "$*"; }

start_bg() {
  # start_bg <name> <working-dir> <cmd...>
  local name="$1"; shift
  local dir="$1"; shift
  log "starting $name"
  ( cd "$dir" && "$@" >>"$LOGS/$name.log" 2>&1 ) &
  local pid=$!
  PIDS+=("$pid")
  printf '  pid=%s  log=logs/%s.log\n' "$pid" "$name"
}

cleanup() {
  echo
  log "shutting down..."
  for pid in "${PIDS[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  # Give children a moment, then force-kill leftovers.
  sleep 2
  for pid in "${PIDS[@]}"; do
    kill -9 "$pid" 2>/dev/null || true
  done

  log "stopping docker infra"
  ( cd "$APPS/rabbitmq" && docker compose down ) || true
  ( cd "$APPS/s3-sync-service" && docker compose down ) || true

  log "done"
}
trap cleanup INT TERM

# --- infra ------------------------------------------------------------------

log "bringing up rabbitmq"
( cd "$APPS/rabbitmq" && docker compose up -d )

log "bringing up postgres"
( cd "$APPS/s3-sync-service" && docker compose up -d postgres )

log "waiting for rabbitmq to become healthy..."
until [ "$(docker inspect --format '{{.State.Health.Status}}' rabbitmq 2>/dev/null)" = "healthy" ]; do
  sleep 1
done
log "rabbitmq is up on localhost:5672 (mgmt UI: http://localhost:15672)"

log "waiting for postgres to become healthy..."
until [ "$(cd "$APPS/s3-sync-service" && docker compose ps postgres --format '{{.Health}}')" = "healthy" ]; do
  sleep 1
done
log "postgres is up on localhost:5433"

# --- python services --------------------------------------------------------

start_bg s3-sync-service    "$APPS/s3-sync-service"    ./start-dev-server.sh
start_bg ingestion-service  "$APPS/ingestion-service"  ./start-dev-server.sh
start_bg ingestion-worker   "$APPS/ingestion-service"  ./start-worker.sh
start_bg agent-service      "$APPS/agent-service"      ./start-dev-server.sh

# --- UIs --------------------------------------------------------------------

start_bg ingestion-ui       "$APPS/ingestion-ui"       npm run dev
start_bg agent-ui           "$APPS/agent-ui"           npm run dev

echo
log "all services started. tail a log with: tail -f logs/<name>.log"
log "press Ctrl+C to stop everything."

# Wait on any child; if one dies, keep the script alive so cleanup runs on Ctrl+C.
wait
