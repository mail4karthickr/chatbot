# app.py — registry of successfully ingested documents
import logging

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from db import get_session, init_db
from models import File, _now
from schemas import (
    DiffRequest,
    DiffResponse,
    FileListResponse,
    FileRecord,
    MarkDeletedRequest,
    MarkIngestedRequest,
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
    """Classify a caller-supplied S3 file list against the ingested registry.

    PURE READ — the ledger changes only via mark-ingested / mark-deleted.
    A previously failed ingest simply never wrote its row, so its key shows
    up as `new` (or `modified`) again here: that IS the retry mechanism.
    """
    incoming = {f.s3_key: f for f in req.files}
    known_q = select(File)
    if req.prefix:
        # Prefix-scoped diff: only consider registry rows under this prefix so
        # callers can partition the ledger (e.g. one caller owns "docs/").
        known_q = known_q.where(File.s3_key.startswith(req.prefix))
    known = {row.s3_key: row for row in s.execute(known_q).scalars().all()}

    new_keys  = [k for k in incoming if k not in known]
    modified  = [k for k, f in incoming.items() if k in known and known[k].etag != f.etag]
    unchanged = [k for k, f in incoming.items() if k in known and known[k].etag == f.etag]
    deleted   = [k for k in known if k not in incoming]

    log.info(
        "diff new=%d modified=%d deleted=%d unchanged=%d (incoming=%d, known=%d)",
        len(new_keys), len(modified), len(deleted), len(unchanged), len(incoming), len(known),
    )
    return DiffResponse(new=new_keys, modified=modified, deleted=deleted, unchanged=unchanged)


# ---------------------------------------------------------- mark-ingested / deleted

@app.post("/files/mark-ingested")
def mark_ingested(req: MarkIngestedRequest, s: Session = Depends(_session)):
    """Upsert: create the row on first ingest, refresh etag on re-ingest.
    The caller reports the object state it actually processed."""
    for f in req.files:
        row = s.get(File, f.s3_key)
        if row is None:
            s.add(File(s3_key=f.s3_key, etag=f.etag, size=f.size,
                       last_modified=f.last_modified))
        else:
            row.etag, row.size, row.last_modified = f.etag, f.size, f.last_modified
            row.ingested_at = _now()
    s.commit()
    return {"updated": len(req.files)}


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
