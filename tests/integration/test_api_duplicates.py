"""Duplicate detection over HTTP: exact matches on the file's bytes, whatever its name or book.

A duplicate is still accepted (201) and gets its own upload, but it points at the first upload of
the same bytes, and those bytes are stored only once.
"""

import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx2
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select

from app.config import Settings
from app.models import AudioFile, Upload

CH01 = "ch01_192k_cbr_44k_mono.mp3"
CH02 = "ch02_192k_cbr_44k_mono.mp3"
# The same audio as ch01 with different ID3 tags, so different bytes.
RETAGGED_CH01 = "tagged_id3v1_192k_cbr_44k_mono.mp3"
CONCURRENT_UPLOADS = 8


def post_upload(
    client: TestClient, data: bytes, filename: str, *, book_id: str | None = None
) -> httpx2.Response:
    form = None if book_id is None else {"book_id": book_id}
    return client.post("/api/upload", files={"file": (filename, data, "audio/mpeg")}, data=form)


def upload_ok(
    client: TestClient, data: bytes, filename: str, *, book_id: str | None = None
) -> dict[str, Any]:
    response = post_upload(client, data, filename, book_id=book_id)
    assert response.status_code == 201, response.text
    return response.json()


def stored_files(storage_dir: Path) -> list[Path]:
    """Every file under the storage folder, temporary files included."""
    if not storage_dir.exists():
        return []
    return sorted(path for path in storage_dir.rglob("*") if path.is_file())


def storage_path(storage_dir: Path, sha256_hex: str) -> Path:
    return storage_dir / sha256_hex[:2] / sha256_hex[2:4] / f"{sha256_hex}.mp3"


def count(engine: Engine, model: type[Upload] | type[AudioFile], *where: Any) -> int:
    with engine.connect() as conn:
        return conn.scalar(select(func.count()).select_from(model).where(*where)) or 0


def test_same_bytes_under_another_filename_are_a_duplicate_of_the_first_upload(
    client: TestClient, fixture_file: Callable[[str], Path], settings: Settings
) -> None:
    data = fixture_file(CH01).read_bytes()
    first = upload_ok(client, data, "chapter-01.mp3", book_id="book-1")

    second = upload_ok(client, data, "chapter-01 (final).mp3", book_id="book-1")

    assert second["id"] != first["id"]
    assert second["filename"] == "chapter-01 (final).mp3"
    assert second["duplicate"]["is_duplicate"] is True
    original = second["duplicate"]["original"]
    assert set(original) == {"id", "filename", "book_id", "uploaded_at", "url"}
    assert original["id"] == first["id"]
    assert original["filename"] == "chapter-01.mp3"
    assert original["book_id"] == "book-1"
    assert datetime.fromisoformat(original["uploaded_at"]) == datetime.fromisoformat(
        first["uploaded_at"]
    )
    assert original["url"] == f"/api/uploads/{first['id']}"
    # The same audio, so the same analysis.
    assert second["file"] == first["file"]
    assert second["quality"] == first["quality"]
    # The first upload is still the original, and the url leads to it.
    assert client.get(original["url"]).json() == first
    # The duplicate can be fetched as an upload of its own.
    assert client.get(f"/api/uploads/{second['id']}").json() == second
    # The bytes are on disk once.
    assert stored_files(settings.storage_dir) == [
        storage_path(settings.storage_dir, first["file"]["sha256"])
    ]


def test_a_duplicate_gets_its_own_upload_row_and_shares_the_stored_audio(
    client: TestClient, fixture_file: Callable[[str], Path], db_engine: Engine
) -> None:
    data = fixture_file(CH01).read_bytes()
    first = upload_ok(client, data, "a.mp3")
    second = upload_ok(client, data, "b.mp3")

    with db_engine.connect() as conn:
        uploads = dict(conn.execute(select(Upload.id, Upload.duplicate_of_id)).all())

    assert uploads == {
        uuid.UUID(first["id"]): None,
        uuid.UUID(second["id"]): uuid.UUID(first["id"]),
    }
    assert count(db_engine, AudioFile) == 1


def test_every_later_copy_points_at_the_first_upload_not_the_one_before_it(
    client: TestClient, fixture_file: Callable[[str], Path]
) -> None:
    data = fixture_file(CH01).read_bytes()
    first = upload_ok(client, data, "take-1.mp3")
    upload_ok(client, data, "take-2.mp3")

    third = upload_ok(client, data, "take-3.mp3")

    assert third["duplicate"]["is_duplicate"] is True
    assert third["duplicate"]["original"]["id"] == first["id"]
    assert third["duplicate"]["original"]["filename"] == "take-1.mp3"


