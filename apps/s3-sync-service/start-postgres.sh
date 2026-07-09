#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

docker compose up -d postgres

echo "waiting for postgres to become healthy..."
until [ "$(docker compose ps postgres --format '{{.Health}}')" = "healthy" ]; do
  sleep 1
done

echo "postgres is up on localhost:5433"
