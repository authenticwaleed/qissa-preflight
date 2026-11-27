"""Rejected uploads and failed lookups over HTTP.

Every error body is {"error": {"code": ..., "message": ...}}, and a rejected upload leaves nothing
behind: no file in the storage folder (temporary files included) and no database row.
"""

import io
import os
import uuid
import wave
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select

from app.config import Settings
from app.models import AudioFile, Upload
from app.schemas import ErrorOut

CH01 = "ch01_192k_cbr_44k_mono.mp3"
CH04_LONG = "ch04_long_192k_cbr_44k_mono.mp3"
NOT_AUDIO = "not_audio.mp3"
ONE_MB = 1_000_000  # MAX_UPLOAD_MB counts decimal megabytes
RUNNING_AS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


def post_upload(
    client: TestClient,
    data: bytes,
    filename: str,
    *,
    book_id: str | None = None,
    content_type: str = "audio/mpeg",
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    form = None if book_id is None else {"book_id": book_id}
    return client.post(
        "/api/upload",
        files={"file": (filename, data, content_type)},
        data=form,
        headers=headers,
    )


def assert_error(response: httpx.Response, status: int, code: str | None = None) -> None:
    """The response is an error with this status, the standard body and (if given) this code."""
    assert response.status_code == status, response.text
    body = response.json()
    ErrorOut.model_validate(body)
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}
    assert body["error"]["code"].strip()
    assert body["error"]["message"].strip()
    if code is not None:
        assert body["error"]["code"] == code


def stored_files(storage_dir: Path) -> list[Path]:
    """Every file under the storage folder, temporary files included."""
    if not storage_dir.exists():
        return []
    return sorted(path for path in storage_dir.rglob("*") if path.is_file())


def storage_path(storage_dir: Path, sha256_hex: str) -> Path:
    return storage_dir / sha256_hex[:2] / sha256_hex[2:4] / f"{sha256_hex}.mp3"


def row_counts(engine: Engine) -> tuple[int, int]:
    """How many rows the uploads and audio_files tables hold, in that order."""
    with engine.connect() as conn:
        uploads = conn.scalar(select(func.count()).select_from(Upload))
        audio_files = conn.scalar(select(func.count()).select_from(AudioFile))
    return uploads or 0, audio_files or 0


def assert_nothing_stored(settings: Settings, engine: Engine) -> None:
    assert stored_files(settings.storage_dir) == []
    assert row_counts(engine) == (0, 0)


def padded(data: bytes, size: int) -> bytes:
    """A valid MP3 grown to exactly `size` bytes with trailing zeros, so only its size is wrong."""
    assert size >= len(data)
    return data + bytes(size - len(data))


