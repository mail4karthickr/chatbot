"""Seed the eval corpus into the TEST stack — always a full, atomic rebuild.

Design doc: plan/ingestion-eval-design.md §6.2 (the authoritative protocol,
amended 2026-08-05: fingerprint comparison removed — see the amendment note).

    .venv/bin/python seed.py            # confirm, then erase + re-ingest everything
    .venv/bin/python seed.py --yes      # skip the confirmation (scripted use)

EVERY run erases the test index, ledger, and eval bucket prefix, then re-ingests
all fixtures from scratch (~3-5 min + OpenAI captioning/embedding cost). The
indexed corpus therefore always reflects the current pipeline implementation
right after a seed — there is no staleness detection between seeds, so re-run
seed.py after any change to service code, dependencies, or fixtures.

Black-box: everything goes through the running test stack's HTTP APIs, plus the
RabbitMQ management API (quiesce) and a read-only Qdrant client (verification).
"""

import argparse
import fcntl
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn

import httpx

EVALS_DIR = Path(__file__).resolve().parent
SERVICE_DIR = EVALS_DIR.parent                  # apps/ingestion-service
ROOT = SERVICE_DIR.parent.parent                # repo root
FIXTURES_DIR = EVALS_DIR / "fixtures"
MANIFEST = EVALS_DIR / ".manifest.json"
LOCKFILE = EVALS_DIR / ".seed.lock"
WORKER_LOG = ROOT / "logs" / "test-ingestion-worker.log"

API = "http://127.0.0.1:8010"                   # test ingestion API
SYNC = "http://127.0.0.1:8013"                  # test s3-sync-service
MGMT = "http://127.0.0.1:15673"                 # test RabbitMQ management API
MGMT_AUTH = ("app", "app")
VHOST = "%2F"                                   # default vhost, URL-encoded

S3_PREFIX = "docs/eval-corpus"
QUIESCE_TIMEOUT_S = 600                         # an in-flight Docling job can be slow
INGEST_TIMEOUT_S = 900

SERVICE_PY = SERVICE_DIR / ".venv" / "bin" / "python"


def log(msg: str) -> None:
    print(f"[seed] {msg}", flush=True)


