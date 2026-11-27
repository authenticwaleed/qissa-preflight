"""Database queries. Every function takes an open Session and none of them commits."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.audio import AudioInfo
from app.models import AudioFile, Upload
from app.scoring import QualityResult


def insert_audio_if_new(
    session: Session,
    *,
    sha256: bytes,
    storage_key: str,
    info: AudioInfo,
    quality: QualityResult,
) -> bool:
    """INSERT INTO audio_files … ON CONFLICT (sha256) DO NOTHING RETURNING sha256.

    Returns True when this call inserted the row (new audio) and False when the audio was
    already known. PostgreSQL serialises concurrent inserts of the same sha256, so exactly one
    caller gets True.
    """
    # One statement instead of SELECT-then-INSERT: there is no gap between the check and the
    # insert in which a concurrent upload of the same file could also decide it is new.
    statement = (
        insert(AudioFile)
        .values(
            sha256=sha256,
            storage_key=storage_key,
            size_bytes=info.size_bytes,
            duration_seconds=info.duration_seconds,
            bitrate_kbps=info.bitrate_kbps,
            bitrate_mode=info.bitrate_mode.value,
            sample_rate_hz=info.sample_rate_hz,
            channels=info.channels,
            quality_score=quality.score,
            quality_deductions=[
                {"code": d.code, "points": d.points, "message": d.message}
                for d in quality.deductions
            ],
        )
        .on_conflict_do_nothing(index_elements=[AudioFile.sha256])
        .returning(AudioFile.sha256)
    )
    return session.execute(statement).scalar_one_or_none() is not None


def find_original_upload(session: Session, sha256: bytes) -> Upload | None:
    """The first upload of this audio: its row with duplicate_of_id IS NULL."""
    # Same predicate as the partial unique index uq_uploads_original_sha256: one index lookup.
    statement = select(Upload).where(Upload.sha256 == sha256, Upload.duplicate_of_id.is_(None))
    return session.scalars(statement).one_or_none()


def book_duration_stats(session: Session, book_id: str) -> tuple[float | None, int]:
    """(median duration in seconds, chapter count) over the book's original uploads.

    Computed in PostgreSQL with percentile_cont(0.5). Returns (None, 0) for an unknown book.
    """
    # Aggregated in the database, so one row comes back however many chapters the book has.
    statement = (
        select(
            func.percentile_cont(0.5).within_group(AudioFile.duration_seconds),
            func.count(),
        )
        .select_from(Upload)
        .join(Upload.audio)
        .where(Upload.book_id == book_id, Upload.duplicate_of_id.is_(None))
    )
    median, count = session.execute(statement).one()
    return (float(median) if median is not None else None), count


def add_upload(
    session: Session,
    *,
    sha256: bytes,
    original_filename: str,
    book_id: str | None,
    duplicate_of_id: uuid.UUID | None,
    is_duration_outlier: bool,
    outlier_reason: str | None,
) -> Upload:
    """Insert and flush an uploads row; returns it with id and created_at set."""
    upload = Upload(
        sha256=sha256,
        original_filename=original_filename,
        book_id=book_id,
        duplicate_of_id=duplicate_of_id,
        is_duration_outlier=is_duration_outlier,
        outlier_reason=outlier_reason,
    )
    session.add(upload)
    # SQLAlchemy fetches server defaults with the INSERT (… RETURNING created_at), so created_at
    # is loaded without another query and stays readable after the session is closed.
    session.flush()
    return upload


def get_upload(session: Session, upload_id: uuid.UUID) -> Upload | None:
    return session.get(Upload, upload_id)
