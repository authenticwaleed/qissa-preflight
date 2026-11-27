"""The upload flow over HTTP: POST /api/upload, then GET /api/uploads/{id}.

The real app runs against PostgreSQL and a temporary storage folder (see tests/conftest.py).
Expected values are the ones in tests/fixtures/README.md.
"""

import hashlib
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.schemas import UploadOut

CH01 = "ch01_192k_cbr_44k_mono.mp3"

# fixture: (score, deductions as {code: points})
EXPECTED_QUALITY: dict[str, tuple[int, dict[str, int]]] = {
    "ch04_long_192k_cbr_44k_mono.mp3": (10, {}),
    "low_128k_cbr_44k_stereo.mp3": (7, {"LOW_BITRATE": 3}),
    "rate_48k_192k_cbr_mono.mp3": (9, {"SAMPLE_RATE_OFF_SPEC": 1}),
    "poor_64k_cbr_22k_mono.mp3": (1, {"LOW_BITRATE": 7, "SAMPLE_RATE_OFF_SPEC": 4}),
    "vbr_44k_mono.mp3": (3, {"LOW_BITRATE": 5, "NOT_CBR": 2}),
    "tagged_id3v1_192k_cbr_44k_mono.mp3": (10, {}),
    "truncated_192k_cbr_44k_mono.mp3": (5, {"TRUNCATED": 5}),
}

# fixture: (seconds, formatted, bitrate_kbps, bitrate_mode, sample_rate_hz, channels, size_bytes)
# Seconds are rounded to 0.01: poor_64k lasts 4.0751 s, so it reports 4.08.
EXPECTED_PROPERTIES: dict[str, tuple[float, str, int, str, int, int, int]] = {
    "ch04_long_192k_cbr_44k_mono.mp3": (16.04, "00:16", 192, "CBR", 44_100, 1, 385_611),
    "low_128k_cbr_44k_stereo.mp3": (4.05, "00:04", 128, "CBR", 44_100, 2, 65_245),
    "rate_48k_192k_cbr_mono.mp3": (4.03, "00:04", 192, "CBR", 48_000, 1, 97_389),
    "poor_64k_cbr_22k_mono.mp3": (4.08, "00:04", 64, "CBR", 22_050, 1, 32_853),
    "vbr_44k_mono.mp3": (4.05, "00:04", 122, "VBR", 44_100, 1, 62_213),
    "tagged_id3v1_192k_cbr_44k_mono.mp3": (4.05, "00:04", 192, "CBR", 44_100, 1, 97_995),
    "truncated_192k_cbr_44k_mono.mp3": (16.04, "00:16", 192, "CBR", 44_100, 1, 192_805),
}


def post_upload(
    client: TestClient, data: bytes, filename: str, *, book_id: str | None = None
) -> httpx.Response:
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


def deduction_points(quality: dict[str, Any]) -> dict[str, int]:
    """The deductions as {code: points}, after checking each is listed once with a message."""
    deductions = quality["deductions"]
    codes = [deduction["code"] for deduction in deductions]
    assert len(codes) == len(set(codes)), f"a deduction is listed twice: {codes}"
    for deduction in deductions:
        assert set(deduction) == {"code", "points", "message"}
        assert isinstance(deduction["message"], str) and deduction["message"].strip()
    return {deduction["code"]: deduction["points"] for deduction in deductions}


def test_upload_returns_201_with_a_location_and_the_full_analysis(
    client: TestClient, fixture_file: Callable[[str], Path]
) -> None:
    data = fixture_file(CH01).read_bytes()

    response = post_upload(client, data, CH01, book_id="book-1")

    assert response.status_code == 201, response.text
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    UploadOut.model_validate(body)
    assert set(body) == {
        "id",
        "filename",
        "book_id",
        "uploaded_at",
        "file",
        "duration",
        "quality",
        "duplicate",
    }
    assert str(uuid.UUID(body["id"])) == body["id"]
    assert response.headers["location"] == f"/api/uploads/{body['id']}"
    assert body["filename"] == CH01
    assert body["book_id"] == "book-1"
    assert body["file"] == {"size_bytes": 97_846, "sha256": hashlib.sha256(data).hexdigest()}
    assert body["duration"] == {
        "seconds": pytest.approx(4.05),
        "formatted": "00:04",
        "is_outlier": False,
        "outlier_reason": None,
    }
    assert body["quality"] == {
        "score": 10,
        "bitrate_kbps": 192,
        "bitrate_mode": "CBR",
        "sample_rate_hz": 44_100,
        "channels": 1,
        "deductions": [],
    }
    assert body["duplicate"] == {"is_duplicate": False, "original": None}
    uploaded_at = datetime.fromisoformat(body["uploaded_at"])
    assert uploaded_at.tzinfo is not None, "uploaded_at needs a UTC offset"
    assert abs(datetime.now(UTC) - uploaded_at) < timedelta(minutes=10)


