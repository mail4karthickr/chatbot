# logging_config.py
import logging
import os


def setup_logging() -> None:
    """Configure root logging once. Level via LOG_LEVEL env (default INFO).

    Also attaches the SSE event-stream handler unless STREAM_EVENTS=0. Every
    log record then also flows to the /events/stream SSE endpoint via
    RabbitMQ. See events.py for the mechanism.
    """
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("botocore", "boto3", "urllib3", "httpx", "httpcore", "s3transfer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if os.getenv("STREAM_EVENTS", "1") != "0":
        from events import install_log_handler
        install_log_handler(level=logging.INFO)
