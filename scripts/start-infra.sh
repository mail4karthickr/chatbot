#!/usr/bin/env bash
# Bring up rabbitmq + postgres via docker compose and wait for health.
# Exits 0 once both are healthy so VSCode's "Start All" task can proceed.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APPS="$ROOT/apps"

log() { printf '\033[1;36m[infra]\033[0m %s\n' "$*"; }

log "bringing up rabbitmq"
( cd "$APPS/rabbitmq" && docker compose up -d )

log "bringing up postgres"
( cd "$APPS/s3-sync-service" && docker compose up -d postgres )

log "waiting for rabbitmq..."
until [ "$(docker inspect --format '{{.State.Health.Status}}' rabbitmq 2>/dev/null)" = "healthy" ]; do
  sleep 1
done
log "rabbitmq is up on localhost:5672 (mgmt UI: http://localhost:15672)"

log "waiting for postgres..."
until [ "$(cd "$APPS/s3-sync-service" && docker compose ps postgres --format '{{.Health}}')" = "healthy" ]; do
  sleep 1
done
log "postgres is up on localhost:5433"

log "infra ready"
