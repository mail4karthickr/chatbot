#!/usr/bin/env bash
# Start the ISOLATED TEST STACK for the ingestion-to-retrieval eval suite.
# Design doc: plan/ingestion-eval-design.md (§5). Sibling of start-all.sh —
# same services, same code, different endpoints, different data stores.
#
#   infra (docker):   minio :9000, postgres-test :5434, rabbitmq-test :5673,
#                     qdrant-eval :6333        (docker-compose.test.yml, pinned)
#   host processes:   s3-sync-service :8013, ingestion API :8010, 1 worker
#
# Runs DETACHED: service PIDs land in logs/test-stack.pids; stop with
# ./stop-test-stack.sh. Logs stream to logs/test-<name>.log.
#
# ⚠️ Never reuses start-dev-server.sh — those scripts hardcode the dev ports
# AND kill whatever holds them (they would take down the dev stack).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
APPS="$ROOT/apps"
LOGS="$ROOT/logs"
PIDFILE="$LOGS/test-stack.pids"
mkdir -p "$LOGS"

log() { printf '\033[1;35m[test-stack]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[test-stack] FATAL:\033[0m %s\n' "$*" >&2; exit 1; }

# --- sanitized service environment (design §5.2) -----------------------------
# EVERY backend endpoint + credential is overridden; only non-backend config
# (OPENAI_API_KEY, model names) falls through to each service's .env.
TEST_ENV=(
  # +psycopg: the sync venv ships psycopg3; bare postgresql:// selects psycopg2
  # (matches the dev .env scheme — checked, not assumed)
  "DATABASE_URL=postgresql+psycopg://sync:sync@localhost:5434/sync"
  "QDRANT_URL=http://localhost:6333"
  "QDRANT_API_KEY="
  "S3_ENDPOINT=http://localhost:9000"
  "S3_ACCESS_KEY=minioadmin"
  "S3_SECRET_KEY=minioadmin"
  "S3_BUCKET=docs-eval"
  "S3_REGION=us-east-1"
  "RABBITMQ_URL=amqp://app:app@localhost:5673/"
  "SYNC_URL=http://localhost:8013"
  "EVAL_MODE=true"
)

# --- preflight: infra up + ports free (design §5.3) --------------------------
if [ -f "$PIDFILE" ]; then
  die "$PIDFILE exists — test stack already running? Run ./stop-test-stack.sh first."
fi

log "bringing up test infra (pinned images)"
docker compose -f "$ROOT/docker-compose.test.yml" up -d

for c in minio-test postgres-test rabbitmq-test qdrant-eval; do
  log "waiting for $c to become healthy..."
  for _ in $(seq 1 60); do
    [ "$(docker inspect --format '{{.State.Health.Status}}' "$c" 2>/dev/null)" = "healthy" ] && break
    sleep 2
  done
  [ "$(docker inspect --format '{{.State.Health.Status}}' "$c" 2>/dev/null)" = "healthy" ] \
    || die "$c did not become healthy"
done
log "all four containers healthy"

for port in 8010 8013; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN -t >/dev/null 2>&1; then
    die "port $port already in use — is a previous test stack still running?"
  fi
done

# --- preflight: EFFECTIVE settings validation (design §5.3, review #3) -------
# Run inside each service's own venv with the sanitized env, asserting what the
# service's real Settings class RESOLVES (not what we exported above).
VALIDATOR="$APPS/ingestion-service/evals/validate_effective_settings.py"

log "validating effective settings (ingestion-service)"
env "${TEST_ENV[@]}" "$APPS/ingestion-service/.venv/bin/python" "$VALIDATOR" --service ingestion \
  || die "ingestion-service resolved non-test settings — refusing to start"

log "validating effective settings (s3-sync-service)"
env "${TEST_ENV[@]}" "$APPS/s3-sync-service/.venv/bin/python" "$VALIDATOR" --service sync \
  || die "s3-sync-service resolved non-test settings — refusing to start"

# --- start host processes (direct uvicorn — see header warning) --------------
PIDS=()

start_bg() {
  # start_bg <name> <working-dir> <cmd...>
  local name="$1"; shift
  local dir="$1"; shift
  log "starting $name"
  ( cd "$dir" && env "${TEST_ENV[@]}" "$@" >>"$LOGS/test-$name.log" 2>&1 ) &
  local pid=$!
  PIDS+=("$pid")
  printf '%s %s\n' "$pid" "$name" >>"$PIDFILE"
  printf '  pid=%s  log=logs/test-%s.log\n' "$pid" "$name"
}

wait_http() { # wait_http <name> <url> <tries>
  local name="$1" url="$2" tries="$3"
  log "waiting for $name at $url ..."
  for _ in $(seq 1 "$tries"); do
    if curl -sf -o /dev/null "$url"; then log "$name is up"; return 0; fi
    sleep 2
  done
  die "$name never became ready — see logs/test-*.log"
}

# Startup is STAGGERED on purpose: ensure_bucket() (storage.py:44) is
# check-then-create, so starting the API and worker simultaneously against an
# empty MinIO races both into create_bucket and one dies on
# BucketAlreadyOwnedByYou. The API goes first and must be fully ready (bucket
# created) before the worker starts.
start_bg sync-service "$APPS/s3-sync-service" \
  ./.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8013
wait_http "s3-sync-service" "http://127.0.0.1:8013/docs" 30

start_bg ingestion-api "$APPS/ingestion-service" \
  ./.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8010
# The API imports docling at startup — first boot can take a while.
wait_http "ingestion API" "http://127.0.0.1:8010/docs" 90

start_bg ingestion-worker "$APPS/ingestion-service" \
  ./.venv/bin/python worker.py

log "test stack is UP:  API :8010 · sync :8013 · worker running"
log "next:  cd apps/ingestion-service/evals && .venv/bin/python seed.py"
log "stop:  ./stop-test-stack.sh"
