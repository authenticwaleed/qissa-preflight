"""Duration outlier rule.

Two checks, both with strict inequalities:
1. Fixed limits, always: shorter than min_seconds (likely an empty or broken export) or longer
   than max_seconds (ACX accepts at most 120 minutes per file).
2. Within a book, once it has at least min_chapters earlier original chapters: shorter than
   median / ratio or longer than median × ratio. The median, unlike the mean, is not dragged
   around by the one broken chapter we are trying to catch, and "3× the book's typical chapter"
   is a rule a client can check by hand.
"""

import math
from dataclasses import dataclass
from fractions import Fraction

from app.audio import format_duration


@dataclass(frozen=True, slots=True)
class OutlierRule:
    """The thresholds. Their values live in one place: the OUTLIER_* settings in app/config.py."""

    min_seconds: float
    max_seconds: float
    ratio: float
    min_chapters: int


@dataclass(frozen=True, slots=True)
class OutlierResult:
    is_outlier: bool
    reason: str | None  # one sentence, e.g. "3.04× the median chapter of this book (20:00)."


def check_duration(
    duration_seconds: float,
    rule: OutlierRule,
    *,
    book_median_seconds: float | None = None,
    book_chapter_count: int = 0,
) -> OutlierResult:
    """Apply the fixed limits first, then the book comparison when there is enough history."""
    reason = _fixed_limit_reason(duration_seconds, rule)
    if reason is None:
        reason = _book_reason(duration_seconds, rule, book_median_seconds, book_chapter_count)
    return OutlierResult(is_outlier=reason is not None, reason=reason)


def _fixed_limit_reason(duration_seconds: float, rule: OutlierRule) -> str | None:
    if duration_seconds < rule.min_seconds:
        shortest = format_duration(rule.min_seconds)
        return f"Shorter than {shortest}, the minimum length; probably an empty or broken export."
    if duration_seconds > rule.max_seconds:
        longest = format_duration(rule.max_seconds)
        return f"Longer than {longest}, the maximum length; split it into shorter files."
    return None


def _book_reason(
    duration_seconds: float,
    rule: OutlierRule,
    median_seconds: float | None,
    chapter_count: int,
) -> str | None:
    # With only a chapter or two, the "typical chapter" is just whichever came first.
    if median_seconds is None or chapter_count < rule.min_chapters:
        return None
    # Exact fractions of the values as people read them: str(1800.9) is "1800.9", while the float
    # is a binary approximation. In floats, 600.3 × 3 comes out just under 1800.9, which would
    # flag a chapter at exactly 3× the median and show it as "3.00×".
    times = Fraction(str(duration_seconds)) / Fraction(str(median_seconds))
    ratio = Fraction(str(rule.ratio))
    if 1 / ratio <= times <= ratio:
        return None
    median = format_duration(median_seconds)
    return f"{_two_decimals_away_from_1(times)}× the median chapter of this book ({median})."


def _two_decimals_away_from_1(times: Fraction) -> str:
    """``times`` to two decimals, rounded up above 1× and down below it.

    So a flagged chapter never shows a multiple that looks allowed: 3.001× shows as 3.01×, where
    ordinary rounding would show 3.00×. Below 1× it stops at 0.01×, never 0.00×.
    """
    cents = math.ceil(times * 100) if times > 1 else max(math.floor(times * 100), 1)
    return f"{cents / 100:.2f}"
