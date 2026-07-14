# app.py
import base64
import json
import logging
import mimetypes
import os
import tempfile
import time
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from broker import publish_ingest_jobs
from config import get_settings
from events import current_seq, events_since, start_tail_consumer, wait_for_events
from generate import synthesize_answer
from ingest import _get_parser
from logging_config import setup_logging
from rag import retrieve, warmup as rag_warmup
from storage import (
    create_folder,
    delete_object,
    delete_prefix,
    ensure_bucket,
    get_object,
    head_object_etag,
    list_files,
    list_folder_markers,
    upload_object,
)
from sync_client import diff as sync_diff, reset_ledger
from vectordb import create_collection, reset_collection

INGEST_PREFIX = "docs/"  # only S3 objects under this prefix are ingested

setup_logging()
log = logging.getLogger("app")
user_log = logging.getLogger("user")  # human-friendly milestones for the UI "Info" view

app = FastAPI(title="Multimodal RAG Pipeline")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:5174", "http://127.0.0.1:5174",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup():
    log.info("startup: ensuring bucket + qdrant collection")
    ensure_bucket()
    create_collection()
    # Kick off the background tail consumer. It maintains the durable RabbitMQ
    # queue that buffers events during uvicorn reloads / SSE reconnects, and
    # fills the in-memory ring buffer that /events/stream replays from.
    start_tail_consumer()
    # Preload the reranker so the first /retrieve doesn't pay the model-load
    # penalty (~5–10s cold). Runs async so uvicorn's ready signal isn't delayed.
    import threading
    threading.Thread(target=rag_warmup, daemon=True, name="rag-warmup").start()
    log.info("startup: ready")


class RetrieveRequest(BaseModel):
    query: str
    doc_ids: list[str] | None = None
    top_n: int = 8


class FolderRequest(BaseModel):
    path: str


class ParsePreviewRequest(BaseModel):
    key: str
    # Cap on how many bytes of image data we base64-inline per figure. Docling
    # renders figures at 2× so single-page images can be big; 800 KB keeps the
    # response fast to transfer while still readable at typical thumbnail size.
    max_image_bytes: int = 800_000


@app.get("/s3/files")
async def s3_files():
    """List every object in the configured S3 bucket, plus empty folder markers."""
    try:
        files = list_files()
        folders = list_folder_markers()
    except Exception as e:
        log.exception("s3 list failed")
        raise HTTPException(status_code=502, detail=str(e))
    return {
        "bucket": get_settings().s3_bucket,
        "files": files,
        "folders": folders,
        "count": len(files),
    }


@app.post("/s3/upload")
async def s3_upload(
    files: list[UploadFile] = File(...),
    target: str = Form(""),
):
    """Upload one or more files to `target` prefix. Empty target = bucket root."""
    prefix = target.strip("/") + "/" if target.strip("/") else ""
    uploaded, failed = [], []
    for f in files:
        name = f.filename or "unnamed"
        key = prefix + name
        try:
            data = await f.read()
            upload_object(key, data, f.content_type or "application/octet-stream")
            uploaded.append({"key": key, "bytes": len(data)})
        except Exception as e:
            log.exception("upload failed key=%s", key)
            failed.append({"key": key, "error": str(e)})
    return {"uploaded": uploaded, "failed": failed}


@app.post("/s3/folder")
async def s3_create_folder(req: FolderRequest):
    path = req.path.strip().strip("/")
    if not path:
        raise HTTPException(status_code=400, detail="path is required")
    try:
        key = create_folder(path)
    except Exception as e:
        log.exception("create folder failed path=%s", path)
        raise HTTPException(status_code=502, detail=str(e))
    return {"created": key}


@app.get("/s3/object")
async def s3_get_object(key: str):
    """Stream a single S3 object back to the browser.

    Used by the UI to preview images in-place without exposing S3 credentials or
    presigned URLs. Content-type falls back to a filename-sniffed guess so browsers
    render PNGs/JPEGs as images rather than downloading them.
    """
    key = key.strip().lstrip("/")
    if not key:
        raise HTTPException(status_code=400, detail="key is required")
    try:
        data, ctype = get_object(key)
    except Exception as e:
        log.exception("get object failed key=%s", key)
        raise HTTPException(status_code=502, detail=str(e))
    if not ctype or ctype == "application/octet-stream":
        guessed, _ = mimetypes.guess_type(key)
        if guessed:
            ctype = guessed
    return Response(content=data, media_type=ctype or "application/octet-stream")


