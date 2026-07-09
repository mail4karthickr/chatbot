"""RabbitMQ consumer for ingestion jobs.

Same codebase as ingestion-service (Option B): shares config, storage, vectordb,
ingest, sync_client. Runs as a separate process — start one per worker slot.

Concurrency model:
  * prefetch_count=1 — RabbitMQ hands the next message only after we ack.
  * Manual ack after the whole job succeeds so a crash mid-job redelivers.
  * Horizontal scale: launch more processes / bump the deployment replica count.

Failure handling (deliberately minimal for now):
  * Success -> mark_ingested/mark_deleted + ack.
  * Failure -> log + mark_failed (best effort) + ack.
    Ack-on-failure drops the message; a poisoned message won't loop.
    Next iteration will wire up a retry queue + DLX for backoff.
"""
import json
import logging
import time
from pathlib import Path

import pika

from broker import QUEUE, ensure_topology
from config import get_settings
from ingest import ingest_document
from logging_config import setup_logging
from storage import delete_prefix, download_file, ensure_bucket
from sync_client import mark_deleted, mark_failed, mark_ingested
from vectordb import create_collection, delete_by_doc_id

setup_logging()
log = logging.getLogger("worker")
# `user` logger carries human-friendly messages for the "Info" view in the UI.
# Non-technical readers should be able to follow along using only these lines.
user_log = logging.getLogger("user")

DOWNLOAD_DIR = Path(__file__).parent / "data" / "ingested"


def _handle_ingest(s3_key: str, job_id: str) -> None:
    dest = DOWNLOAD_DIR / s3_key
    dest.parent.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    download_file(s3_key, str(dest))
    log.info("download done job_id=%s s3_key=%s took=%.2fs",
             job_id, s3_key, time.perf_counter() - t0)

    t0 = time.perf_counter()
    ingest_document(str(dest), doc_id=s3_key, job_id=job_id)
    log.info("ingest done job_id=%s s3_key=%s took=%.2fs",
             job_id, s3_key, time.perf_counter() - t0)

    t0 = time.perf_counter()
    mark_ingested([s3_key])
    log.info("mark_ingested done job_id=%s s3_key=%s took=%.2fs",
             job_id, s3_key, time.perf_counter() - t0)


def _handle_delete(s3_key: str, job_id: str) -> None:
    t0 = time.perf_counter()
    delete_by_doc_id(s3_key)
    log.info("qdrant delete done job_id=%s s3_key=%s took=%.2fs",
             job_id, s3_key, time.perf_counter() - t0)

    t0 = time.perf_counter()
    delete_prefix(f"_artifacts/{s3_key}/")  # sweep extracted image artifacts
    log.info("s3 sweep done job_id=%s s3_key=%s took=%.2fs",
             job_id, s3_key, time.perf_counter() - t0)

    t0 = time.perf_counter()
    mark_deleted([s3_key])
    log.info("mark_deleted done job_id=%s s3_key=%s took=%.2fs",
             job_id, s3_key, time.perf_counter() - t0)


def _on_message(ch, method, properties, body: bytes) -> None:
    t_start = time.perf_counter()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        log.exception("dropping malformed message delivery_tag=%s", method.delivery_tag)
        ch.basic_ack(delivery_tag=method.delivery_tag)
        return

    job_id = payload.get("job_id")
    s3_key = payload.get("s3_key")
    op = payload.get("op")
    log.info("received job_id=%s op=%s s3_key=%s", job_id, op, s3_key)
    filename = Path(s3_key).name if s3_key else "(unknown)"
    if op == "ingest":
        user_log.info("Processing %s…", filename)
    elif op == "delete":
        user_log.info("Removing %s…", filename)

    try:
        if op == "ingest":
            _handle_ingest(s3_key, job_id=job_id)
        elif op == "delete":
            _handle_delete(s3_key, job_id=job_id)
        else:
            raise ValueError(f"unknown op: {op!r}")
    except Exception as e:
        elapsed = time.perf_counter() - t_start
        log.exception("job failed job_id=%s s3_key=%s took=%.2fs",
                      job_id, s3_key, elapsed)
        user_log.error("Failed to process %s — %s", filename, _short_error(e))
        try:
            mark_failed(s3_key, str(e))
        except Exception:
            log.exception("mark_failed also failed job_id=%s", job_id)
        ch.basic_ack(delivery_tag=method.delivery_tag)
        return

    elapsed = time.perf_counter() - t_start
    log.info("job done job_id=%s op=%s s3_key=%s took=%.2fs",
             job_id, op, s3_key, elapsed)
    if op == "ingest":
        user_log.info("Finished processing %s (took %.0fs)", filename, elapsed)
    elif op == "delete":
        user_log.info("Removed %s (took %.0fs)", filename, elapsed)
    ch.basic_ack(delivery_tag=method.delivery_tag)


def _short_error(exc: Exception) -> str:
    """One-line, non-scary summary of an exception for user-facing messages."""
    msg = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
    return msg[:200]


def main() -> None:
    log.info("worker starting — preparing downstream resources")
    ensure_bucket()
    create_collection()
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    params = pika.URLParameters(get_settings().rabbitmq_url)
    # heartbeat=0 disables heartbeats. Docling parsing on a large PDF blocks the
    # pika thread for minutes, which trips heartbeats and drops the connection.
    # Proper fix: run the job on a worker thread. Deferring that.
    params.heartbeat = 0
    conn = pika.BlockingConnection(params)
    ch = conn.channel()
    ensure_topology(ch)
    ch.basic_qos(prefetch_count=1)
    ch.basic_consume(queue=QUEUE, on_message_callback=_on_message)
    log.info("consuming queue=%s prefetch=1 (Ctrl+C to stop)", QUEUE)
    try:
        ch.start_consuming()
    except KeyboardInterrupt:
        log.info("interrupt received — stopping consumer")
        ch.stop_consuming()
    finally:
        conn.close()
        log.info("worker exited")


if __name__ == "__main__":
    main()
