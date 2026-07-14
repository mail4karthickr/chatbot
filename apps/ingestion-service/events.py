"""Event bus for real-time UI updates.

Every Python `logging` record (from worker, ingest, embed, pika, docling, etc.)
is published to a RabbitMQ fanout exchange (`ingestion.events`). The FastAPI
`/events/stream` endpoint replays and forwards those records to a browser over
Server-Sent Events (SSE).

Design notes:
- **Fanout for cross-process reach.** Workers and the API each publish; both
  target the same exchange.
- **Non-blocking publish.** A daemon thread drains a bounded in-process queue
  and does the actual `basic_publish`. Log calls never wait on network I/O.
  A `threading.local` guard breaks pika's log-during-publish recursion.
- **Reliable delivery to the browser.**
    1. On API startup, we declare a *named, non-exclusive* queue
       (`EVENTS_TAIL_QUEUE`) bound to the fanout. Messages accumulate there
       even when there is no SSE client — surviving browser reconnects,
       uvicorn --reload restarts, and short RabbitMQ hiccups.
    2. A tail thread consumes that queue into an in-memory ring buffer,
       stamping each event with a monotonically increasing `_seq` id.
    3. `/events/stream` reads Last-Event-ID from the request, flushes any
       events after that id from the ring, then blocks on a Condition until
       new events arrive. Native EventSource resume Just Works.
"""
import collections
import json
import logging
import queue
import threading
import time
from datetime import datetime, timezone

import pika

from config import get_settings

EVENTS_EXCHANGE = "ingestion.events"
# Named queue bound to the fanout. Non-exclusive so it survives an SSE client
# disconnect; capped and TTL'd so it can't grow without bound if nobody drains
# it. See `_ensure_tail_queue` for the queue arguments.
EVENTS_TAIL_QUEUE = "ingestion.events.tail"
# Max messages kept on RabbitMQ if no consumer is draining. Oldest are dropped.
_TAIL_MAX_LENGTH = 2000
# Message TTL in ms — five minutes is enough to bridge a uvicorn reload or a
# short network blip without pinning gigabytes of ancient logs.
_TAIL_MSG_TTL_MS = 5 * 60 * 1000
# Number of events kept in the in-memory ring buffer used by /events/stream.
# Must comfortably exceed the events emitted during one ingest run so a
# reconnecting browser can catch up.
_RING_MAX = 2000

_queue: queue.Queue = queue.Queue(maxsize=10000)
_thread: threading.Thread | None = None
_in_publish = threading.local()

# In-memory replay ring populated by `_tail_consumer_loop`. Each item is the
# decoded event dict with an extra `_seq` int (assigned in insertion order).
# The condition variable wakes /events/stream readers whenever a new event
# lands. Access to the deque *and* `_next_seq` must go through the lock.
_ring: collections.deque = collections.deque(maxlen=_RING_MAX)
_ring_cv = threading.Condition()
_next_seq: int = 0
_tail_thread: threading.Thread | None = None


def _params() -> pika.URLParameters:
    p = pika.URLParameters(get_settings().rabbitmq_url)
    p.heartbeat = 0  # long pika consumes/publishes shouldn't trip heartbeats
    return p


def ensure_events_topology(ch) -> None:
    ch.exchange_declare(exchange=EVENTS_EXCHANGE, exchange_type="fanout", durable=False)


def _ensure_tail_queue(ch) -> None:
    """Declare + bind the named tail queue that accumulates events for the API.

    Not durable across broker restarts (events aren't worth persisting to disk),
    but non-exclusive and non-auto-delete so a single subscriber going away
    doesn't blow the queue and drop the log stream.
    """
    ch.queue_declare(
        queue=EVENTS_TAIL_QUEUE,
        durable=False,
        exclusive=False,
        auto_delete=False,
        arguments={
            "x-max-length": _TAIL_MAX_LENGTH,
            "x-message-ttl": _TAIL_MSG_TTL_MS,
            # Drop oldest when full so we always retain the most recent state.
            "x-overflow": "drop-head",
        },
    )
    ch.queue_bind(exchange=EVENTS_EXCHANGE, queue=EVENTS_TAIL_QUEUE)


def _record_event(payload: dict) -> None:
    """Append one event to the in-memory ring, stamping a monotonic seq id.
    Wakes /events/stream readers that are waiting on new events."""
    global _next_seq
    with _ring_cv:
        _next_seq += 1
        payload["_seq"] = _next_seq
        _ring.append(payload)
        _ring_cv.notify_all()


def events_since(seq: int) -> list[dict]:
    """Return a snapshot of events with `_seq > seq`, oldest first."""
    with _ring_cv:
        # Copy under lock; deque iteration is unsafe while another thread appends.
        return [e for e in list(_ring) if e.get("_seq", 0) > seq]


def current_seq() -> int:
    """Return the seq of the most recently recorded event (0 if none).

    Callers use this to detect the "API just restarted, cursor is from a stale
    sequence" case — if the client's Last-Event-ID exceeds current_seq(), the
    seq counter has rolled back and the cursor should be reset to 0.
    """
    with _ring_cv:
        return _next_seq


def wait_for_events(seq: int, timeout: float) -> list[dict]:
    """Block up to `timeout` seconds for events with `_seq > seq`. Returns
    the new events (possibly empty)."""
    with _ring_cv:
        _ring_cv.wait_for(
            lambda: any(e.get("_seq", 0) > seq for e in _ring),
            timeout=timeout,
        )
        return [e for e in list(_ring) if e.get("_seq", 0) > seq]


def _tail_consumer_loop() -> None:
    """Drain EVENTS_TAIL_QUEUE into the in-memory ring buffer forever.

    Runs on a daemon thread. On any pika failure, closes the connection,
    sleeps briefly, and reconnects — the durable queue on the broker keeps
    accumulating events during the outage so nothing is lost.
    """
    log = logging.getLogger("events.tail")
    while True:
        conn = None
        try:
            conn = pika.BlockingConnection(_params())
            ch = conn.channel()
            ensure_events_topology(ch)
            _ensure_tail_queue(ch)
            ch.basic_qos(prefetch_count=64)
            for method, _props, body in ch.consume(
                EVENTS_TAIL_QUEUE, inactivity_timeout=30.0,
            ):
                if body is None:
                    continue
                try:
                    payload = json.loads(body.decode())
                except json.JSONDecodeError:
                    if method is not None:
                        ch.basic_ack(method.delivery_tag)
                    continue
                if isinstance(payload, dict):
                    _record_event(payload)
                if method is not None:
                    ch.basic_ack(method.delivery_tag)
        except Exception:
            log.exception("tail consumer error — reconnecting")
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            time.sleep(1)


def start_tail_consumer() -> None:
    """Start the background tail consumer. Idempotent. Called by the API on
    startup — workers don't need to run this (they only publish)."""
    global _tail_thread
    if _tail_thread is not None and _tail_thread.is_alive():
        return
    _tail_thread = threading.Thread(
        target=_tail_consumer_loop, daemon=True, name="events-tail",
    )
    _tail_thread.start()


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
