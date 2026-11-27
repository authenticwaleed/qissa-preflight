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

from dataclasses import dataclass

from app.audio import AudioInfo

MAX_SCORE = 10
MIN_SCORE = 1
ACX_MIN_BITRATE_KBPS = 192
ACX_SAMPLE_RATE_HZ = 44_100
RETAIL_MAX_FILE_BYTES = 170_000_000  # decimal MB: the stricter reading of ACX's "170 MB"
TRUNCATION_RATIO = 0.8


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
    raise NotImplementedError
