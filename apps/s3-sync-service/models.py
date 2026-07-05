# models.py
from datetime import datetime, timezone
from enum import Enum as PyEnum

from sqlalchemy import BigInteger, DateTime, Enum, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from db import Base


class IngestStatus(str, PyEnum):
    PENDING = "pending"
    INGESTED = "ingested"
    FAILED = "failed"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class File(Base):
    """Every S3 object we know about + the state of its last successful ingest.

    Diff logic (see /diff endpoint):
        new       — key seen in S3, not in this table
        modified  — key in both, but s3_etag != ingested_etag (or never ingested)
        deleted   — key in this table, no longer in S3
        unchanged — key in both, s3_etag == ingested_etag
    """

    __tablename__ = "files"

    # S3 key is the natural PK — bucket-scoped uniqueness assumed for this service instance
    s3_key: Mapped[str] = mapped_column(String, primary_key=True)

    # Current S3 state — refreshed on every diff/upsert call
    s3_etag: Mapped[str] = mapped_column(String, nullable=False)
    s3_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    s3_last_modified: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # State of the last successful ingest — null until mark-ingested is called
    ingested_etag: Mapped[str | None] = mapped_column(String, nullable=True)
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    status: Mapped[IngestStatus] = mapped_column(
        Enum(IngestStatus, name="ingest_status"),
        nullable=False,
        default=IngestStatus.PENDING,
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now,
        server_default=func.now(),
    )

    def is_up_to_date(self) -> bool:
        return self.ingested_etag is not None and self.ingested_etag == self.s3_etag
