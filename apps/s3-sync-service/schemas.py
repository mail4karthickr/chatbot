# schemas.py — Pydantic request/response DTOs
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class S3FileState(BaseModel):
    """Snapshot of a single S3 object as observed by the caller."""
    s3_key: str
    s3_etag: str
    s3_size: int
    s3_last_modified: datetime


class DiffRequest(BaseModel):
    files: list[S3FileState]
    prefix: str | None = None  # if set, only ledger rows under this prefix are considered


class DiffResponse(BaseModel):
    new: list[str]
    modified: list[str]
    deleted: list[str]
    unchanged: list[str]


class MarkIngestedRequest(BaseModel):
    keys: list[str]


class MarkFailedRequest(BaseModel):
    key: str
    error: str


class MarkDeletedRequest(BaseModel):
    keys: list[str]


class FileRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    s3_key: str
    s3_etag: str
    s3_size: int
    s3_last_modified: datetime
    ingested_etag: str | None
    ingested_at: datetime | None
    status: str
    error: str | None
    created_at: datetime
    updated_at: datetime


class FileListResponse(BaseModel):
    files: list[FileRecord]
    count: int