def fail(msg: str, code: int = 1) -> NoReturn:
    print(f"[seed] FAILED: {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


# --------------------------------------------------------------------------- #
# Service probe (queue/collection/settings resolved in the SERVICE venv)
# --------------------------------------------------------------------------- #

def _service_probe() -> dict:
    """One subprocess in the SERVICE venv: resolved model settings, runtime,
    queue name and collection name — derived from the service's own code/config,
    never copied into this script (fourth review #3)."""
    code = (
        "import json, platform, sys\n"
        "import broker, vectordb\n"
        "from config import get_settings\n"
        "s = get_settings()\n"
        "print(json.dumps({\n"
        "  'settings': {'embed_model': s.embed_model, 'caption_model': s.caption_model,\n"
        "               'generate_model': s.generate_model},\n"
        "  'runtime': {'python': sys.version.split()[0], 'system': platform.system(),\n"
        "              'machine': platform.machine()},\n"
        "  'queue': broker.QUEUE,\n"
        "  'collection': vectordb.COLLECTION,\n"
        "}))\n"
    )
    r = subprocess.run([str(SERVICE_PY), "-c", code], cwd=SERVICE_DIR,
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        fail(f"service probe failed:\n{r.stderr[-2000:]}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def fixture_pdfs() -> list[tuple[Path, str]]:
    """(path, s3_key) for every committed fixture. doc_id == s3_key."""
    pdfs = [(p, f"{S3_PREFIX}/{p.parent.name}/{p.name}")
            for p in sorted(FIXTURES_DIR.glob("*/*.pdf"))]
    if not pdfs:
        fail(f"no fixture PDFs under {FIXTURES_DIR}")
    return pdfs


# --------------------------------------------------------------------------- #
# Broker quiesce + assertions (§6.2 steps 1, pre-6)
# --------------------------------------------------------------------------- #

def _queue_state(client: httpx.Client, queue: str) -> dict | None:
    r = client.get(f"{MGMT}/api/queues/{VHOST}/{queue}", auth=MGMT_AUTH)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    q = r.json()
    return {"ready": q.get("messages_ready", 0),
            "unacked": q.get("messages_unacknowledged", 0),
            "consumers": q.get("consumers", 0)}


def quiesce_broker(client: httpx.Client, queue: str) -> None:
    log(f"quiescing broker (queue={queue!r}, vhost=/)")
    r = client.delete(f"{MGMT}/api/queues/{VHOST}/{queue}/contents", auth=MGMT_AUTH)
    if r.status_code not in (200, 204, 404):    # 404: queue not declared yet — nothing to purge
        r.raise_for_status()
    deadline = time.monotonic() + QUIESCE_TIMEOUT_S
    while time.monotonic() < deadline:
        state = _queue_state(client, queue)
        if state is None or (state["ready"] == 0 and state["unacked"] == 0):
            log("broker quiesced (ready=0, unacked=0)")
            return
        time.sleep(3)
    state = _queue_state(client, queue) or {}
    _dump_worker_log()
    fail("quiesce timed out after "
         f"{QUIESCE_TIMEOUT_S}s — queue={queue!r} vhost=/ state={state}")


def assert_queue_ready_for_ingest(client: httpx.Client, queue: str) -> None:
    """Pre-ingestion assertion (fourth review #3): right queue, one test worker,
    nothing pending — catches 'purged the wrong queue' and 'worker not running'."""
    state = _queue_state(client, queue)
    if state is None:
        fail(f"queue {queue!r} does not exist on the test broker — is the worker running?")
    if state["consumers"] != 1:
        fail(f"expected exactly 1 consumer on {queue!r} (the test worker), found "
             f"{state['consumers']}")
    if state["ready"] or state["unacked"]:
        fail(f"queue {queue!r} not empty before ingest: {state}")
    log(f"queue check OK: {queue!r} consumers=1 ready=0 unacked=0")


def _dump_worker_log(lines: int = 40) -> None:
    if WORKER_LOG.exists():
        tail = WORKER_LOG.read_text().splitlines()[-lines:]
        print(f"[seed] ---- tail of {WORKER_LOG} ----", file=sys.stderr)
        print("\n".join(tail), file=sys.stderr)


# --------------------------------------------------------------------------- #
# Rebuild protocol (§6.2, authoritative 9 steps)
# --------------------------------------------------------------------------- #

def eval_keys_in_bucket(client: httpx.Client) -> set[str]:
    r = client.get(f"{API}/s3/files")
    r.raise_for_status()
    return {f["key"] for f in r.json()["files"] if f["key"].startswith(f"{S3_PREFIX}/")}


def rebuild(client: httpx.Client, probe: dict) -> None:
    queue = probe["queue"]
    expected_keys = {key for _, key in fixture_pdfs()}

    # 1. QUIESCE
    quiesce_broker(client, queue)

    # 2. RESET (existing endpoint — Qdrant + ledger + _artifacts/ + parse cache)
    log("POST /reset (test index + ledger)")
    client.post(f"{API}/reset", timeout=120).raise_for_status()

    # 3. CLEAR SOURCE PREFIX
    log(f"clearing s3 prefix {S3_PREFIX}/")
    r = client.delete(f"{API}/s3/folder", params={"path": S3_PREFIX})
    if r.status_code not in (200, 404):
        r.raise_for_status()
    leftover = eval_keys_in_bucket(client)
    if leftover:
        fail(f"prefix not empty after clear: {sorted(leftover)}")

    # 4. UPLOAD
    for path, key in fixture_pdfs():
        target = key.rsplit("/", 1)[0]          # docs/eval-corpus/<type>
        log(f"uploading {key}")
        r = client.post(f"{API}/s3/upload",
                        files=[("files", (path.name, path.read_bytes(), "application/pdf"))],
                        data={"target": target}, timeout=120)
        r.raise_for_status()
        if r.json().get("failed"):
            fail(f"upload failed: {r.json()['failed']}")

    # 5. VERIFY UPLOAD — exactly the expected keys, no more, no fewer
    present = eval_keys_in_bucket(client)
    if present != expected_keys:
        fail(f"bucket mismatch — extra={sorted(present - expected_keys)} "
             f"missing={sorted(expected_keys - present)}")
    log(f"bucket verified: {len(present)} fixture objects")

    # 6. INGEST (after asserting queue identity/consumer)
    assert_queue_ready_for_ingest(client, queue)
    log("POST /ingest")
    r = client.post(f"{API}/ingest", timeout=120)
    r.raise_for_status()
    enqueued = r.json().get("enqueued", 0)
    if enqueued != len(expected_keys):
        fail(f"enqueued {enqueued} jobs, expected {len(expected_keys)}: {r.json()}")

    # 7. POLL — doc_summary written LAST by ingest_document = completion marker;
    #    sound because the index started empty (steps 1-2).
    log(f"waiting for {len(expected_keys)} documents to finish ingesting "
        f"(first run: ~1-2 min/doc for parse + captions)...")
    deadline = time.monotonic() + INGEST_TIMEOUT_S
    done: set[str] = set()
    while time.monotonic() < deadline:
        r = client.get(f"{API}/documents")
        r.raise_for_status()
        done = {d["doc_id"] for d in r.json()["documents"]} & expected_keys
        if done == expected_keys:
            break
        time.sleep(10)
    if done != expected_keys:
        _dump_worker_log()
        quiesce_broker(client, queue)           # leave no half-finished work behind
        fail(f"ingest incomplete after {INGEST_TIMEOUT_S}s — done={sorted(done)} "
             f"missing={sorted(expected_keys - done)}")
    log("all documents ingested")

    # 8. VERIFY — index artifacts + ledger
    verify_corpus(client, probe, expected_keys)

    # 9. MANIFEST
    MANIFEST.write_text(json.dumps({
        "fixtures": {key: hashlib.sha256(path.read_bytes()).hexdigest()
                     for path, key in fixture_pdfs()},
        "queue": queue,
        "collection": probe["collection"],
        "seeded_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2))
    log(f"manifest written: {MANIFEST}")


def verify_corpus(client: httpx.Client, probe: dict, expected_keys: set[str]) -> None:
    """Post-ingest artifact checks (§6.2 step 8): per doc ≥1 text chunk and
    exactly one doc_summary (read-only Qdrant), plus ledger row via /diff."""
    from qdrant_client import QdrantClient, models

    qc = QdrantClient(url="http://localhost:6333")
    collection = probe["collection"]
    for key in sorted(expected_keys):
        def count(kind: str) -> int:
            return qc.count(collection, count_filter=models.Filter(must=[
                models.FieldCondition(key="doc_id", match=models.MatchValue(value=key)),
                models.FieldCondition(key="kind", match=models.MatchValue(value=kind)),
            ]), exact=True).count
        n_text, n_summary = count("text"), count("doc_summary")
        if n_text < 1:
            fail(f"{key}: no text chunks in index")
        if n_summary != 1:
            fail(f"{key}: expected exactly 1 doc_summary, found {n_summary}")
        log(f"index OK: {key} text_chunks={n_text}")

    # Ledger check, black-box: /diff must classify every fixture as unchanged.
    r = client.get(f"{API}/s3/files")
    r.raise_for_status()
    files = [{"s3_key": f["key"], "etag": f["etag"], "size": f["size"],
              "last_modified": f["last_modified"]}
             for f in r.json()["files"] if f["key"] in expected_keys]
    r = httpx.post(f"{SYNC}/diff", json={"files": files, "prefix": "docs/"}, timeout=30)
    r.raise_for_status()
    unchanged = set(r.json().get("unchanged", []))
    if not expected_keys <= unchanged:
        fail(f"ledger incomplete — not marked ingested: {sorted(expected_keys - unchanged)}")
    log("ledger OK: all fixtures marked ingested")


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def confirm_or_exit(n_fixtures: int) -> None:
    """Every seed is destructive — say exactly what happens, then ask."""
    print(
        "\nThis will ERASE the test environment's current corpus and re-ingest "
        "from scratch:\n"
        "  - test Qdrant collection (all chunks, captions, summaries)\n"
        "  - test ledger (Postgres :5434, via /reset)\n"
        f"  - eval bucket prefix {S3_PREFIX}/ in MinIO\n"
        f"then re-ingest {n_fixtures} fixture PDFs "
        "(~3-5 min; OpenAI captioning/embedding cost, cents).\n"
        "Dev/production data is NOT touched — this is the isolated test stack only.\n",
        flush=True,
    )
    if not sys.stdin.isatty():
        fail("stdin is not a terminal — pass --yes to seed non-interactively")
    if input("Continue? [y/N] ").strip().lower() not in ("y", "yes"):
        print("[seed] aborted — nothing was changed", flush=True)
        sys.exit(0)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed the eval corpus into the test stack (always a full rebuild).")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="skip the confirmation prompt (scripted use)")
    args = parser.parse_args()

    if not args.yes:
        confirm_or_exit(len(fixture_pdfs()))

    # Exclusive lock for the WHOLE sequence — two concurrent seeds would
    # interleave quiesce/reset/upload (third review).
    lock_fh = open(LOCKFILE, "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fail("another seed.py is already running (evals/.seed.lock held)")

    with httpx.Client(timeout=30) as client:
        try:
            client.get(f"{API}/documents").raise_for_status()
        except Exception as e:
            fail(f"test API not reachable at {API} — run ./start-test-stack.sh first ({e})")

        rebuild(client, _service_probe())

    log("done")


if __name__ == "__main__":
    main()
