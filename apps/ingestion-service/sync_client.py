# sync_client.py — HTTP client for the s3-sync-service ledger
import logging
import time
from functools import lru_cache

import httpx

from config import get_settings

log = logging.getLogger("sync_client")


@lru_cache
def _client() -> httpx.Client:
    return httpx.Client(base_url=get_settings().sync_url, timeout=30.0)


def diff(files: list[dict], prefix: str | None = None) -> dict:
    """POST /diff. `files` items must carry s3_key, s3_etag, s3_size, s3_last_modified.
    Optional prefix scopes the ledger read so cross-prefix rows aren't classified as deleted.
    Returns {new, modified, deleted, unchanged}."""
    t0 = time.perf_counter()
    body: dict = {"files": files}
    if prefix is not None:
        body["prefix"] = prefix
    r = _client().post("/diff", json=body)
    r.raise_for_status()
    result = r.json()
    log.info("diff files=%d new=%d modified=%d deleted=%d unchanged=%d took=%.2fs",
             len(files),
             len(result.get("new", [])), len(result.get("modified", [])),
             len(result.get("deleted", [])), len(result.get("unchanged", [])),
             time.perf_counter() - t0)
    return result


def mark_ingested(files: list[dict]) -> None:
    """POST /files/mark-ingested (registry upsert). Items must carry
    s3_key, etag, size, last_modified — the object state that was ingested."""
    if not files:
        return
    t0 = time.perf_counter()
    r = _client().post("/files/mark-ingested", json={"files": files})
    r.raise_for_status()
    log.info("mark_ingested files=%d took=%.2fs",
             len(files), time.perf_counter() - t0)


def mark_deleted(keys: list[str]) -> None:
    if not keys:
        return
    t0 = time.perf_counter()
    r = _client().post("/files/mark-deleted", json={"keys": keys})
    r.raise_for_status()
    log.info("mark_deleted keys=%d took=%.2fs",
             len(keys), time.perf_counter() - t0)


def reset_ledger() -> int:
    """Nuke every row from the s3-sync-service ledger. Returns rows removed."""
    r = _client().post("/files/reset")
    r.raise_for_status()
    return int(r.json().get("removed", 0))
