#!/usr/bin/env bash
# Start the whole Chatbot DEV stack — fully containerized (docker-compose.yml).
#
#   ./start-all.sh              # build (if needed) + run; Ctrl+C stops everything
#   WORKERS=3 ./start-all.sh    # scale ingestion workers
#
# URLs once up:
#   ingestion UI  http://localhost:5174      agent UI   http://localhost:5173
#   ingestion API http://localhost:8000      agent API  http://localhost:8001
#   sync API      http://localhost:8003      rabbitmq   http://localhost:15672 (app/app)
#
# Logs: all containers stream here; or `docker compose logs -f <service>`.
# The TEST stack (./start-test-stack.sh) is separate and unaffected.

set -euo pipefail
cd "$(dirname "$0")"

docker info >/dev/null 2>&1 || {
  echo "FATAL: Docker daemon not running — start Docker Desktop first." >&2
  exit 1
}

exec docker compose up --build