@app.delete("/s3/file")
async def s3_delete_file(key: str):
    key = key.strip().lstrip("/")
    if not key:
        raise HTTPException(status_code=400, detail="key is required")
    try:
        delete_object(key)
    except Exception as e:
        log.exception("delete file failed key=%s", key)
        raise HTTPException(status_code=502, detail=str(e))
    return {"deleted": key}


@app.delete("/s3/folder")
async def s3_delete_folder(path: str):
    path = path.strip().strip("/")
    if not path:
        raise HTTPException(status_code=400, detail="path is required")
    try:
        deleted = delete_prefix(path)
    except Exception as e:
        log.exception("delete folder failed path=%s", path)
        raise HTTPException(status_code=502, detail=str(e))
    return {"deleted": deleted, "count": len(deleted)}


@app.post("/ingest", status_code=202)
def ingest():
    """Reconcile S3 ↔ s3-sync-service ledger ↔ Qdrant by enqueuing per-key jobs.

    Fast path: list S3, ask s3-sync-service to classify keys, publish one
    RabbitMQ message per {new, modified, deleted} key. Workers drain the queue
    asynchronously and update the ledger. Unchanged keys are skipped.
    """
    try:
        s3_files = list_files(prefix=INGEST_PREFIX)
    except Exception as e:
        log.exception("s3 list failed during ingest")
        raise HTTPException(status_code=502, detail=str(e))

    diff_payload = [{
        "s3_key": f["key"],
        "s3_etag": f["etag"],
        "s3_size": f["size"],
        "s3_last_modified": f["last_modified"],
    } for f in s3_files]

    try:
        classification = sync_diff(diff_payload, prefix=INGEST_PREFIX)
    except Exception as e:
        log.exception("s3-sync-service /diff failed")
        raise HTTPException(status_code=502, detail=f"s3-sync-service unavailable: {e}")

    new_keys = classification["new"]
    modified = classification["modified"]
    deleted = classification["deleted"]
    unchanged = classification["unchanged"]
    log.info("diff new=%d modified=%d deleted=%d unchanged=%d",
             len(new_keys), len(modified), len(deleted), len(unchanged))

    items = [(k, "ingest") for k in new_keys + modified] + [(k, "delete") for k in deleted]
    try:
        job_ids = publish_ingest_jobs(items)
    except Exception as e:
        log.exception("publish to broker failed")
        raise HTTPException(status_code=502, detail=f"broker unavailable: {e}")

    log.info("enqueued jobs=%d new=%d modified=%d deleted=%d unchanged=%d",
             len(job_ids), len(new_keys), len(modified), len(deleted), len(unchanged))
    if job_ids:
        user_log.info("Queued %d file%s for processing", len(job_ids), "" if len(job_ids) == 1 else "s")
    else:
        user_log.info("Nothing to do — all files are already up to date")
    return {
        "enqueued": len(job_ids),
        "new": new_keys,
        "modified": modified,
        "deleted": deleted,
        "unchanged": unchanged,
        "job_ids": job_ids,
    }


@app.post("/reset")
def reset():
    """Wipe Qdrant, s3-sync-service ledger, and S3 image artifacts.

    Source documents under INGEST_PREFIX are untouched — only the pipeline's
    own extracted image PNGs (stored under _artifacts/) are swept.
    """
    reset_collection()
    try:
        removed = reset_ledger()
    except Exception as e:
        log.exception("ledger reset failed")
        raise HTTPException(status_code=502, detail=f"ledger reset failed: {e}")
    try:
        artifacts = delete_prefix("_artifacts/")
    except Exception as e:
        log.exception("artifact sweep failed")
        raise HTTPException(status_code=502, detail=f"artifact sweep failed: {e}")
    # Preview data is served from an in-process LRU of parsed docs keyed on
    # (S3 key, ETag). Neither changes on reset, so without this clear the next
    # /parse-preview would return the pre-reset parse — visibly stale whenever
    # parser.py has changed since it was cached.
    parse_cache_cleared = _parse_cached.cache_info().currsize
    _parse_cached.cache_clear()
    log.info("reset done qdrant=recreated ledger_rows_removed=%d artifacts_removed=%d parse_cache_cleared=%d",
             removed, len(artifacts), parse_cache_cleared)
    user_log.info("Reset complete — knowledge base is empty and ready to start fresh")
    return {
        "qdrant": "recreated",
        "ledger_rows_removed": removed,
        "artifacts_removed": len(artifacts),
        "parse_cache_cleared": parse_cache_cleared,
    }


