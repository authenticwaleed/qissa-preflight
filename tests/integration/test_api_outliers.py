"""Duration outliers over HTTP.

Every upload is checked against fixed limits (shorter than the minimum or longer than the
maximum). With a book_id and at least three earlier original chapters of that book, a chapter is
also an outlier when it is more than 3 times the book's median length, or less than a third of it.
The settings fixture lowers the fixed minimum to 1 second so the 4-second fixtures pass it.
"""

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

CH01 = "ch01_192k_cbr_44k_mono.mp3"
CH02 = "ch02_192k_cbr_44k_mono.mp3"
CH03 = "ch03_192k_cbr_44k_mono.mp3"
# 16 seconds: 3.96 times the 4-second chapters above.
CH04_LONG = "ch04_long_192k_cbr_44k_mono.mp3"
BOOK = "book-1"


def upload_ok(
    client: TestClient, data: bytes, filename: str, *, book_id: str | None = None
) -> dict[str, Any]:
    form = None if book_id is None else {"book_id": book_id}
    response = client.post("/api/upload", files={"file": (filename, data, "audio/mpeg")}, data=form)
    assert response.status_code == 201, response.text
    return response.json()


def upload_fixtures(
    client: TestClient,
    fixture_file: Callable[[str], Path],
    names: list[str],
    *,
    book_id: str | None = None,
) -> list[dict[str, Any]]:
    return [
        upload_ok(client, fixture_file(name).read_bytes(), name, book_id=book_id) for name in names
    ]


def with_id3v1_tag(data: bytes, title: str) -> bytes:
    """The same audio with a 128-byte ID3v1 tag at the end: new bytes, the same duration."""
    tag = b"TAG" + title.encode("ascii").ljust(30, b"\0") + bytes(94) + b"\xff"
    assert len(tag) == 128
    return data + tag


def assert_flagged(body: dict[str, Any]) -> None:
    duration = body["duration"]
    assert duration["is_outlier"] is True, duration
    reason = duration["outlier_reason"]
    assert isinstance(reason, str) and reason.strip(), "a flagged upload needs a reason"
    assert len(reason) <= 200, "the reason is stored in a 200-character column"
    assert not re.search(r"\d\.\d{4,}", reason), f"the reason should read well: {reason!r}"


def assert_not_flagged(body: dict[str, Any]) -> None:
    assert body["duration"]["is_outlier"] is False, body["duration"]
    assert body["duration"]["outlier_reason"] is None


def test_a_chapter_over_three_times_the_book_median_is_flagged(
    client: TestClient, fixture_file: Callable[[str], Path]
) -> None:
    chapters = upload_fixtures(client, fixture_file, [CH01, CH02, CH03], book_id=BOOK)

    long_chapter = upload_ok(client, fixture_file(CH04_LONG).read_bytes(), CH04_LONG, book_id=BOOK)

    for chapter in chapters:
        assert_not_flagged(chapter)
    assert_flagged(long_chapter)
    fetched = client.get(f"/api/uploads/{long_chapter['id']}").json()
    assert fetched["duration"] == long_chapter["duration"]


def test_a_chapter_under_a_third_of_the_book_median_is_flagged(
    client: TestClient, fixture_file: Callable[[str], Path]
) -> None:
    long_audio = fixture_file(CH04_LONG).read_bytes()
    chapters = [
        upload_ok(client, long_audio, "chapter-01.mp3", book_id=BOOK),
        upload_ok(client, with_id3v1_tag(long_audio, "Chapter 2"), "chapter-02.mp3", book_id=BOOK),
        upload_ok(client, with_id3v1_tag(long_audio, "Chapter 3"), "chapter-03.mp3", book_id=BOOK),
    ]

    short_chapter = upload_ok(
        client, fixture_file(CH01).read_bytes(), "chapter-04.mp3", book_id=BOOK
    )

    for chapter in chapters:
        assert chapter["duplicate"]["is_duplicate"] is False
        assert_not_flagged(chapter)
    assert_flagged(short_chapter)


def test_without_a_book_id_the_book_rule_does_not_apply(
    client: TestClient, fixture_file: Callable[[str], Path]
) -> None:
    upload_fixtures(client, fixture_file, [CH01, CH02, CH03])

    long_chapter = upload_ok(client, fixture_file(CH04_LONG).read_bytes(), CH04_LONG)

    assert_not_flagged(long_chapter)


def test_chapters_of_another_book_do_not_count(
    client: TestClient, fixture_file: Callable[[str], Path]
) -> None:
    upload_fixtures(client, fixture_file, [CH01, CH02, CH03], book_id="book-1")

    long_chapter = upload_ok(
        client, fixture_file(CH04_LONG).read_bytes(), CH04_LONG, book_id="book-2"
    )

    assert_not_flagged(long_chapter)


def test_the_book_rule_needs_three_earlier_chapters(
    client: TestClient, fixture_file: Callable[[str], Path]
) -> None:
    upload_fixtures(client, fixture_file, [CH01, CH02], book_id=BOOK)

    long_chapter = upload_ok(client, fixture_file(CH04_LONG).read_bytes(), CH04_LONG, book_id=BOOK)

    assert_not_flagged(long_chapter)


def test_duplicate_uploads_do_not_count_as_chapters(
    client: TestClient, fixture_file: Callable[[str], Path]
) -> None:
    upload_fixtures(client, fixture_file, [CH01, CH02], book_id=BOOK)
    copy = upload_ok(client, fixture_file(CH01).read_bytes(), "ch01-again.mp3", book_id=BOOK)
    assert copy["duplicate"]["is_duplicate"] is True

    long_chapter = upload_ok(client, fixture_file(CH04_LONG).read_bytes(), CH04_LONG, book_id=BOOK)

    assert_not_flagged(long_chapter)


def test_the_flag_is_decided_at_upload_time_and_kept(
    client: TestClient, fixture_file: Callable[[str], Path]
) -> None:
    # The book's first chapter has nothing to compare with yet.
    long_chapter = upload_ok(client, fixture_file(CH04_LONG).read_bytes(), CH04_LONG, book_id=BOOK)

    upload_fixtures(client, fixture_file, [CH01, CH02, CH03], book_id=BOOK)

    assert_not_flagged(long_chapter)
    assert_not_flagged(client.get(f"/api/uploads/{long_chapter['id']}").json())


def test_a_file_shorter_than_the_minimum_length_is_flagged(
    make_client: Callable[..., TestClient], fixture_file: Callable[[str], Path]
) -> None:
    client = make_client(outlier_min_seconds=5)

    short = upload_ok(client, fixture_file(CH01).read_bytes(), CH01)
    long_enough = upload_ok(client, fixture_file(CH04_LONG).read_bytes(), CH04_LONG)

    assert_flagged(short)
    assert_not_flagged(long_enough)


def test_a_file_longer_than_the_maximum_length_is_flagged(
    make_client: Callable[..., TestClient], fixture_file: Callable[[str], Path]
) -> None:
    client = make_client(outlier_max_seconds=10)

    too_long = upload_ok(client, fixture_file(CH04_LONG).read_bytes(), CH04_LONG)
    short_enough = upload_ok(client, fixture_file(CH01).read_bytes(), CH01)

    assert_flagged(too_long)
    assert_not_flagged(short_enough)