def wav_bytes(seconds: float = 0.5, sample_rate: int = 44_100) -> bytes:
    """Silence as a 16-bit mono WAV file."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(bytes(2 * int(seconds * sample_rate)))
    return buffer.getvalue()


@contextmanager
def read_only(folder: Path) -> Iterator[None]:
    """Make a folder and every folder in it read-only for the duration of the block."""
    folder.mkdir(parents=True, exist_ok=True)
    folders = [folder, *(path for path in folder.rglob("*") if path.is_dir())]
    for path in folders:
        path.chmod(0o555)
    try:
        yield
    finally:
        for path in folders:
            path.chmod(0o755)


@pytest.mark.parametrize(
    "filename", ["chapter-01.wav", "chapter-01", "chapter-01.mp3.wav", "chapter-01mp3"]
)
def test_a_filename_that_does_not_end_in_mp3_is_rejected(
    client: TestClient,
    fixture_file: Callable[[str], Path],
    settings: Settings,
    db_engine: Engine,
    filename: str,
) -> None:
    # Valid MP3 bytes with an MP3 content type: only the name is wrong.
    response = post_upload(client, fixture_file(CH01).read_bytes(), filename)

    assert_error(response, 415, "UNSUPPORTED_FILE_TYPE")
    assert_nothing_stored(settings, db_engine)


def test_an_uppercase_mp3_extension_is_accepted(
    client: TestClient, fixture_file: Callable[[str], Path]
) -> None:
    response = post_upload(client, fixture_file(CH01).read_bytes(), "CHAPTER-01.MP3")

    assert response.status_code == 201, response.text
    assert response.json()["filename"] == "CHAPTER-01.MP3"


@pytest.mark.parametrize("content_type", ["application/octet-stream", "text/plain"])
def test_the_content_type_sent_by_the_client_is_ignored(
    client: TestClient, fixture_file: Callable[[str], Path], content_type: str
) -> None:
    response = post_upload(client, fixture_file(CH01).read_bytes(), CH01, content_type=content_type)

    assert response.status_code == 201, response.text


def test_bytes_that_are_not_mp3_audio_are_rejected(
    client: TestClient,
    fixture_file: Callable[[str], Path],
    settings: Settings,
    db_engine: Engine,
) -> None:
    response = post_upload(client, fixture_file(NOT_AUDIO).read_bytes(), NOT_AUDIO)

    assert_error(response, 415, "INVALID_MP3")
    assert_nothing_stored(settings, db_engine)


def test_a_wav_file_renamed_to_mp3_is_rejected(
    client: TestClient, settings: Settings, db_engine: Engine
) -> None:
    response = post_upload(client, wav_bytes(), "chapter-01.mp3")

    assert_error(response, 415, "INVALID_MP3")
    assert_nothing_stored(settings, db_engine)


def test_an_empty_file_is_rejected(
    client: TestClient, settings: Settings, db_engine: Engine
) -> None:
    response = post_upload(client, b"", "chapter-01.mp3")

    assert_error(response, 422, "EMPTY_FILE")
    assert_nothing_stored(settings, db_engine)


def test_a_file_sent_under_another_field_name_is_rejected(
    client: TestClient,
    fixture_file: Callable[[str], Path],
    settings: Settings,
    db_engine: Engine,
) -> None:
    data = fixture_file(CH01).read_bytes()

    response = client.post("/api/upload", files={"audio": (CH01, data, "audio/mpeg")})

    assert_error(response, 422, "VALIDATION_ERROR")
    assert_nothing_stored(settings, db_engine)


def test_a_form_with_only_a_book_id_is_rejected(
    client: TestClient, settings: Settings, db_engine: Engine
) -> None:
    # (None, value) sends a plain multipart field, so the form has no file part at all.
    response = client.post("/api/upload", files={"book_id": (None, "book-1")})

    assert_error(response, 422, "VALIDATION_ERROR")
    assert_nothing_stored(settings, db_engine)


def test_a_book_id_longer_than_100_characters_is_rejected(
    client: TestClient,
    fixture_file: Callable[[str], Path],
    settings: Settings,
    db_engine: Engine,
) -> None:
    response = post_upload(client, fixture_file(CH01).read_bytes(), CH01, book_id="b" * 101)

    assert_error(response, 422, "VALIDATION_ERROR")
    assert_nothing_stored(settings, db_engine)


def test_a_file_over_the_size_limit_is_rejected(
    make_client: Callable[..., TestClient],
    fixture_file: Callable[[str], Path],
    settings: Settings,
    db_engine: Engine,
) -> None:
    client = make_client(max_upload_mb=1)
    data = padded(fixture_file(CH04_LONG).read_bytes(), 1_200_000)

    response = post_upload(client, data, "chapter-01.mp3")

    assert_error(response, 413, "FILE_TOO_LARGE")
    assert_nothing_stored(settings, db_engine)


def test_a_file_one_byte_over_the_size_limit_is_rejected(
    make_client: Callable[..., TestClient],
    fixture_file: Callable[[str], Path],
    settings: Settings,
    db_engine: Engine,
) -> None:
    """The request fits in the multipart allowance of the Content-Length check, so the limit
    must also hold while the file is streamed."""
    client = make_client(max_upload_mb=1)
    data = padded(fixture_file(CH04_LONG).read_bytes(), ONE_MB + 1)

    response = post_upload(client, data, "chapter-01.mp3")

    assert_error(response, 413, "FILE_TOO_LARGE")
    assert_nothing_stored(settings, db_engine)


def test_a_file_exactly_at_the_size_limit_is_accepted(
    make_client: Callable[..., TestClient], fixture_file: Callable[[str], Path]
) -> None:
    client = make_client(max_upload_mb=1)
    data = padded(fixture_file(CH04_LONG).read_bytes(), ONE_MB)

    response = post_upload(client, data, "chapter-01.mp3")

    assert response.status_code == 201, response.text
    assert response.json()["file"]["size_bytes"] == ONE_MB


def test_a_content_length_over_the_limit_is_rejected_before_the_body_is_read(
    make_client: Callable[..., TestClient],
    fixture_file: Callable[[str], Path],
    settings: Settings,
    db_engine: Engine,
) -> None:
    """The header alone decides: a small body that announces 2 MB is refused."""
    client = make_client(max_upload_mb=1)

    response = post_upload(
        client,
        fixture_file(CH01).read_bytes(),
        CH01,
        headers={"Content-Length": str(2 * ONE_MB)},
    )

    assert_error(response, 413, "FILE_TOO_LARGE")
    assert_nothing_stored(settings, db_engine)


def test_an_unknown_upload_id_is_not_found(client: TestClient) -> None:
    response = client.get(f"/api/uploads/{uuid.uuid4()}")

    assert_error(response, 404, "NOT_FOUND")


def test_a_malformed_upload_id_is_a_validation_error(client: TestClient) -> None:
    response = client.get("/api/uploads/not-a-uuid")

    assert_error(response, 422, "VALIDATION_ERROR")


@pytest.mark.parametrize(
    ("path", "status"),
    [("/api/nothing-here", 404), ("/api/upload", 405)],
    ids=["unknown route", "wrong method"],
)
def test_routing_errors_use_the_same_error_body(client: TestClient, path: str, status: int) -> None:
    assert_error(client.get(path), status)


def test_a_very_long_filename_does_not_cause_a_server_error(
    client: TestClient, fixture_file: Callable[[str], Path]
) -> None:
    """Filenames are kept in a 255-character column: a longer one is shortened or refused."""
    response = post_upload(client, fixture_file(CH01).read_bytes(), "a" * 300 + ".mp3")

    if response.status_code == 422:
        assert_error(response, 422, "VALIDATION_ERROR")
    else:
        assert response.status_code == 201, response.text
        assert len(response.json()["filename"]) <= 255


@pytest.mark.skipif(RUNNING_AS_ROOT, reason="root can write to read-only folders")
def test_a_storage_failure_is_a_500_and_the_upload_can_be_retried(
    settings: Settings, db_engine: Engine, fixture_file: Callable[[str], Path]
) -> None:
    from app.main import create_app

    data = fixture_file(CH01).read_bytes()
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        with read_only(settings.storage_dir):
            failed = post_upload(client, data, CH01)
        retried = post_upload(client, data, CH01)

    assert_error(failed, 500, "INTERNAL_ERROR")
    assert "Traceback" not in failed.text
    assert str(settings.storage_dir.parent) not in failed.text, "server paths must not leak"
    # The failed attempt left nothing half-stored, so the retry is a fresh original.
    assert retried.status_code == 201, retried.text
    body = retried.json()
    assert body["duplicate"] == {"is_duplicate": False, "original": None}
    assert stored_files(settings.storage_dir) == [
        storage_path(settings.storage_dir, body["file"]["sha256"])
    ]
    assert row_counts(db_engine) == (1, 1)