@app.post("/retrieve")
async def retrieve_endpoint(req: RetrieveRequest):
    log.info("retrieve received len=%d doc_ids=%s top_n=%d",
             len(req.query), req.doc_ids, req.top_n)
    try:
        return retrieve(req.query, doc_ids=req.doc_ids, top_n=req.top_n)
    except Exception:
        log.exception("retrieve failed")
        raise


@app.post("/generate")
async def generate_endpoint(req: RetrieveRequest):
    """Retrieve top passages, then ask OpenAI to synthesize a grounded answer.

    This is the one-shot RAG path used by the ingestion-ui when the user flips
    the "Generation" toggle on. The full tool-calling agent lives in
    agent-service; this endpoint is deliberately simpler.
    """
    log.info("generate received len=%d doc_ids=%s top_n=%d",
             len(req.query), req.doc_ids, req.top_n)
    try:
        retrieved = retrieve(req.query, doc_ids=req.doc_ids, top_n=req.top_n)
    except Exception:
        log.exception("generate: retrieve failed")
        raise
    try:
        answer, generate_ms = synthesize_answer(
            req.query, retrieved["chunks"], retrieved["images"],
        )
    except Exception:
        log.exception("generate: synthesis failed")
        raise
    timing = dict(retrieved.get("timing") or {})
    timing["generate_ms"] = generate_ms
    if "total_ms" in timing:
        timing["total_ms"] = int(timing["total_ms"]) + generate_ms
    return {
        "answer": answer,
        "chunks": retrieved["chunks"],
        "images": retrieved["images"],
        "timing": timing,
    }


@lru_cache(maxsize=8)
def _parse_cached(key: str, etag: str) -> dict:
    """Fetch an S3 object and run Docling on it, memoized on (key, etag).

    Cache key: (S3 key, S3 ETag) — the ETag flips as soon as the underlying
    bytes change, so replacing a doc via /s3/upload transparently invalidates
    the cached parse. `maxsize=8` keeps the memory footprint bounded (image
    bytes for previous parses stay resident); older documents get evicted LRU.

    Do NOT mutate the returned dict — callers share the same object.
    """
    del etag  # Unused inside the body; only present as a cache-key discriminator.
    try:
        data, _ = get_object(key)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"could not fetch object: {e}")

    # Docling reads from a filesystem path. Round-trip through a temp file so
    # we don't couple parser.py to any particular fetch strategy.
    suffix = Path(key).suffix or ".pdf"
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            elements, version = _get_parser().parse(tmp_path, doc_id=key)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"parse failed: {e}")
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    n_text = sum(1 for e in elements if e["kind"] == "text")
    n_image = len(elements) - n_text
    pages = sorted({int(e.get("page") or 0) for e in elements})
    return {
        "version": version,
        "elements": elements,   # image dicts still carry raw `image_bytes`
        "stats": {
            "elements": len(elements),
            "text": n_text,
            "images": n_image,
            "pages": pages,
        },
    }


