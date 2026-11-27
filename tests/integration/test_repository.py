"""Repository queries on PostgreSQL: ON CONFLICT, the original lookup, the book median, the
constraints behind them, concurrent inserts, and a migration that matches the models."""

import dataclasses
import hashlib
import queue
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import repository
from app.audio import AudioInfo, BitrateMode
from app.models import AudioFile, Upload
from app.scoring import Deduction, QualityResult
from app.storage import LocalStorage

ROOT = Path(__file__).resolve().parents[2]

CHAPTER = AudioInfo(
    duration_seconds=1500.0,
    bitrate_kbps=192,
    bitrate_mode=BitrateMode.CBR,
    sample_rate_hz=44_100,
    channels=1,
    size_bytes=36_000_000,
    audio_bytes=35_999_955,
)
PERFECT = QualityResult(score=10, deductions=())


def digest(name: str) -> bytes:
    """Stands in for the SHA-256 of an uploaded file's bytes."""
    return hashlib.sha256(name.encode()).digest()


def add_audio(
    session: Session, name: str, *, duration_seconds: float = 1500.0, score: int = 10
) -> bytes:
    sha256 = digest(name)
    inserted = repository.insert_audio_if_new(
        session,
        sha256=sha256,
        storage_key=LocalStorage.key_for(sha256.hex()),
        info=dataclasses.replace(CHAPTER, duration_seconds=duration_seconds),
        quality=QualityResult(score=score, deductions=()),
    )
    assert inserted
    return sha256


def add_upload(
    session: Session,
    sha256: bytes,
    *,
    book_id: str | None = None,
    duplicate_of: Upload | None = None,
) -> Upload:
    return repository.add_upload(
        session,
        sha256=sha256,
        original_filename="chapter.mp3",
        book_id=book_id,
        duplicate_of_id=duplicate_of.id if duplicate_of else None,
        is_duration_outlier=False,
        outlier_reason=None,
    )


def add_chapter(session: Session, name: str, *, book_id: str, duration_seconds: float) -> Upload:
    """New audio and its original upload."""
    return add_upload(
        session, add_audio(session, name, duration_seconds=duration_seconds), book_id=book_id
    )


def test_insert_audio_if_new_is_true_only_the_first_time(db_session: Session) -> None:
    sha256 = digest("chapter one")

    def insert() -> bool:
        return repository.insert_audio_if_new(
            db_session,
            sha256=sha256,
            storage_key=LocalStorage.key_for(sha256.hex()),
            info=CHAPTER,
            quality=PERFECT,
        )

    assert insert() is True
    assert insert() is False
    assert db_session.scalar(select(func.count()).select_from(AudioFile)) == 1


def test_insert_audio_if_new_stores_the_analysis(db_session: Session) -> None:
    sha256 = digest("a poor chapter")
    info = AudioInfo(
        duration_seconds=1441.3,
        bitrate_kbps=122,
        bitrate_mode=BitrateMode.VBR,
        sample_rate_hz=22_050,
        channels=2,
        size_bytes=21_984_000,
        audio_bytes=21_983_000,
    )
    quality = QualityResult(
        score=1,
        deductions=(
            Deduction("LOW_BITRATE", 5, "122 kbps is below the 192 kbps retailers ask for."),
            Deduction("SAMPLE_RATE_OFF_SPEC", 4, "22,050 Hz instead of 44,100 Hz."),
            Deduction("NOT_CBR", 2, "Variable bit rate instead of constant (CBR)."),
        ),
    )

    repository.insert_audio_if_new(
        db_session, sha256=sha256, storage_key="ab/cd/key.mp3", info=info, quality=quality
    )

    row = db_session.get(AudioFile, sha256)
    assert row is not None
    assert row.storage_key == "ab/cd/key.mp3"
    assert row.size_bytes == 21_984_000
    assert row.duration_seconds == 1441.3
    assert row.bitrate_kbps == 122
    assert row.bitrate_mode == "VBR"
    assert row.sample_rate_hz == 22_050
    assert row.channels == 2
    assert row.quality_score == 1
    # Kept in the order scoring listed them (not alphabetical).
    assert row.quality_deductions == [
        {"code": d.code, "points": d.points, "message": d.message} for d in quality.deductions
    ]
    assert row.created_at is not None


def test_add_upload_then_get_upload(db_session: Session) -> None:
    sha256 = add_audio(db_session, "chapter one")

    upload = repository.add_upload(
        db_session,
        sha256=sha256,
        original_filename="Chapter 01.mp3",
        book_id="book-1",
        duplicate_of_id=None,
        is_duration_outlier=True,
        outlier_reason="Shorter than 30 seconds.",
    )
    # Detached, nothing more can be loaded: the id and the server default must already be set.
    db_session.expunge(upload)
    assert isinstance(upload.id, uuid.UUID)
    assert upload.created_at is not None

    found = repository.get_upload(db_session, upload.id)
    assert found is not None
    assert found is not upload  # read back from the database, not the session's cache
    assert found.original_filename == "Chapter 01.mp3"
    assert found.book_id == "book-1"
    assert found.is_duration_outlier is True
    assert found.outlier_reason == "Shorter than 30 seconds."
    assert found.created_at == upload.created_at
    assert found.audio.sha256 == sha256
    assert repository.get_upload(db_session, uuid.uuid4()) is None


