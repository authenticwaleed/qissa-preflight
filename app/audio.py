"""Reads MP3 headers with mutagen.

Nothing here decodes audio: mutagen parses the MPEG frame headers and the Xing/Info/LAME header,
which is enough for duration, bitrate, bitrate mode, sample rate and channels, and takes
milliseconds even for a two-hour chapter.
"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class BitrateMode(StrEnum):
    CBR = "CBR"
    VBR = "VBR"
    ABR = "ABR"
    UNKNOWN = "UNKNOWN"  # no Xing/Info/LAME header says which mode was used


@dataclass(frozen=True, slots=True)
class AudioInfo:
    duration_seconds: float
    bitrate_kbps: int  # the average for VBR files
    bitrate_mode: BitrateMode
    sample_rate_hz: int
    channels: int
    size_bytes: int  # the whole file
    audio_bytes: int  # size_bytes without the ID3v2 tag (header, body, footer) or an ID3v1 tag


def read_audio_info(path: Path) -> AudioInfo:
    """Read the MPEG headers of the file at ``path``.

    Raises app.errors.InvalidMp3 when mutagen finds no MPEG frames, the stream is not Layer III
    (for example an MP2 file renamed to .mp3), or the duration is not positive. The caller
    rejects empty files before this is called.
    """
    raise NotImplementedError


def format_duration(seconds: float) -> str:
    """'mm:ss' under an hour ('04:05'), 'h:mm:ss' from one hour ('1:02:05').

    Rounds to the nearest whole second, halves going up: 59.5 -> '01:00'.
    """
    raise NotImplementedError
