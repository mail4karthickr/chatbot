# schemas.py — Pydantic request/response DTOs
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class S3FileState(BaseModel):
    """Snapshot of a single S3 object as observed/ingested by the caller."""
    s3_key: str
    etag: str
    size: int
    last_modified: datetime


class DiffRequest(BaseModel):
    files: list[S3FileState]
    prefix: str | None = None   # scope ledger reads to one prefix


class DiffResponse(BaseModel):
    new: list[str]
    modified: list[str]
    deleted: list[str]
    unchanged: list[str]


class MarkIngestedRequest(BaseModel):
    files: list[S3FileState]    # the object state that was actually ingested


class MarkDeletedRequest(BaseModel):
    keys: list[str]


class FileRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    s3_key: str
    etag: str
    size: int
    last_modified: datetime
    ingested_at: datetime


class FileListResponse(BaseModel):
    files: list[FileRecord]
    count: int
