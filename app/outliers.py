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
    raise NotImplementedError
