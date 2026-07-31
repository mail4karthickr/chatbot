# models.py
from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class File(Base):
    """Registry of successfully ingested documents — one row per doc that is
    IN the vector index. Rows are written only by mark-ingested (upsert) and
    removed only by mark-deleted. Failures write nothing: a failed document
    is absent (or stale), so the next /diff classifies it as new (or
    modified) and ingestion retries it naturally.

    Diff logic (see /diff — pure read):
        new       — key in S3, not in this table
        modified  — key in both, S3 etag != stored etag
        deleted   — key in this table, no longer in S3
        unchanged — key in both, etags match
    """

    __tablename__ = "files"

    s3_key: Mapped[str] = mapped_column(String, primary_key=True)
    etag: Mapped[str] = mapped_column(String, nullable=False)   # of the ingested object
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_modified: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=_now, server_default=func.now(), onupdate=_now,
    )
