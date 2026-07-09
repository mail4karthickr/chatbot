# app.py
import logging

import pika
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from broker import publish_ingest_jobs
from config import get_settings
from events import EVENTS_EXCHANGE, ensure_events_topology, _params as _events_params
from logging_config import setup_logging
from rag import retrieve, warmup as rag_warmup
from storage import (
    create_folder,
    delete_object,
    delete_prefix,
    ensure_bucket,
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
    log.info("reset done qdrant=recreated ledger_rows_removed=%d artifacts_removed=%d",
             removed, len(artifacts))
    user_log.info("Reset complete — knowledge base is empty and ready to start fresh")
    return {
        "qdrant": "recreated",
        "ledger_rows_removed": removed,
        "artifacts_removed": len(artifacts),
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


@app.get("/events/stream")
def events_stream():
    """Server-Sent Events stream of every log record from the API and workers.

    Each SSE message is one JSON object of shape:
        {"type": "log", "ts": ..., "level": ..., "logger": ..., "message": ...}

    The client (browser EventSource or `curl -N`) sees every log line from any
    process that has the events LogEventHandler installed (API + workers).
    """
    import json as _json

    def gen():
        conn = pika.BlockingConnection(_events_params())
        ch = conn.channel()
        ensure_events_topology(ch)
        # Anonymous exclusive queue, auto-deleted when this client disconnects.
        result = ch.queue_declare(queue="", exclusive=True, auto_delete=True)
        qname = result.method.queue
        ch.queue_bind(exchange=EVENTS_EXCHANGE, queue=qname)

        try:
            yield f"data: {_json.dumps({'type': 'connected'})}\n\n"
            # inactivity_timeout keeps the generator alive so we can emit
            # SSE keep-alive comments and detect client disconnects promptly.
            for method, _props, body in ch.consume(qname, inactivity_timeout=15.0):
                if body is None:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {body.decode()}\n\n"
                if method is not None:
                    ch.basic_ack(method.delivery_tag)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # in case a reverse proxy is in front
        },
    )