def test_duplicates_are_detected_across_books(
    client: TestClient, fixture_file: Callable[[str], Path]
) -> None:
    data = fixture_file(CH01).read_bytes()
    first = upload_ok(client, data, CH01, book_id="book-1")

    other_book = upload_ok(client, data, CH01, book_id="book-2")
    no_book = upload_ok(client, data, CH01)

    for later in (other_book, no_book):
        assert later["duplicate"]["is_duplicate"] is True
        assert later["duplicate"]["original"]["id"] == first["id"]
        assert later["duplicate"]["original"]["book_id"] == "book-1"
    assert other_book["book_id"] == "book-2"
    assert no_book["book_id"] is None


def test_a_different_file_under_the_same_filename_is_not_a_duplicate(
    client: TestClient, fixture_file: Callable[[str], Path], settings: Settings
) -> None:
    upload_ok(client, fixture_file(CH01).read_bytes(), "chapter.mp3")

    other = upload_ok(client, fixture_file(CH02).read_bytes(), "chapter.mp3")

    assert other["duplicate"] == {"is_duplicate": False, "original": None}
    assert len(stored_files(settings.storage_dir)) == 2


def test_a_retagged_copy_of_the_same_audio_is_not_an_exact_duplicate(
    client: TestClient, fixture_file: Callable[[str], Path], settings: Settings
) -> None:
    """A known limit: matching is on the exact bytes, so a copy with edited tags is a new file."""
    first = upload_ok(client, fixture_file(CH01).read_bytes(), CH01)

    retagged = upload_ok(client, fixture_file(RETAGGED_CH01).read_bytes(), RETAGGED_CH01)

    assert retagged["duplicate"] == {"is_duplicate": False, "original": None}
    assert retagged["file"]["sha256"] != first["file"]["sha256"]
    assert retagged["duration"]["seconds"] == first["duration"]["seconds"]
    assert len(stored_files(settings.storage_dir)) == 2


def test_uploading_the_same_bytes_again_restores_a_lost_file(
    client: TestClient, fixture_file: Callable[[str], Path], settings: Settings
) -> None:
    data = fixture_file(CH01).read_bytes()
    first = upload_ok(client, data, "chapter-01.mp3")
    stored = storage_path(settings.storage_dir, first["file"]["sha256"])
    stored.unlink()  # the disk lost it

    again = upload_ok(client, data, "chapter-01.mp3")

    assert again["duplicate"]["original"]["id"] == first["id"]
    assert stored.read_bytes() == data
    assert stored_files(settings.storage_dir) == [stored]


def test_concurrent_uploads_of_the_same_new_file_make_exactly_one_original(
    client: TestClient,
    fixture_file: Callable[[str], Path],
    settings: Settings,
    db_engine: Engine,
) -> None:
    data = fixture_file(CH01).read_bytes()
    start = threading.Barrier(CONCURRENT_UPLOADS, timeout=30)

    def upload(index: int) -> httpx2.Response:
        start.wait()  # release every upload at the same moment
        return post_upload(client, data, f"copy-{index}.mp3")

    with ThreadPoolExecutor(max_workers=CONCURRENT_UPLOADS) as pool:
        responses = list(pool.map(upload, range(CONCURRENT_UPLOADS)))

    statuses = [response.status_code for response in responses]
    assert statuses == [201] * CONCURRENT_UPLOADS, [response.text for response in responses]
    bodies = [r.json() for r in responses]
    originals = [body for body in bodies if not body["duplicate"]["is_duplicate"]]
    assert len(originals) == 1, "exactly one of the uploads must be the original"
    original = originals[0]
    assert original["duplicate"]["original"] is None
    for body in bodies:
        if body is not original:
            assert body["duplicate"]["original"]["id"] == original["id"]
            assert body["duplicate"]["original"]["filename"] == original["filename"]
    assert len({body["id"] for body in bodies}) == CONCURRENT_UPLOADS
    assert stored_files(settings.storage_dir) == [
        storage_path(settings.storage_dir, original["file"]["sha256"])
    ]
    assert count(db_engine, AudioFile) == 1
    assert count(db_engine, Upload) == CONCURRENT_UPLOADS
    assert count(db_engine, Upload, Upload.duplicate_of_id.is_(None)) == 1
