"""The upload use case: store the file, analyse it, detect duplicates, flag outliers, save."""

import logging
import time
from typing import BinaryIO

from sqlalchemy.orm import Session

from app import repository
from app.audio import AudioInfo, read_audio_info
from app.config import Settings
from app.errors import EmptyFile, UnsupportedFileType
from app.models import Upload
from app.outliers import OutlierResult, OutlierRule, check_duration
from app.scoring import QualityResult, score_quality
from app.storage import LocalStorage, StagedFile

logger = logging.getLogger(__name__)

MP3_SUFFIX = ".mp3"
MAX_FILENAME_LENGTH = 255  # uploads.original_filename is VARCHAR(255)
# C0 control characters and DEL: PostgreSQL rejects NUL in text, and none of them belongs in a
# name that is shown to people.
CONTROL_CHARACTERS = dict.fromkeys([*range(0x20), 0x7F])


def process_upload(
    *,
    stream: BinaryIO,
    filename: str | None,
    book_id: str | None,
    session: Session,
    storage: LocalStorage,
    settings: Settings,
) -> Upload:
    """Handle one uploaded file and return its saved (committed) Upload row.

    1. The filename must end in .mp3 (UnsupportedFileType). Keep only its last path part,
       at most 255 characters.
    2. storage.stage(stream, settings.max_upload_bytes) (FileTooLarge); 0 bytes -> EmptyFile.
    3. read_audio_info(staged.path) (InvalidMp3), then score_quality(info).
    4. With a book_id: repository.book_duration_stats(book_id), read before this upload is
       inserted; then check_duration(...) with the rule built from settings.
    5. repository.insert_audio_if_new(...); known audio -> repository.find_original_upload(sha256).
       Then storage.commit(staged), new or known: it keeps a file that is already stored and
       drops the copy, and it puts the bytes back if the stored file has gone missing.
    6. repository.add_upload(...), then session.commit().
    On any error: discard the staged file, roll back, re-raise.
    """
    started = time.perf_counter()
    display_name = _display_filename(filename)
    staged = storage.stage(stream, settings.max_upload_bytes)
    try:
        if staged.size_bytes == 0:
            raise EmptyFile("The file is empty.")
        info = read_audio_info(staged.path)
        quality = score_quality(info)
        outlier = _check_duration(session, settings, book_id, info.duration_seconds)
        original = _store_audio(session, storage, staged, info, quality)
        upload = repository.add_upload(
            session,
            sha256=staged.sha256,
            original_filename=display_name,
            book_id=book_id,
            duplicate_of_id=original.id if original else None,
            is_duration_outlier=outlier.is_outlier,
            outlier_reason=outlier.reason,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        # A no-op once the file was committed; on every error path it is the cleanup that keeps
        # temp files from piling up.
        storage.discard(staged)

    logger.info(
        "upload id=%s sha256=%s size_bytes=%d duration_s=%.2f score=%d duplicate=%s "
        "outlier=%s elapsed_ms=%d",
        upload.id,
        staged.sha256_hex[:12],
        staged.size_bytes,
        info.duration_seconds,
        quality.score,
        str(upload.duplicate_of_id is not None).lower(),
        str(outlier.is_outlier).lower(),
        (time.perf_counter() - started) * 1000,
    )
    return upload


def _display_filename(filename: str | None) -> str:
    """The client's filename, kept for people to read. Files are stored under their SHA-256, so
    this never becomes a path on disk.

    Raises UnsupportedFileType unless it ends in .mp3, in any case.
    """
    # Some clients send the whole path, with Windows backslashes.
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = name.translate(CONTROL_CHARACTERS)
    if not name.lower().endswith(MP3_SUFFIX):
        raise UnsupportedFileType("Only .mp3 files are accepted.")
    if len(name) > MAX_FILENAME_LENGTH:
        # Shorten the stem rather than cut off the extension.
        name = name[: MAX_FILENAME_LENGTH - len(MP3_SUFFIX)] + name[-len(MP3_SUFFIX) :]
    return name


def _store_audio(
    session: Session,
    storage: LocalStorage,
    staged: StagedFile,
    info: AudioInfo,
    quality: QualityResult,
) -> Upload | None:
    """Keep the file and its analysis once per unique audio.

    Returns the original upload of the same bytes, or None when this upload is the first, which
    is also the case for known audio whose original upload row is gone.
    """
    is_new = repository.insert_audio_if_new(
        session,
        sha256=staged.sha256,
        storage_key=storage.key_for(staged.sha256_hex),
        info=info,
        quality=quality,
    )
    original = None if is_new else repository.find_original_upload(session, staged.sha256)
    # The last step before the transaction commits, so that a failure in between leaves at most
    # a stray file, never a row that points at a missing file. For known audio this also
    # restores the stored file if it has gone missing.
    storage.commit(staged)
    return original


def _check_duration(
    session: Session, settings: Settings, book_id: str | None, duration_seconds: float
) -> OutlierResult:
    rule = OutlierRule(
        min_seconds=settings.outlier_min_seconds,
        max_seconds=settings.outlier_max_seconds,
        ratio=settings.outlier_ratio,
        min_chapters=settings.outlier_min_chapters,
    )
    if book_id is None:
        return check_duration(duration_seconds, rule)
    # Read before this upload's row exists, so the chapter is not part of its own comparison.
    median, chapter_count = repository.book_duration_stats(session, book_id)
    return check_duration(
        duration_seconds, rule, book_median_seconds=median, book_chapter_count=chapter_count
    )
