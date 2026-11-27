"""Quality score from 1 to 10, from encoding properties only.

A file starts at 10 and loses points for each problem below; the score never goes under 1.
The score means "ready for retail" rather than "sounds nice": the thresholds follow Audible's
published ACX audio requirements (MP3 at 192 kbps or more, constant bit rate, 44.1 kHz, files up
to 170 MB). That is why 48 kHz and VBR lose points although they can sound fine.

| Code                   | Problem                                                | Points        |
|------------------------|--------------------------------------------------------|---------------|
| LOW_BITRATE            | 160–191 kbps / 128–159 / 96–127 / under 96              | 2 / 3 / 5 / 7 |
| SAMPLE_RATE_OFF_SPEC   | 48 kHz / 32 kHz / under 32 kHz (44.1 kHz loses nothing) | 1 / 3 / 4     |
| NOT_CBR                | variable (VBR) or average (ABR) bitrate                 | 2             |
| BITRATE_MODE_UNKNOWN   | no header says which bitrate mode was used              | 1             |
| OVER_RETAIL_SIZE_LIMIT | more than 170,000,000 bytes                             | 2             |
| TRUNCATED              | audio bytes under 80% of bitrate × duration / 8         | 5             |

File size on its own is not a quality signal: it is bitrate × duration, so it would count the
bitrate twice. It is used for the retail size limit and to spot files cut off mid-export.
"""

import math
from dataclasses import dataclass

from app.audio import AudioInfo, BitrateMode

MAX_SCORE = 10
MIN_SCORE = 1
ACX_MIN_BITRATE_KBPS = 192
ACX_SAMPLE_RATE_HZ = 44_100
RETAIL_MAX_FILE_BYTES = 170_000_000  # decimal MB: the stricter reading of ACX's "170 MB"
TRUNCATION_RATIO = 0.8

_NOT_CBR_NAMES = {
    BitrateMode.VBR: "Variable bitrate (VBR)",
    BitrateMode.ABR: "Average bitrate (ABR)",
}


@dataclass(frozen=True, slots=True)
class Deduction:
    code: str
    points: int
    message: str  # one sentence a narrator can act on


@dataclass(frozen=True, slots=True)
class QualityResult:
    score: int
    deductions: tuple[Deduction, ...]


def score_quality(info: AudioInfo) -> QualityResult:
    """Apply the table above. Deductions are listed in the table's order."""
    checks = (
        _low_bitrate(info),
        _sample_rate_off_spec(info),
        _bitrate_mode(info),
        _over_retail_size_limit(info),
        _truncated(info),
    )
    deductions = tuple(deduction for deduction in checks if deduction is not None)
    points = sum(deduction.points for deduction in deductions)
    return QualityResult(score=max(MIN_SCORE, MAX_SCORE - points), deductions=deductions)


def _low_bitrate(info: AudioInfo) -> Deduction | None:
    kbps = info.bitrate_kbps
    if kbps >= ACX_MIN_BITRATE_KBPS:
        return None
    if kbps >= 160:
        points = 2
    elif kbps >= 128:
        points = 3
    elif kbps >= 96:
        points = 5
    else:
        points = 7
    message = f"{kbps} kbps is below the {ACX_MIN_BITRATE_KBPS} kbps minimum that ACX requires."
    return Deduction("LOW_BITRATE", points, message)


def _sample_rate_off_spec(info: AudioInfo) -> Deduction | None:
    hz = info.sample_rate_hz
    if hz == ACX_SAMPLE_RATE_HZ:
        return None
    # 48 kHz is the only MPEG rate above 44.1 kHz: off spec, but nothing audible is lost. Lower
    # rates also cut the treble (32 kHz keeps nothing above 16 kHz), so they cost more.
    if hz > ACX_SAMPLE_RATE_HZ:
        points = 1
    elif hz >= 32_000:
        points = 3
    else:
        points = 4
    message = f"{hz:,} Hz; ACX requires {ACX_SAMPLE_RATE_HZ:,} Hz."
    return Deduction("SAMPLE_RATE_OFF_SPEC", points, message)


def _bitrate_mode(info: AudioInfo) -> Deduction | None:
    """NOT_CBR or BITRATE_MODE_UNKNOWN: a file has one bitrate mode, so at most one applies."""
    mode = info.bitrate_mode
    if mode == BitrateMode.CBR:
        return None
    if mode == BitrateMode.UNKNOWN:
        message = (
            "The file does not say whether its bitrate is constant; "
            "ACX requires constant bitrate (CBR)."
        )
        return Deduction("BITRATE_MODE_UNKNOWN", 1, message)
    message = f"{_NOT_CBR_NAMES[mode]}; ACX requires constant bitrate (CBR)."
    return Deduction("NOT_CBR", 2, message)


def _over_retail_size_limit(info: AudioInfo) -> Deduction | None:
    if info.size_bytes <= RETAIL_MAX_FILE_BYTES:
        return None
    message = f"{info.size_bytes:,} bytes; ACX accepts files up to {RETAIL_MAX_FILE_BYTES:,} bytes."
    return Deduction("OVER_RETAIL_SIZE_LIMIT", 2, message)


def _truncated(info: AudioInfo) -> Deduction | None:
    """Compare the audio bytes on disk with what the header's bitrate and duration promise."""
    if info.bitrate_kbps <= 0 or info.duration_seconds <= 0:
        return None  # the header promises nothing to compare against
    promised_bytes = info.bitrate_kbps * 1000 / 8 * info.duration_seconds
    ratio = info.audio_bytes / promised_bytes
    if ratio >= TRUNCATION_RATIO:
        return None
    percent = math.floor(ratio * 100)  # floored, so a file just under the line never shows 80%
    message = (
        f"Only {percent}% of the audio data its header promises is there; the file looks cut off."
    )
    return Deduction("TRUNCATED", 5, message)
