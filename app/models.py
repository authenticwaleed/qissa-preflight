"""Database tables.

audio_files: one row per unique audio content, keyed by its SHA-256, so each file and its analysis
are stored once however often it is uploaded.
uploads: one row per upload request (the audit trail), duplicates included.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    Index,
    LargeBinary,
    MetaData,
    SmallInteger,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    # Predictable constraint names, so migrations can refer to them.
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class AudioFile(Base):
    __tablename__ = "audio_files"
    __table_args__ = (
        CheckConstraint("octet_length(sha256) = 32", name="sha256_length"),
        CheckConstraint("quality_score BETWEEN 1 AND 10", name="quality_score_range"),
        CheckConstraint("bitrate_mode IN ('CBR', 'VBR', 'ABR', 'UNKNOWN')", name="bitrate_mode"),
    )

    # The raw 32-byte digest: half the size of a hex string in the primary key index.
    sha256: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    storage_key: Mapped[str] = mapped_column(String(80))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    duration_seconds: Mapped[float] = mapped_column(Double)
    bitrate_kbps: Mapped[int]
    bitrate_mode: Mapped[str] = mapped_column(String(8))
    sample_rate_hz: Mapped[int]
    channels: Mapped[int] = mapped_column(SmallInteger)
    quality_score: Mapped[int] = mapped_column(SmallInteger)
    quality_deductions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Upload(Base):
    __tablename__ = "uploads"
    __table_args__ = (
        # At most one original upload per audio; every later upload of it is a duplicate.
        # It also serves the "find the original upload" lookup.
        Index(
            "uq_uploads_original_sha256",
            "sha256",
            unique=True,
            postgresql_where=text("duplicate_of_id IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    sha256: Mapped[bytes] = mapped_column(ForeignKey("audio_files.sha256"))
    original_filename: Mapped[str] = mapped_column(String(255))
    book_id: Mapped[str | None] = mapped_column(String(100), index=True)
    duplicate_of_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("uploads.id"))
    is_duration_outlier: Mapped[bool]
    outlier_reason: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    audio: Mapped[AudioFile] = relationship(lazy="joined")
    duplicate_of: Mapped["Upload | None"] = relationship(remote_side="Upload.id")