def test_find_original_upload_ignores_duplicates(db_session: Session) -> None:
    sha256 = add_audio(db_session, "chapter one")
    original = add_upload(db_session, sha256, book_id="book-1")
    add_upload(db_session, sha256, book_id="book-2", duplicate_of=original)

    found = repository.find_original_upload(db_session, sha256)

    assert found is not None
    assert found.id == original.id
    assert repository.find_original_upload(db_session, digest("never uploaded")) is None


@pytest.mark.parametrize(
    ("durations", "median"),
    [
        ([600.0, 1500.0, 1200.0], 1200.0),
        # percentile_cont interpolates: halfway between the middle two, not one of them.
        ([600.0, 1500.0, 1200.0, 3000.0], 1350.0),
    ],
    ids=["odd count", "even count"],
)
def test_book_duration_stats_gives_the_median(
    db_session: Session, durations: list[float], median: float
) -> None:
    for number, seconds in enumerate(durations):
        add_chapter(db_session, f"chapter {number}", book_id="book-1", duration_seconds=seconds)

    assert repository.book_duration_stats(db_session, "book-1") == (median, len(durations))


def test_book_duration_stats_counts_only_the_books_original_uploads(db_session: Session) -> None:
    for number, seconds in enumerate([600.0, 1200.0, 1500.0]):
        add_chapter(db_session, f"chapter {number}", book_id="book-1", duration_seconds=seconds)
    other = add_chapter(db_session, "another book", book_id="book-2", duration_seconds=9000.0)
    # The other book's chapter uploaded again into book-1: a duplicate, not a book-1 chapter.
    add_upload(db_session, other.sha256, book_id="book-1", duplicate_of=other)

    assert repository.book_duration_stats(db_session, "book-1") == (1200.0, 3)
    assert repository.book_duration_stats(db_session, "book-2") == (9000.0, 1)


def test_book_duration_stats_for_an_unknown_book(db_session: Session) -> None:
    assert repository.book_duration_stats(db_session, "no-such-book") == (None, 0)


@pytest.mark.parametrize("score", [0, 11])
def test_quality_score_must_be_between_1_and_10(db_session: Session, score: int) -> None:
    with pytest.raises(IntegrityError) as caught:
        add_audio(db_session, "chapter one", score=score)

    assert caught.value.orig.diag.constraint_name == "ck_audio_files_quality_score_range"


def test_only_one_original_upload_per_audio(db_session: Session) -> None:
    sha256 = add_audio(db_session, "chapter one")
    original = add_upload(db_session, sha256)
    add_upload(db_session, sha256, duplicate_of=original)  # duplicates are fine

    with pytest.raises(IntegrityError) as caught:
        add_upload(db_session, sha256)  # a second original is not

    assert caught.value.orig.diag.constraint_name == "uq_uploads_original_sha256"


def wait_until(condition: Callable[[], bool], timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def blocking_pids(engine: Engine, pid: int) -> list[int]:
    """The server processes whose locks the session with this pid is waiting for."""
    with engine.connect() as conn:
        return conn.scalar(text("SELECT pg_blocking_pids(:pid)"), {"pid": pid})


def test_concurrent_inserts_of_the_same_new_audio_make_one_row(db_engine: Engine) -> None:
    """Two uploads of one new file at once: the second insert waits for the first transaction
    and, once that commits, finds the audio already known."""
    sha256 = digest("uploaded twice at the same moment")

    def insert(session: Session) -> bool:
        return repository.insert_audio_if_new(
            session,
            sha256=sha256,
            storage_key=LocalStorage.key_for(sha256.hex()),
            info=CHAPTER,
            quality=PERFECT,
        )

    second_pid: queue.Queue[int] = queue.Queue()
    outcome: dict[str, object] = {}

    def second_upload() -> None:
        try:
            with Session(db_engine) as session:
                # A wait that never ends fails the test instead of hanging it.
                session.execute(text("SET LOCAL lock_timeout = '10s'"))
                second_pid.put(session.scalar(text("SELECT pg_backend_pid()")))
                outcome["inserted"] = insert(session)
                session.commit()
        except Exception as exc:
            outcome["error"] = exc

    second = threading.Thread(target=second_upload, daemon=True)
    first = Session(db_engine)
    try:
        first_pid = first.scalar(text("SELECT pg_backend_pid()"))
        assert insert(first) is True  # inserted but not committed yet
        second.start()
        pid = second_pid.get(timeout=10)
        assert wait_until(lambda: first_pid in blocking_pids(db_engine, pid)), (
            "the second insert did not wait for the first transaction"
        )
        assert second.is_alive()
        first.commit()
    finally:
        first.close()  # also releases the second insert when an assertion above failed
        second.join(timeout=10)

    assert not second.is_alive()
    assert outcome == {"inserted": False}
    with Session(db_engine) as session:
        rows = select(func.count()).select_from(AudioFile).where(AudioFile.sha256 == sha256)
        assert session.scalar(rows) == 1


def test_migrations_match_the_models(database_url: str) -> None:
    """`alembic check` on the migrated test database finds nothing to change: the tables,
    columns, types, nullability, server defaults, indexes and foreign keys match app/models.py."""
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

    command.check(config)  # raises AutogenerateDiffsDetected on any difference
