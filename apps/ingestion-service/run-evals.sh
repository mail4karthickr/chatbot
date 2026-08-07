#!/usr/bin/env bash
# One-command eval run (design doc plan/ingestion-eval-design.md, §9):
#
#   1. start the test env (skipped if already running) and wait until it's up
#   2. ask whether to run ingestion (erases + re-seeds the test corpus)
#   3. run the evaluation suite (pytest)
#
#   ./run-evals.sh                  # eval tiers T0-T2 (free — no LLM judge)
#   ./run-evals.sh -m smoke         # any extra args are passed straight to pytest
#   ./run-evals.sh -m ""            # everything incl. judged T3 (OpenAI cost)
#
# The stack is left running afterwards for fast re-runs: ./stop-test-stack.sh
# when you're done for the day.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
EVALS="$ROOT/apps/ingestion-service/evals"
API="http://127.0.0.1:8010"

log() { printf '\033[1;36m[run-evals]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[run-evals] FATAL:\033[0m %s\n' "$*" >&2; exit 1; }

[ -x "$EVALS/.venv/bin/python" ] || die \
  "eval venv missing — run: cd apps/ingestion-service/evals && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"

# --- 1. test env up ----------------------------------------------------------
if curl -sf -o /dev/null "$API/documents"; then
  log "test stack already running (API answering on :8010) — not restarting"
elif [ -f "$ROOT/logs/test-stack.pids" ]; then
  die "logs/test-stack.pids exists but the API is not answering — stale stack; run ./stop-test-stack.sh, then re-run"
else
  "$ROOT/start-test-stack.sh"   # blocks until containers healthy + services answering
fi

# --- 2. ingestion, on request ------------------------------------------------
# seed.py --yes: it would ask the same question again; this prompt replaces it.
printf '\nRun ingestion now? This ERASES the test corpus (index, ledger, eval bucket)\nand re-ingests all fixtures: ~3-5 min + OpenAI cost (cents). Needed after any\nchange to service code, dependencies, or fixture PDFs.\n'
read -r -p "Ingest? [y/N] " answer
case "$answer" in
  [yY]|[yY][eE][sS])
    log "seeding the corpus (full rebuild)"
    (cd "$EVALS" && ./.venv/bin/python seed.py --yes)
    ;;
  *)
    log "skipping ingestion — evaluating the existing corpus"
    ;;
esac

# --- 3. evaluation -----------------------------------------------------------
if [ $# -gt 0 ]; then
  log "running: pytest $*"
  (cd "$EVALS" && ./.venv/bin/python -m pytest "$@")
else
  log 'running: pytest -m "not judged"   (T0-T2; pass args to override, see header)'
  (cd "$EVALS" && ./.venv/bin/python -m pytest -m "not judged")
fi
