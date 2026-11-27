"""check_duration: the fixed limits, the book rule, and which of them wins."""

from dataclasses import replace

import pytest

from app.outliers import OutlierResult, OutlierRule, check_duration

# The production values, the OUTLIER_* defaults in app/config.py: 30 s, 2 hours, 3× the median,
# 3 earlier chapters.
RULE = OutlierRule(min_seconds=30.0, max_seconds=7200.0, ratio=3.0, min_chapters=3)
MEDIAN = 600.0  # a book of ten-minute chapters: its rule allows 200 s to 1,800 s

NOT_AN_OUTLIER = OutlierResult(is_outlier=False, reason=None)
TOO_SHORT = OutlierResult(
    True, "Shorter than 00:30, the minimum length; probably an empty or broken export."
)
TOO_LONG = OutlierResult(
    True, "Longer than 2:00:00, the maximum length; split it into shorter files."
)


def in_book(
    seconds: float, median: float, rule: OutlierRule = RULE, chapters: int = 3
) -> OutlierResult:
    """check_duration for a chapter of a book that has ``chapters`` earlier chapters."""
    return check_duration(seconds, rule, book_median_seconds=median, book_chapter_count=chapters)


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
    ("seconds", "median", "is_outlier"),
    [
        (199.99, MEDIAN, True),
        (200.0, MEDIAN, False),
        (MEDIAN, MEDIAN, False),
        (1800.0, MEDIAN, False),
        (1800.01, MEDIAN, True),
        # Exactly 3× and exactly a third, where float arithmetic lands just past the limit.
        (1800.9, 600.3, False),
        (200.2, 600.6, False),
    ],
)
def test_book_rule_flags_chapters_beyond_3x_the_median_either_way(
    seconds: float, median: float, is_outlier: bool
) -> None:
    assert in_book(seconds, median).is_outlier is is_outlier


@pytest.mark.parametrize(
    ("seconds", "reason"),
    [
        (3648.0, "3.04× the median chapter of this book (20:00)."),
        (300.0, "0.25× the median chapter of this book (20:00)."),
        (348.0, "0.29× the median chapter of this book (20:00)."),  # floats would give 0.28
    ],
)
def test_book_reason_gives_the_multiple_to_two_decimals(seconds: float, reason: str) -> None:
    assert in_book(seconds, 1200.0) == OutlierResult(True, reason)


@pytest.mark.parametrize(
    ("seconds", "median", "rule", "multiple"),
    [
        (3601.0, 1200.0, RULE, "3.01×"),  # 3.0008×; ordinary rounding would show 3.00×
        (599.0, 1200.0, replace(RULE, ratio=2.0), "0.49×"),  # 0.4992×; not 0.50×, the limit
        (16.039, 4.049, replace(RULE, min_seconds=1.0), "3.97×"),  # 3.9612×: ch04 against ch01
        (31.0, 3600.0, RULE, "0.01×"),  # 0.0086×; never 0.00×
    ],
)
def test_book_reason_rounds_the_multiple_away_from_1x(
    seconds: float, median: float, rule: OutlierRule, multiple: str
) -> None:
    reason = in_book(seconds, median, rule).reason

    assert reason is not None
    assert reason.startswith(f"{multiple} the median chapter")


def test_book_rule_waits_for_min_chapters() -> None:
    assert in_book(5000.0, MEDIAN, chapters=2) == NOT_AN_OUTLIER


def test_book_rule_needs_a_median() -> None:
    result = check_duration(5000.0, RULE, book_median_seconds=None, book_chapter_count=10)

    assert result == NOT_AN_OUTLIER


@pytest.mark.parametrize(("seconds", "expected"), [(10.0, TOO_SHORT), (7300.0, TOO_LONG)])
def test_fixed_limit_wins_over_the_book_rule(seconds: float, expected: OutlierResult) -> None:
    # Both durations break the book rule as well.
    assert in_book(seconds, MEDIAN, chapters=5) == expected


def test_reasons_use_the_configured_numbers() -> None:
    rule = OutlierRule(min_seconds=45.0, max_seconds=5400.0, ratio=2.0, min_chapters=5)

    assert check_duration(44.0, rule).reason == (
        "Shorter than 00:45, the minimum length; probably an empty or broken export."
    )
    assert check_duration(5401.0, rule).reason == (
        "Longer than 1:30:00, the maximum length; split it into shorter files."
    )
    assert in_book(1500.0, MEDIAN, rule, chapters=5) == OutlierResult(
        True, "2.50× the median chapter of this book (10:00)."
    )
    assert in_book(1200.0, MEDIAN, rule, chapters=5) == NOT_AN_OUTLIER
    assert in_book(1500.0, MEDIAN, rule, chapters=4) == NOT_AN_OUTLIER
