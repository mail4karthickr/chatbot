# app.py — S3-file metadata / ingest-tracking service
import logging

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from db import get_session, init_db
from models import File, IngestStatus, _now
from schemas import (
    DiffRequest,
    DiffResponse,
    FileListResponse,
    FileRecord,
    MarkDeletedRequest,
    MarkFailedRequest,
    MarkIngestedRequest,
    S3FileState,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("sync")

app = FastAPI(title="Sync Service")

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
    log.info("startup: creating tables if needed")
    init_db()
    log.info("startup: ready")


def _session() -> Session:
    s = get_session()
    try:
        yield s
    finally:
        s.close()


@app.get("/health")
def health():
    return {"status": "ok"}


# --------------------------------------------------------------------------- diff

@app.post("/diff", response_model=DiffResponse)
def diff(req: DiffRequest, s: Session = Depends(_session)) -> DiffResponse:
    """Classify a caller-supplied S3 file list against the DB.

    Also refreshes the current S3 state on every known row so the DB always
    reflects reality. Callers should follow up with mark-ingested / mark-deleted
    once they've actually processed the diff.
    """
    incoming: dict[str, S3FileState] = {f.s3_key: f for f in req.files}
    known_q = select(File)
    if req.prefix:
        # Prefix-scoped diff: only reconcile rows under this prefix so callers
        # can partition the ledger (e.g. one caller owns "docs/", another owns "logs/").
        known_q = known_q.where(File.s3_key.startswith(req.prefix))
    known = {row.s3_key: row for row in s.execute(known_q).scalars().all()}

    new_keys: list[str] = []
    modified: list[str] = []
    unchanged: list[str] = []

    for key, snap in incoming.items():
        row = known.get(key)
        if row is None:
            new_keys.append(key)
            s.add(File(
                s3_key=key,
                s3_etag=snap.s3_etag,
                s3_size=snap.s3_size,
                s3_last_modified=snap.s3_last_modified,
                status=IngestStatus.PENDING,
            ))
        else:
            # Always refresh observed state
            row.s3_etag = snap.s3_etag
            row.s3_size = snap.s3_size
            row.s3_last_modified = snap.s3_last_modified
            if row.is_up_to_date():
                unchanged.append(key)
            else:
                modified.append(key)

    deleted = [k for k in known.keys() if k not in incoming]

    s.commit()
    log.info(
        "diff new=%d modified=%d deleted=%d unchanged=%d (incoming=%d, known=%d)",
        len(new_keys), len(modified), len(deleted), len(unchanged), len(incoming), len(known),
    )
    return DiffResponse(new=new_keys, modified=modified, deleted=deleted, unchanged=unchanged)


# ---------------------------------------------------------- mark-ingested / failed

@app.post("/files/mark-ingested")
def mark_ingested(req: MarkIngestedRequest, s: Session = Depends(_session)):
    if not req.keys:
        return {"updated": 0}
    rows = s.execute(select(File).where(File.s3_key.in_(req.keys))).scalars().all()
    found = {r.s3_key for r in rows}
    missing = [k for k in req.keys if k not in found]
    if missing:
        raise HTTPException(status_code=404, detail={"missing_keys": missing})
    now = _now()
    for r in rows:
        r.ingested_etag = r.s3_etag
        r.ingested_at = now
        r.status = IngestStatus.INGESTED
        r.error = None
    s.commit()
    return {"updated": len(rows)}


@app.post("/files/mark-failed")
def mark_failed(req: MarkFailedRequest, s: Session = Depends(_session)):
    row = s.get(File, req.key)
    if row is None:
        raise HTTPException(status_code=404, detail="key not found")
    row.status = IngestStatus.FAILED
    row.error = req.error
    s.commit()
    return {"updated": 1}


@app.post("/files/mark-deleted")
def mark_deleted(req: MarkDeletedRequest, s: Session = Depends(_session)):
    if not req.keys:
        return {"removed": 0}
    rows = s.execute(select(File).where(File.s3_key.in_(req.keys))).scalars().all()
    for r in rows:
        s.delete(r)
    s.commit()
    return {"removed": len(rows)}


@app.post("/files/reset")
def reset_files(s: Session = Depends(_session)):
    """Delete every row from the files table. Testing utility — no auth, no undo."""
    res = s.execute(delete(File))
    s.commit()
    removed = res.rowcount or 0
    log.info("reset removed=%d", removed)
    return {"removed": removed}


# ---------------------------------------------------------------------- read side

@app.get("/files", response_model=FileListResponse)
def list_all(s: Session = Depends(_session)) -> FileListResponse:
    rows = s.execute(select(File).order_by(File.s3_key)).scalars().all()
    return FileListResponse(
        files=[FileRecord.model_validate(r) for r in rows],
        count=len(rows),
    )
