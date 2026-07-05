# sync_client.py — HTTP client for the s3-sync-service ledger
from functools import lru_cache

import httpx

from config import get_settings


@lru_cache
def _client() -> httpx.Client:
    return httpx.Client(base_url=get_settings().sync_url, timeout=30.0)


def diff(files: list[dict], prefix: str | None = None) -> dict:
    """POST /diff. `files` items must carry s3_key, s3_etag, s3_size, s3_last_modified.
    Optional prefix scopes the ledger read so cross-prefix rows aren't classified as deleted.
    Returns {new, modified, deleted, unchanged}."""
    body: dict = {"files": files}
    if prefix is not None:
        body["prefix"] = prefix
    r = _client().post("/diff", json=body)
    r.raise_for_status()
    return r.json()


def mark_ingested(keys: list[str]) -> None:
    if not keys:
        return
    r = _client().post("/files/mark-ingested", json={"keys": keys})
    r.raise_for_status()


def mark_failed(key: str, error: str) -> None:
    r = _client().post("/files/mark-failed", json={"key": key, "error": error})
    r.raise_for_status()


def mark_deleted(keys: list[str]) -> None:
    if not keys:
        return
    r = _client().post("/files/mark-deleted", json={"keys": keys})
    r.raise_for_status()


def reset_ledger() -> int:
    """Nuke every row from the s3-sync-service ledger. Returns rows removed."""
    r = _client().post("/files/reset")
    r.raise_for_status()
    return int(r.json().get("removed", 0))
