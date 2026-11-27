"""score_quality: every row and boundary of the table in app/scoring.py, then the fixtures."""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from app.audio import AudioInfo, BitrateMode, read_audio_info
from app.scoring import Deduction, QualityResult, score_quality

FixtureFile = Callable[[str], Path]

# Meets every ACX rule: 192 kbps CBR at 44.1 kHz, ten minutes, every promised byte present.
PERFECT = AudioInfo(
    duration_seconds=600.0,
    bitrate_kbps=192,
    bitrate_mode=BitrateMode.CBR,
    sample_rate_hz=44_100,
    channels=1,
    size_bytes=14_400_045,
    audio_bytes=14_400_000,  # 192,000 bits/s × 600 s / 8
)


def chapter(**changes: Any) -> AudioInfo:
    """PERFECT with some fields changed."""
    return replace(PERFECT, **changes)


def deductions(info: AudioInfo) -> list[tuple[str, int]]:
    return [(deduction.code, deduction.points) for deduction in score_quality(info).deductions]


def test_a_file_that_meets_the_acx_spec_scores_10() -> None:
    assert score_quality(PERFECT) == QualityResult(score=10, deductions=())


@pytest.mark.parametrize(
    ("kbps", "points"),
    [(192, 0), (191, 2), (160, 2), (159, 3), (128, 3), (127, 5), (96, 5), (95, 7), (32, 7)],
)
def test_low_bitrate_bands(kbps: int, points: int) -> None:
    expected = [("LOW_BITRATE", points)] if points else []
    assert deductions(chapter(bitrate_kbps=kbps)) == expected


@pytest.mark.parametrize(
    ("hz", "points"),
    [(44_100, 0), (48_000, 1), (32_000, 3), (24_000, 4), (22_050, 4), (8_000, 4)],
)
def test_sample_rate_off_spec(hz: int, points: int) -> None:
    expected = [("SAMPLE_RATE_OFF_SPEC", points)] if points else []
    assert deductions(chapter(sample_rate_hz=hz)) == expected


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (BitrateMode.CBR, []),
        (BitrateMode.VBR, [("NOT_CBR", 2)]),
        (BitrateMode.ABR, [("NOT_CBR", 2)]),
        (BitrateMode.UNKNOWN, [("BITRATE_MODE_UNKNOWN", 1)]),
    ],
)
def test_bitrate_mode(mode: BitrateMode, expected: list[tuple[str, int]]) -> None:
    assert deductions(chapter(bitrate_mode=mode)) == expected


@pytest.mark.parametrize(
    ("size_bytes", "expected"),
    [(170_000_000, []), (170_000_001, [("OVER_RETAIL_SIZE_LIMIT", 2)])],
)
def test_retail_size_limit(size_bytes: int, expected: list[tuple[str, int]]) -> None:
    assert deductions(chapter(size_bytes=size_bytes)) == expected


@pytest.mark.parametrize(
    ("audio_bytes", "expected"),
    [
        (14_400_000, []),
        (11_520_000, []),  # exactly 80% of what the header promises
        (11_519_999, [("TRUNCATED", 5)]),
        (0, [("TRUNCATED", 5)]),
    ],
)
def test_truncated(audio_bytes: int, expected: list[tuple[str, int]]) -> None:
    assert deductions(chapter(audio_bytes=audio_bytes)) == expected


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"duration_seconds": 0.0}, []),
        ({"bitrate_kbps": 0}, [("LOW_BITRATE", 7)]),
    ],
)
def test_truncation_needs_a_bitrate_and_a_duration(
    changes: dict[str, Any], expected: list[tuple[str, int]]
) -> None:
    assert deductions(chapter(audio_bytes=0, **changes)) == expected


