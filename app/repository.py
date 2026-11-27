"""Database queries. Every function takes an open Session and none of them commits."""

import uuid

from sqlalchemy.orm import Session

from app.audio import AudioInfo
from app.models import Upload
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
    raise NotImplementedError


def find_original_upload(session: Session, sha256: bytes) -> Upload | None:
    """The first upload of this audio: its row with duplicate_of_id IS NULL."""
    raise NotImplementedError


def book_duration_stats(session: Session, book_id: str) -> tuple[float | None, int]:
    """(median duration in seconds, chapter count) over the book's original uploads.

    Computed in PostgreSQL with percentile_cont(0.5). Returns (None, 0) for an unknown book.
    """
    raise NotImplementedError


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
    raise NotImplementedError


def get_upload(session: Session, upload_id: uuid.UUID) -> Upload | None:
    raise NotImplementedError