@app.post("/parse-preview")
async def parse_preview(req: ParsePreviewRequest):
    """Run Docling on an object *without* touching the pipeline.

    No captioning, no embedding, no Qdrant, no ledger mutations — this endpoint
    only tells you how Docling flattened the doc into text/image elements in
    reading order. Useful when the retrieval feels off and you want to know
    whether the parse or the downstream steps are the problem.

    Result is cached on (S3 key, ETag) so re-opening the modal for the same
    file is instant. Uploading a new version flips the ETag and busts the cache.
    """
    key = req.key.strip().lstrip("/")
    if not key:
        raise HTTPException(status_code=400, detail="key is required")

    try:
        etag = head_object_etag(key)
    except Exception as e:
        log.exception("parse-preview: head failed key=%s", key)
        raise HTTPException(status_code=502, detail=f"could not stat object: {e}")
    if not etag:
        raise HTTPException(status_code=502, detail="object has no ETag")

    hits_before = _parse_cached.cache_info().hits
    t0 = time.perf_counter()
    parsed = _parse_cached(key, etag)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    cache_hit = _parse_cached.cache_info().hits > hits_before

    # Serialize per-request so `max_image_bytes` can vary without polluting
    # the cache key. Base64 encoding is cheap relative to Docling itself.
    out: list[dict] = []
    for i, e in enumerate(parsed["elements"]):
        if e["kind"] == "text":
            out.append({"kind": "text", "page": e["page"], "text": e["text"]})
            continue
        img_bytes: bytes = e["image_bytes"]
        entry = {
            "kind": "image",
            "page": e["page"],
            "image_key": e["image_key"],
            "caption_hint": e.get("caption_hint", ""),
            "context_text": e.get("context_text", ""),
            "img_index": e.get("img_index", i),
            "image_size": len(img_bytes),
        }
        if len(img_bytes) <= req.max_image_bytes:
            entry["image_data_url"] = (
                "data:image/png;base64," + base64.b64encode(img_bytes).decode()
            )
        out.append(entry)

    log.info(
        "parse-preview key=%s etag=%s cache=%s took=%dms elements=%d text=%d images=%d",
        key, etag, "hit" if cache_hit else "miss", elapsed_ms,
        parsed["stats"]["elements"], parsed["stats"]["text"], parsed["stats"]["images"],
    )
    return {
        "doc_id": key,
        "version": parsed["version"],
        "etag": etag,
        "cache": "hit" if cache_hit else "miss",
        "stats": parsed["stats"],
        "elements": out,
    }


@app.get("/events/cursor")
def events_cursor():
    """Return the current tail seq. The UI grabs this right before opening
    /events/stream so the initial connect only replays events *after* this
    point — otherwise a page load would dump the full ring buffer of stale
    log lines into the fresh log view."""
    return {"seq": current_seq()}


@app.get("/events/stream")
def events_stream(
    since: int = 0,
    last_event_id: str | None = Header(default=None),
):
    """SSE stream backed by the in-memory ring buffer in events.py.

    Cursor precedence: the browser's native EventSource remembers the last
    `id:` it saw and sends it back as the `Last-Event-ID` header on auto-
    reconnect — that always wins. On a fresh EventSource open there's no
    header, and the caller's `?since=<seq>` query param is used instead.

    Each SSE frame:
        id: <seq>
        data: {"type":"log","ts":...,"level":...,"logger":...,"message":...}
    """
    if last_event_id:
        try:
            cursor = int(last_event_id)
        except ValueError:
            cursor = 0
    else:
        cursor = max(since, 0)
    # If the ring's max seq is below the client's cursor, the API restarted
    # (uvicorn --reload etc.) and the seq counter reset to 0. The client's
    # cursor now points into a future that no longer exists — reset it so we
    # don't accidentally skip every new event because their seq is < cursor.
    if cursor > current_seq():
        cursor = 0

    def gen():
        seq = cursor
        yield f"data: {json.dumps({'type': 'connected'})}\n\n"

        # Initial replay: everything currently in the ring after the cursor.
        for evt in events_since(seq):
            seq = int(evt.get("_seq", seq))
            yield f"id: {seq}\ndata: {json.dumps(evt)}\n\n"

        # Live tail: wait for new events, emit keepalives on idle.
        while True:
            new = wait_for_events(seq, timeout=15.0)
            if not new:
                yield ": keepalive\n\n"
                continue
            for evt in new:
                seq = int(evt.get("_seq", seq))
                yield f"id: {seq}\ndata: {json.dumps(evt)}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # in case a reverse proxy is in front
        },
    )
