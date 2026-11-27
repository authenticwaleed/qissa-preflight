"""Duration outlier rule.

Two checks, both with strict inequalities:
1. Fixed limits, always: shorter than min_seconds (likely an empty or broken export) or longer
   than max_seconds (ACX accepts at most 120 minutes per file).
2. Within a book, once it has at least min_chapters earlier original chapters: shorter than
   median / ratio or longer than median × ratio. The median, unlike the mean, is not dragged
   around by the one broken chapter we are trying to catch, and "3× the book's typical chapter"
   is a rule a client can check by hand.
"""

from dataclasses import dataclass

from app.audio import format_duration


@dataclass(frozen=True, slots=True)
class OutlierRule:
    min_seconds: float = 30.0
    max_seconds: float = 7200.0
    ratio: float = 3.0
    min_chapters: int = 3


@dataclass(frozen=True, slots=True)
class OutlierResult:
    is_outlier: bool
    reason: str | None  # one sentence, e.g. "4.0× the median chapter of this book (00:04)"


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
        return f"Shorter than {rule.min_seconds:g} s; probably an empty or broken export."
    if duration_seconds > rule.max_seconds:
        return f"Longer than {format_duration(rule.max_seconds)}, the per-file limit."
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
    if median_seconds / rule.ratio <= duration_seconds <= median_seconds * rule.ratio:
        return None
    times = duration_seconds / median_seconds
    return f"{times:.1f}× the median chapter of this book ({format_duration(median_seconds)})."
