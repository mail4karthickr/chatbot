#!/usr/bin/env bash
# Stop the eval test stack started by ./start-test-stack.sh.
# Kills the host service processes and stops the infra containers.
# Data VOLUMES SURVIVE (fast next start); factory reset:
#   docker compose -f docker-compose.test.yml down -v

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PIDFILE="$ROOT/logs/test-stack.pids"

log() { printf '\033[1;35m[test-stack]\033[0m %s\n' "$*"; }

if [ -f "$PIDFILE" ]; then
  while read -r pid name; do
    if kill -0 "$pid" 2>/dev/null; then
      log "stopping $name (pid $pid)"
      kill "$pid" 2>/dev/null || true
    fi
  done <"$PIDFILE"
  sleep 2
  while read -r pid name; do
    kill -9 "$pid" 2>/dev/null || true
  done <"$PIDFILE"
  rm -f "$PIDFILE"
else
  log "no pidfile — no host services to stop"
fi

log "stopping test infra containers (volumes persist)"
docker compose -f "$ROOT/docker-compose.test.yml" down

log "done"
