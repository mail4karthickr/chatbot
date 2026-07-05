# app.py
import logging
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import get_settings
from ingest import ingest_document
from logging_config import setup_logging
from rag import retrieve
from storage import (
    create_folder,
    delete_object,
    delete_prefix,
    download_file,
    ensure_bucket,
    list_files,
    list_folder_markers,
    upload_object,
)
from sync_client import diff as sync_diff, mark_deleted, mark_failed, mark_ingested, reset_ledger
from vectordb import create_collection, delete_by_doc_id, reset_collection

INGEST_DIR = Path(__file__).parent / "data" / "ingested"
INGEST_PREFIX = "docs/"  # only S3 objects under this prefix are ingested

setup_logging()
log = logging.getLogger("app")

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


@app.post("/ingest")
def ingest():
    """Reconcile S3 ↔ s3-sync-service ledger ↔ Qdrant.

    Steps:
      1. List every object in the S3 bucket.
      2. Ask the s3-sync-service to classify them: {new, modified, deleted, unchanged}.
      3. For new/modified — download, parse, embed, upsert into Qdrant, mark-ingested.
      4. For deleted — drop the Qdrant points, sweep leftover image artifacts under
         the doc_id prefix, mark-deleted (which hard-deletes the ledger row).
      5. Skip unchanged.

    Sync/blocking for now — will move to a RabbitMQ worker later.
    """
    INGEST_DIR.mkdir(parents=True, exist_ok=True)

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

    ingested_ok: list[str] = []
    reingested_ok: list[str] = []
    failed: list[dict] = []

    for key in new_keys + modified:
        dest = INGEST_DIR / key
        try:
            download_file(key, str(dest))
            ingest_document(str(dest), doc_id=key)
        except Exception as e:
            log.exception("ingest failed key=%s", key)
            failed.append({"key": key, "error": str(e)})
            try:
                mark_failed(key, str(e))
            except Exception:
                log.exception("mark_failed also failed key=%s", key)
            continue
        (ingested_ok if key in new_keys else reingested_ok).append(key)

    if ingested_ok or reingested_ok:
        try:
            mark_ingested(ingested_ok + reingested_ok)
        except Exception:
            log.exception("mark_ingested failed")

    deleted_ok: list[str] = []
    for key in deleted:
        try:
            delete_by_doc_id(key)
            delete_prefix(f"{key}/")  # sweep extracted image artifacts
        except Exception as e:
            log.exception("cleanup failed key=%s", key)
            failed.append({"key": key, "error": f"cleanup: {e}"})
            continue
        deleted_ok.append(key)

    if deleted_ok:
        try:
            mark_deleted(deleted_ok)
        except Exception:
            log.exception("mark_deleted failed")

    log.info("ingest done new=%d modified=%d deleted=%d unchanged=%d failed=%d",
             len(ingested_ok), len(reingested_ok), len(deleted_ok), len(unchanged), len(failed))
    return {
        "ingested": ingested_ok,
        "reingested": reingested_ok,
        "deleted": deleted_ok,
        "unchanged": unchanged,
        "failed": failed,
    }


@app.post("/reset")
def reset():
    """Wipe Qdrant + s3-sync-service ledger. Testing utility — does NOT touch S3."""
    reset_collection()
    try:
        removed = reset_ledger()
    except Exception as e:
        log.exception("ledger reset failed")
        raise HTTPException(status_code=502, detail=f"ledger reset failed: {e}")
    log.info("reset done qdrant=recreated ledger_rows_removed=%d", removed)
    return {"qdrant": "recreated", "ledger_rows_removed": removed}


@app.post("/retrieve")
async def retrieve_endpoint(req: RetrieveRequest):
    log.info("retrieve received len=%d doc_ids=%s top_n=%d",
             len(req.query), req.doc_ids, req.top_n)
    try:
        return retrieve(req.query, doc_ids=req.doc_ids, top_n=req.top_n)
    except Exception:
        log.exception("retrieve failed")
        raise
