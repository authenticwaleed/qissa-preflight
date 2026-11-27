"""check_duration: the fixed limits, the book rule, and which of them wins."""

import pytest

from app.outliers import OutlierResult, OutlierRule, check_duration

RULE = OutlierRule()  # the defaults: 30 s, 2 hours, 3× the median, 3 earlier chapters
MEDIAN = 600.0  # a book of ten-minute chapters: its rule allows 200 s to 1,800 s

NOT_AN_OUTLIER = OutlierResult(is_outlier=False, reason=None)
TOO_SHORT = OutlierResult(True, "Shorter than 30 s; probably an empty or broken export.")
TOO_LONG = OutlierResult(True, "Longer than 2:00:00, the per-file limit.")


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0.5, TOO_SHORT),
        (29.99, TOO_SHORT),
        (30.0, NOT_AN_OUTLIER),  # exactly at a limit is not an outlier
        (1441.3, NOT_AN_OUTLIER),
        (7200.0, NOT_AN_OUTLIER),
        (7200.01, TOO_LONG),
    ],
)
def test_fixed_limits_apply_without_book_history(seconds: float, expected: OutlierResult) -> None:
    assert check_duration(seconds, RULE) == expected


@pytest.mark.parametrize(
    ("seconds", "is_outlier"),
    [
        (199.99, True),
        (200.0, False),
        (MEDIAN, False),
        (1800.0, False),
        (1800.01, True),
    ],
)
def test_book_rule_flags_chapters_beyond_3x_the_median_either_way(
    seconds: float, is_outlier: bool
) -> None:
    result = check_duration(seconds, RULE, book_median_seconds=MEDIAN, book_chapter_count=3)

    assert result.is_outlier is is_outlier


def test_book_reasons_give_the_multiple_and_the_median() -> None:
    # ch04 of the fixtures against ch01–ch03, with the 1-second minimum the upload tests use.
    long = check_duration(
        16.039, OutlierRule(min_seconds=1.0), book_median_seconds=4.049, book_chapter_count=3
    )
    short = check_duration(300.0, RULE, book_median_seconds=1441.0, book_chapter_count=3)

    assert long == OutlierResult(True, "4.0× the median chapter of this book (00:04).")
    assert short == OutlierResult(True, "0.2× the median chapter of this book (24:01).")


def test_book_rule_waits_for_min_chapters() -> None:
    result = check_duration(5000.0, RULE, book_median_seconds=MEDIAN, book_chapter_count=2)

    assert result == NOT_AN_OUTLIER


def test_book_rule_needs_a_median() -> None:
    result = check_duration(5000.0, RULE, book_median_seconds=None, book_chapter_count=10)

    assert result == NOT_AN_OUTLIER


@pytest.mark.parametrize(("seconds", "expected"), [(10.0, TOO_SHORT), (7300.0, TOO_LONG)])
def test_fixed_limit_wins_over_the_book_rule(seconds: float, expected: OutlierResult) -> None:
    # Both durations break the book rule as well.
    result = check_duration(seconds, RULE, book_median_seconds=MEDIAN, book_chapter_count=5)

    assert result == expected


def test_reasons_use_the_configured_numbers() -> None:
    rule = OutlierRule(min_seconds=45.0, max_seconds=5400.0, ratio=2.0, min_chapters=5)

    assert check_duration(44.0, rule).reason == (
        "Shorter than 45 s; probably an empty or broken export."
    )
    assert check_duration(5401.0, rule).reason == "Longer than 1:30:00, the per-file limit."
    assert check_duration(
        1500.0, rule, book_median_seconds=MEDIAN, book_chapter_count=5
    ) == OutlierResult(True, "2.5× the median chapter of this book (10:00).")
    assert check_duration(1200.0, rule, book_median_seconds=MEDIAN, book_chapter_count=5) == (
        NOT_AN_OUTLIER
    )
    assert check_duration(1500.0, rule, book_median_seconds=MEDIAN, book_chapter_count=4) == (
        NOT_AN_OUTLIER
    )
