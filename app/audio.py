"""Reads MP3 headers with mutagen.

Nothing here decodes audio: mutagen parses the MPEG frame headers and the Xing/Info/LAME header,
which is enough for duration, bitrate, bitrate mode, sample rate and channels, and takes
milliseconds even for a two-hour chapter.
"""

import math
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO

from mutagen import MutagenError
from mutagen.mp3 import BitrateMode as MutagenBitrateMode
from mutagen.mp3 import MPEGInfo

from app.errors import InvalidMp3

ID3V2_HEADER_BYTES = 10  # an ID3v2 footer, when present, is the same size
ID3V2_FOOTER_FLAG = 0x10
ID3V1_TAG_BYTES = 128

NO_MP3_AUDIO = "No MP3 audio found in this file."


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


# mutagen's modes are bare ints without names, so they are mapped one by one. Anything else,
# mutagen's own UNKNOWN included, becomes UNKNOWN.
_BITRATE_MODES = {
    MutagenBitrateMode.CBR: BitrateMode.CBR,
    MutagenBitrateMode.VBR: BitrateMode.VBR,
    MutagenBitrateMode.ABR: BitrateMode.ABR,
}


def read_audio_info(path: Path) -> AudioInfo:
    """Read the MPEG headers of the file at ``path``.

    Raises app.errors.InvalidMp3 when mutagen finds no MPEG frames, the stream is not Layer III
    (for example an MP2 file renamed to .mp3), or the duration is not positive. The caller
    rejects empty files before this is called.
    """
    size_bytes = path.stat().st_size
    with path.open("rb") as file:
        stream = _read_mpeg_stream(file)
        tag_bytes = _tag_bytes(file, size_bytes)
    return AudioInfo(
        duration_seconds=stream.length,
        # Rounded, not floored: mutagen reads a 192 kbps CBR file as 191,999 bps, and flooring
        # would cost a perfect file points.
        bitrate_kbps=round(stream.bitrate / 1000),
        bitrate_mode=_BITRATE_MODES.get(stream.bitrate_mode, BitrateMode.UNKNOWN),
        sample_rate_hz=stream.sample_rate,
        channels=stream.channels,
        size_bytes=size_bytes,
        audio_bytes=max(0, size_bytes - tag_bytes),
    )


def format_duration(seconds: float) -> str:
    """'mm:ss' under an hour ('04:05'), 'h:mm:ss' from one hour ('1:02:05').

    Rounds to the nearest whole second, halves going up: 59.5 -> '01:00'.
    """
    # Not round(): it sends halves to the even neighbour, so 2.5 s would show as 00:02.
    hours, rest = divmod(math.floor(seconds + 0.5), 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _read_mpeg_stream(file: BinaryIO) -> MPEGInfo:
    """Parse the first MPEG frames of ``file``; InvalidMp3 unless they are believable MP3 audio."""
    try:
        # MPEGInfo rather than mutagen.mp3.MP3: MP3 also parses the ID3 tag and fails on a
        # malformed one, which would reject a good recording over its metadata.
        stream = MPEGInfo(file)
    except MutagenError as exc:
        raise InvalidMp3(NO_MP3_AUDIO) from exc
    # mutagen settles for two frame headers in a row and calls the result "sketchy". Quiet 16-bit
    # samples just below zero are 0xFF bytes, so a WAV recording can hold two such headers by
    # chance; only a longer run of frames or a Xing/Info/VBRI header counts as MP3 audio.
    if stream.sketchy:
        raise InvalidMp3(NO_MP3_AUDIO)
    if stream.layer != 3:
        raise InvalidMp3(f"This is MPEG Layer {stream.layer} audio (MP{stream.layer}), not MP3.")
    if stream.length <= 0:
        raise InvalidMp3("This MP3 holds no audio: its header gives a duration of zero.")
    return stream


def _tag_bytes(file: BinaryIO, size_bytes: int) -> int:
    """Bytes taken by an ID3v2 tag at the start and an ID3v1 tag at the end (0 for a missing one).

    Reads only the 10-byte ID3v2 header and the last 128 bytes, however long the file is.
    """
    file.seek(0)
    tag_bytes = _id3v2_size(file.read(ID3V2_HEADER_BYTES))
    # An ID3v1 tag follows the ID3v2 tag; it can never sit inside it.
    if size_bytes - tag_bytes >= ID3V1_TAG_BYTES:
        file.seek(-ID3V1_TAG_BYTES, os.SEEK_END)
        if file.read(3) == b"TAG":
            tag_bytes += ID3V1_TAG_BYTES
    return tag_bytes


def _id3v2_size(header: bytes) -> int:
    """Size of the ID3v2 tag that ``header`` (the file's first 10 bytes) opens, or 0 if none.

    The size field is a syncsafe integer, 7 bits per byte, that counts neither the 10-byte header
    nor the optional 10-byte footer.
    """
    if len(header) < ID3V2_HEADER_BYTES or not header.startswith(b"ID3"):
        return 0
    body = 0
    for byte in header[6:10]:
        body = (body << 7) | (byte & 0x7F)
    footer = ID3V2_HEADER_BYTES if header[5] & ID3V2_FOOTER_FLAG else 0
    return ID3V2_HEADER_BYTES + body + footer