def test_the_file_is_stored_once_under_its_sha256_with_no_temporary_files_left(
    client: TestClient, fixture_file: Callable[[str], Path], settings: Settings
) -> None:
    data = fixture_file(CH01).read_bytes()

    upload_ok(client, data, CH01)

    expected = storage_path(settings.storage_dir, hashlib.sha256(data).hexdigest())
    assert stored_files(settings.storage_dir) == [expected]
    assert expected.read_bytes() == data


def test_get_returns_the_same_json_as_the_upload_response(
    client: TestClient, fixture_file: Callable[[str], Path]
) -> None:
    created = post_upload(client, fixture_file(CH01).read_bytes(), CH01, book_id="book-1")
    assert created.status_code == 201, created.text

    fetched = client.get(created.headers["location"])

    assert fetched.status_code == 200, fetched.text
    assert fetched.json() == created.json()


@pytest.mark.parametrize("fixture_name", EXPECTED_QUALITY)
def test_quality_score_and_deductions_follow_the_scoring_table(
    client: TestClient, fixture_file: Callable[[str], Path], fixture_name: str
) -> None:
    score, deductions = EXPECTED_QUALITY[fixture_name]

    body = upload_ok(client, fixture_file(fixture_name).read_bytes(), fixture_name)

    assert body["quality"]["score"] == score
    assert deduction_points(body["quality"]) == deductions


@pytest.mark.parametrize("fixture_name", EXPECTED_PROPERTIES)
def test_duration_and_encoding_are_reported_as_read_from_the_file(
    client: TestClient, fixture_file: Callable[[str], Path], fixture_name: str
) -> None:
    seconds, formatted, kbps, mode, sample_rate, channels, size = EXPECTED_PROPERTIES[fixture_name]

    body = upload_ok(client, fixture_file(fixture_name).read_bytes(), fixture_name)

    assert body["duration"]["seconds"] == pytest.approx(seconds)
    assert body["duration"]["formatted"] == formatted
    quality = body["quality"]
    assert quality["bitrate_kbps"] == kbps
    assert quality["bitrate_mode"] == mode
    assert quality["sample_rate_hz"] == sample_rate
    assert quality["channels"] == channels
    assert body["file"]["size_bytes"] == size


@pytest.mark.parametrize(
    ("sent", "stored"),
    [("  book-7  ", "book-7"), ("   ", None), ("", None), (None, None)],
    ids=["padded", "blank", "empty", "absent"],
)
def test_book_id_is_trimmed_and_blank_means_no_book(
    client: TestClient, fixture_file: Callable[[str], Path], sent: str | None, stored: str | None
) -> None:
    body = upload_ok(client, fixture_file(CH01).read_bytes(), CH01, book_id=sent)

    assert body["book_id"] == stored
    assert client.get(f"/api/uploads/{body['id']}").json()["book_id"] == stored


def test_a_100_character_book_id_is_accepted(
    client: TestClient, fixture_file: Callable[[str], Path]
) -> None:
    book_id = "b" * 100

    body = upload_ok(client, fixture_file(CH01).read_bytes(), CH01, book_id=book_id)

    assert body["book_id"] == book_id


def test_the_client_filename_is_never_used_in_a_storage_path(
    client: TestClient, fixture_file: Callable[[str], Path], settings: Settings
) -> None:
    data = fixture_file(CH01).read_bytes()

    upload_ok(client, data, "../escape.mp3")

    expected = storage_path(settings.storage_dir, hashlib.sha256(data).hexdigest())
    assert stored_files(settings.storage_dir) == [expected]
    assert not (settings.storage_dir.parent / "escape.mp3").exists()
