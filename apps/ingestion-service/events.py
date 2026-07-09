"""Event bus for real-time UI updates.

Every Python `logging` record (from worker, ingest, embed, pika, docling, etc.)
is published to a RabbitMQ fanout exchange (`ingestion.events`). The FastAPI
`/events/stream` endpoint tails this exchange via a temporary exclusive queue
and forwards records to a browser over Server-Sent Events (SSE).

Design notes:
- Fanout so multiple UI tabs (or curl clients) each see every event without
  coordinating. Each subscriber declares its own auto-delete queue.
- Publishing is async via a background daemon thread + bounded queue. Log calls
  never block on network I/O; if the publisher can't keep up, records are
  dropped rather than backpressuring the caller.
- A threading.local `_in_publish` guard breaks the log-during-publish recursion
  (pika itself emits logs while sending, which would otherwise loop).
"""
import json
import logging
import queue
import threading
import time
from datetime import datetime, timezone

import pika

from config import get_settings

EVENTS_EXCHANGE = "ingestion.events"

_queue: queue.Queue = queue.Queue(maxsize=10000)
_thread: threading.Thread | None = None
_in_publish = threading.local()


def _params() -> pika.URLParameters:
    p = pika.URLParameters(get_settings().rabbitmq_url)
    p.heartbeat = 0  # long pika consumes/publishes shouldn't trip heartbeats
    return p


def ensure_events_topology(ch) -> None:
    ch.exchange_declare(exchange=EVENTS_EXCHANGE, exchange_type="fanout", durable=False)


def _publisher_loop() -> None:
    """Drain the in-process queue and publish each event to RabbitMQ.

    Reconnects on any pika failure. Never crashes — failures print via
    traceback (not logging, to avoid recursion) and retry after a short sleep.
    """
    conn = None
    ch = None
    while True:
        event = _queue.get()
        if event is None:
            return
        try:
            if conn is None or conn.is_closed:
                conn = pika.BlockingConnection(_params())
                ch = conn.channel()
                ensure_events_topology(ch)
            _in_publish.value = True
            try:
                ch.basic_publish(
                    exchange=EVENTS_EXCHANGE,
                    routing_key="",
                    body=json.dumps(event).encode(),
                    properties=pika.BasicProperties(content_type="application/json"),
                )
            finally:
                _in_publish.value = False
        except Exception:
            import traceback
            traceback.print_exc()
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass
            conn = None
            ch = None
            time.sleep(1)


def _start_publisher() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _thread = threading.Thread(target=_publisher_loop, daemon=True, name="events-publisher")
    _thread.start()


class LogEventHandler(logging.Handler):
    """A logging Handler that forwards every LogRecord as an SSE event."""

    def emit(self, record: logging.LogRecord) -> None:
        # Break recursion: pika logs from inside our publish call must not loop.
        if getattr(_in_publish, "value", False):
            return
        try:
            event = {
                "type": "log",
                "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            if record.exc_info:
                event["exception"] = self.format(record)
            try:
                _queue.put_nowait(event)
            except queue.Full:
                pass  # drop on backpressure — the UI never blocks the pipeline
        except Exception:
            pass  # a broken handler must never break the caller


def install_log_handler(level: int = logging.INFO) -> None:
    """Attach the streaming handler to the root logger. Idempotent."""
    root = logging.getLogger()
    for h in root.handlers:
        if isinstance(h, LogEventHandler):
            return
    root.addHandler(LogEventHandler(level=level))
    _start_publisher()
