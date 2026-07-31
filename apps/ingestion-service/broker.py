"""RabbitMQ producer for ingestion jobs.

One S3 key = one message. Consumers (ingestion-worker) drain the queue in parallel.
Exchange + queue are declared idempotently on every publish so the API can start
before the broker has any topology set up.
"""
import json
import logging
import uuid

import pika

from config import get_settings

log = logging.getLogger("broker")

EXCHANGE = "ingestion"
QUEUE = "ingestion.jobs"
ROUTING_KEY = "ingest.doc"


def ensure_topology(ch) -> None:
    ch.exchange_declare(exchange=EXCHANGE, exchange_type="direct", durable=True)
    ch.queue_declare(queue=QUEUE, durable=True)
    ch.queue_bind(queue=QUEUE, exchange=EXCHANGE, routing_key=ROUTING_KEY)


def publish_ingest_jobs(items: list[tuple[str, str, dict | None]]) -> list[str]:
    """Publish one message per (s3_key, op, file_state) tuple. `op` is "ingest"
    or "delete". For ingest ops, `file_state` = {etag, size, last_modified} as
    observed at listing time — the worker reports it to the registry on
    success. Returns the generated job_ids in input order. One connection per
    call — fine for burst-per-request use.
    """
    if not items:
        return []
    params = pika.URLParameters(get_settings().rabbitmq_url)
    params.heartbeat = 60
    job_ids: list[str] = []
    with pika.BlockingConnection(params) as conn:
        ch = conn.channel()
        ensure_topology(ch)
        for s3_key, op, state in items:
            job_id = str(uuid.uuid4())
            msg = {
                "job_id": job_id,
                "s3_key": s3_key,
                "op": op,
                "attempt": 1,
            }
            if state is not None:
                msg["etag"] = state["etag"]
                msg["size"] = state["size"]
                msg["last_modified"] = state["last_modified"]
            body = json.dumps(msg).encode()
            ch.basic_publish(
                exchange=EXCHANGE,
                routing_key=ROUTING_KEY,
                body=body,
                properties=pika.BasicProperties(
                    delivery_mode=2,
                    content_type="application/json",
                    message_id=job_id,
                ),
            )
            log.info("published job_id=%s op=%s s3_key=%s", job_id, op, s3_key)
            job_ids.append(job_id)
    return job_ids