@pytest.mark.parametrize(
    ("info", "expected"),
    [
        (
            chapter(bitrate_kbps=128),
            Deduction("LOW_BITRATE", 3, "128 kbps; ACX requires at least 192 kbps."),
        ),
        (
            chapter(sample_rate_hz=48_000),
            Deduction("SAMPLE_RATE_OFF_SPEC", 1, "48,000 Hz; ACX requires 44,100 Hz."),
        ),
        (
            chapter(bitrate_mode=BitrateMode.VBR),
            Deduction("NOT_CBR", 2, "Variable bitrate (VBR); ACX requires constant bitrate (CBR)."),
        ),
        (
            chapter(bitrate_mode=BitrateMode.ABR),
            Deduction("NOT_CBR", 2, "Average bitrate (ABR); ACX requires constant bitrate (CBR)."),
        ),
        (
            chapter(bitrate_mode=BitrateMode.UNKNOWN),
            Deduction(
                "BITRATE_MODE_UNKNOWN",
                1,
                "Unknown bitrate mode; ACX requires constant bitrate (CBR).",
            ),
        ),
        (
            chapter(size_bytes=180_000_000),
            Deduction(
                "OVER_RETAIL_SIZE_LIMIT",
                2,
                "180,000,000 bytes; ACX requires at most 170,000,000 bytes per file.",
            ),
        ),
        (
            chapter(audio_bytes=7_200_000),
            Deduction(
                "TRUNCATED",
                5,
                "Only 50% of the audio data its header promises is there; the file looks cut off.",
            ),
        ),
        (
            chapter(audio_bytes=11_519_999),
            Deduction(
                "TRUNCATED",
                5,
                "Only 79% of the audio data its header promises is there; the file looks cut off.",
            ),
        ),
    ],
)
def test_messages_give_the_actual_value(info: AudioInfo, expected: Deduction) -> None:
    assert score_quality(info).deductions == (expected,)


@pytest.mark.parametrize(
    ("changes", "score"),
    [
        ({"sample_rate_hz": 48_000}, 9),
        ({"bitrate_kbps": 128, "sample_rate_hz": 48_000}, 6),
        ({"bitrate_kbps": 95, "bitrate_mode": BitrateMode.VBR}, 1),  # 9 points: exactly 1
        ({"bitrate_kbps": 95, "bitrate_mode": BitrateMode.VBR, "sample_rate_hz": 48_000}, 1),
    ],
)
def test_score_is_10_minus_the_points_and_never_under_1(
    changes: dict[str, Any], score: int
) -> None:
    assert score_quality(chapter(**changes)).score == score


@pytest.mark.parametrize(
    ("mode", "mode_code"),
    [(BitrateMode.VBR, "NOT_CBR"), (BitrateMode.UNKNOWN, "BITRATE_MODE_UNKNOWN")],
)
def test_every_problem_at_once_in_table_order(mode: BitrateMode, mode_code: str) -> None:
    worst = chapter(
        bitrate_kbps=64,
        sample_rate_hz=22_050,
        bitrate_mode=mode,
        size_bytes=180_000_000,
        audio_bytes=1_000_000,  # about a fifth of what 64 kbps for ten minutes promises
    )

    result = score_quality(worst)

    assert [deduction.code for deduction in result.deductions] == [
        "LOW_BITRATE",
        "SAMPLE_RATE_OFF_SPEC",
        mode_code,
        "OVER_RETAIL_SIZE_LIMIT",
        "TRUNCATED",
    ]
    assert result.score == 1


# Score and deductions for each fixture, as listed in tests/fixtures/README.md.
FIXTURE_SCORES = {
    "ch01_192k_cbr_44k_mono.mp3": (10, []),
    "ch02_192k_cbr_44k_mono.mp3": (10, []),
    "ch03_192k_cbr_44k_mono.mp3": (10, []),
    "ch04_long_192k_cbr_44k_mono.mp3": (10, []),
    "low_128k_cbr_44k_stereo.mp3": (7, [("LOW_BITRATE", 3)]),
    "rate_48k_192k_cbr_mono.mp3": (9, [("SAMPLE_RATE_OFF_SPEC", 1)]),
    "poor_64k_cbr_22k_mono.mp3": (1, [("LOW_BITRATE", 7), ("SAMPLE_RATE_OFF_SPEC", 4)]),
    "vbr_44k_mono.mp3": (3, [("LOW_BITRATE", 5), ("NOT_CBR", 2)]),
    "tagged_id3v1_192k_cbr_44k_mono.mp3": (10, []),
    "truncated_192k_cbr_44k_mono.mp3": (5, [("TRUNCATED", 5)]),
}


@pytest.mark.parametrize("name", FIXTURE_SCORES)
def test_fixture_scores_match_the_fixture_readme(fixture_file: FixtureFile, name: str) -> None:
    score, expected = FIXTURE_SCORES[name]
    info = read_audio_info(fixture_file(name))

    assert score_quality(info).score == score
    assert deductions(info) == expected
