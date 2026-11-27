"""The upload use case: store the file, analyse it, detect duplicates, flag outliers, save."""

from typing import BinaryIO

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Upload
from app.storage import LocalStorage


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
    4. repository.insert_audio_if_new(...): new audio -> storage.commit(staged);
       known audio -> storage.discard(staged) and repository.find_original_upload(sha256).
    5. With a book_id: repository.book_duration_stats(book_id), read before this upload is
       inserted; then check_duration(...) with the rule built from settings.
    6. repository.add_upload(...), then session.commit().
    On any error: discard the staged file, roll back, re-raise.
    """
    raise NotImplementedError
