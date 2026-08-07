"""Assert the settings a service RESOLVES point only at test infrastructure.

Design doc §5.3 (second review #3): checking the shell variables the launcher
exports is not enough — pydantic-settings merges real env vars with the
service's .env file, honours aliases, and has its own empty-string semantics.
The only trustworthy check imports the service's own Settings class, inside the
service's own venv, under the sanitized environment, and asserts the *resolved*
values. start-test-stack.sh runs this for each service and refuses to launch
anything if it exits non-zero.

Usage (run BY THE LAUNCHER, with the sanitized env already applied):
    apps/ingestion-service/.venv/bin/python \
        apps/ingestion-service/evals/validate_effective_settings.py --service ingestion
    apps/s3-sync-service/.venv/bin/python \
        apps/ingestion-service/evals/validate_effective_settings.py --service sync
"""

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

APPS = Path(__file__).resolve().parents[2]  # .../Chatbot/apps

LOCAL_HOSTS = {"localhost", "127.0.0.1"}


def fail(msg: str) -> None:
    print(f"VALIDATION FAILED: {msg}", file=sys.stderr)
    sys.exit(1)


def assert_local(name: str, url: str) -> None:
    host = urlparse(url).hostname
    if host not in LOCAL_HOSTS:
        fail(f"{name}={url!r} resolves to host {host!r}, not localhost")


def assert_port(name: str, url: str, expected: int) -> None:
    port = urlparse(url).port
    if port != expected:
        fail(f"{name}={url!r} uses port {port}, expected {expected}")


def validate_ingestion() -> None:
    service_dir = APPS / "ingestion-service"
    os.chdir(service_dir)          # Settings loads .env relative to CWD
    sys.path.insert(0, str(service_dir))
    from config import get_settings  # the service's real Settings class

    s = get_settings()
    assert_local("qdrant_url", s.qdrant_url)
    assert_port("qdrant_url", s.qdrant_url, 6333)
    assert_local("s3_endpoint", s.s3_endpoint)
    assert_port("s3_endpoint", s.s3_endpoint, 9000)
    assert_local("sync_url", s.sync_url)
    assert_port("sync_url", s.sync_url, 8013)
    assert_local("rabbitmq_url", s.rabbitmq_url)
    assert_port("rabbitmq_url", s.rabbitmq_url, 5673)
    if not s.s3_bucket.endswith("-eval"):
        fail(f"s3_bucket={s.s3_bucket!r} lacks the -eval suffix")
    if s.qdrant_api_key:  # empty-string override must actually win over .env
        fail(f"qdrant_api_key resolved non-empty (len={len(s.qdrant_api_key)}) — "
             "the .env cloud key leaked through")
    if not s.openai_api_key:
        fail("openai_api_key is empty — .env fallback for non-backend vars broke")
    print("ingestion-service effective settings OK:")
    print(f"  qdrant_url   = {s.qdrant_url}")
    print(f"  s3_endpoint  = {s.s3_endpoint}  bucket={s.s3_bucket}")
    print(f"  rabbitmq_url = {s.rabbitmq_url}")
    print(f"  sync_url     = {s.sync_url}")
    print(f"  models       = embed:{s.embed_model} caption:{s.caption_model}")


def validate_sync() -> None:
    service_dir = APPS / "s3-sync-service"
    os.chdir(service_dir)
    sys.path.insert(0, str(service_dir))
    from config import get_settings

    s = get_settings()
    assert_local("database_url", s.database_url)
    assert_port("database_url", s.database_url, 5434)
    print("s3-sync-service effective settings OK:")
    print(f"  database_url = {s.database_url}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--service", choices=["ingestion", "sync"], required=True)
    args = parser.parse_args()
    if args.service == "ingestion":
        validate_ingestion()
    else:
        validate_sync()


if __name__ == "__main__":
    main()
