"""read_audio_info on the fixtures and on small hand-built files, and format_duration."""

import wave
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from app.audio import AudioInfo, BitrateMode, format_duration, read_audio_info
from app.errors import InvalidMp3

FixtureFile = Callable[[str], Path]

CBR = BitrateMode.CBR
VBR = BitrateMode.VBR

# What tests/fixtures/README.md lists for each fixture, in AudioInfo's field order: duration
# (rounded to 1 ms there), kbps, mode, sample rate, channels, file size, audio bytes.
README = {
    "ch01_192k_cbr_44k_mono.mp3": AudioInfo(4.049, 192, CBR, 44_100, 1, 97_846, 97_801),
    "ch02_192k_cbr_44k_mono.mp3": AudioInfo(4.049, 192, CBR, 44_100, 1, 97_846, 97_801),
    "ch03_192k_cbr_44k_mono.mp3": AudioInfo(4.049, 192, CBR, 44_100, 1, 97_846, 97_801),
    "ch04_long_192k_cbr_44k_mono.mp3": AudioInfo(16.039, 192, CBR, 44_100, 1, 385_611, 385_566),
    "low_128k_cbr_44k_stereo.mp3": AudioInfo(4.049, 128, CBR, 44_100, 2, 65_245, 65_200),
    "rate_48k_192k_cbr_mono.mp3": AudioInfo(4.032, 192, CBR, 48_000, 1, 97_389, 97_344),
    "poor_64k_cbr_22k_mono.mp3": AudioInfo(4.075, 64, CBR, 22_050, 1, 32_853, 32_808),
    "vbr_44k_mono.mp3": AudioInfo(4.049, 122, VBR, 44_100, 1, 62_213, 62_168),
    "tagged_id3v1_192k_cbr_44k_mono.mp3": AudioInfo(4.049, 192, CBR, 44_100, 1, 97_995, 97_801),
    "truncated_192k_cbr_44k_mono.mp3": AudioInfo(16.039, 192, CBR, 44_100, 1, 192_805, 192_760),
}

# Hand-built MPEG-1 frame headers: mono, 44.1 kHz, 192 kbps, no CRC, no padding. At that rate a
# frame is 144 × 192,000 / 44,100 = 626 bytes in both layers.
LAYER_3_HEADER = bytes.fromhex("fffbb0c0")
LAYER_2_HEADER = bytes.fromhex("fffda0c0")
FRAME_BYTES = 626
XING_OFFSET = 21  # where an MPEG-1 mono frame carries its Xing/Info header


def frames(header: bytes, count: int) -> bytes:
    """``count`` frames of silence without a Xing/Info header."""
    return (header + bytes(FRAME_BYTES - len(header))) * count


def xing_header(kind: bytes, frame_count: int) -> bytes:
    """A Xing or Info header that gives only the frame count."""
    return kind + (1).to_bytes(4, "big") + frame_count.to_bytes(4, "big")


def layer_3_stream(vbr_header: bytes) -> bytes:
    """Nine Layer III frames; the first carries ``vbr_header`` where a Xing header belongs."""
    first = bytearray(frames(LAYER_3_HEADER, 1))
    first[XING_OFFSET : XING_OFFSET + len(vbr_header)] = vbr_header
    return bytes(first) + frames(LAYER_3_HEADER, 8)


def write_mp3(tmp_path: Path, data: bytes) -> Path:
    path = tmp_path / "chapter.mp3"
    path.write_bytes(data)
    return path


@pytest.mark.parametrize("name", README)
def test_reads_what_the_fixture_readme_lists(fixture_file: FixtureFile, name: str) -> None:
    expected = README[name]

    info = read_audio_info(fixture_file(name))

    assert info.duration_seconds == pytest.approx(expected.duration_seconds, abs=0.0005)
    assert replace(info, duration_seconds=expected.duration_seconds) == expected


def test_rejects_a_text_file_named_mp3(fixture_file: FixtureFile) -> None:
    with pytest.raises(InvalidMp3, match="No MP3 audio found in this file"):
        read_audio_info(fixture_file("not_audio.mp3"))


def test_rejects_a_wav_recording_named_mp3(tmp_path: Path) -> None:
    """Quiet samples just below zero are 0xFF bytes, so a WAV recording can contain what looks
    like MPEG frame headers. This one holds two in a row, which mutagen on its own accepts."""
    samples = bytearray(20_000)  # digital silence
    for offset in (1_000, 1_000 + FRAME_BYTES):
        samples[offset : offset + len(LAYER_3_HEADER)] = LAYER_3_HEADER
    path = tmp_path / "chapter.mp3"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(44_100)
        wav.writeframes(bytes(samples))

    with pytest.raises(InvalidMp3, match="No MP3 audio found in this file"):
        read_audio_info(path)


def test_rejects_mp2_audio(tmp_path: Path) -> None:
    with pytest.raises(InvalidMp3, match="MPEG Layer 2 audio"):
        read_audio_info(write_mp3(tmp_path, frames(LAYER_2_HEADER, 8)))


def test_rejects_a_stream_whose_header_promises_no_audio(tmp_path: Path) -> None:
    stream = layer_3_stream(xing_header(b"Info", frame_count=0))

    with pytest.raises(InvalidMp3, match="duration of zero"):
        read_audio_info(write_mp3(tmp_path, stream))


def test_bitrate_mode_is_unknown_without_a_xing_or_info_header(tmp_path: Path) -> None:
    info = read_audio_info(write_mp3(tmp_path, frames(LAYER_3_HEADER, 8)))

    assert info.bitrate_mode is BitrateMode.UNKNOWN
    assert info.bitrate_kbps == 192


def test_reads_abr_from_the_lame_header(tmp_path: Path) -> None:
    # LAME follows the Xing header with its version and its own tag, whose first byte holds the
    # encoding method: 2 is ABR. (The fixtures cover CBR and VBR.)
    lame = b"LAME3.100" + bytes([2])
    stream = layer_3_stream(xing_header(b"Xing", frame_count=8) + lame)

    info = read_audio_info(write_mp3(tmp_path, stream))

    assert info.bitrate_mode is BitrateMode.ABR


def test_audio_bytes_leave_out_an_id3v2_footer(tmp_path: Path) -> None:
    # ID3v2.4 with the footer flag and a 300-byte body; 300 in syncsafe form (7 bits per byte)
    # is 0x02 0x2C.
    header = b"ID3" + bytes([4, 0, 0x10, 0, 0, 0x02, 0x2C])
    footer = b"3DI" + bytes([4, 0, 0x10, 0, 0, 0x02, 0x2C])
    audio = frames(LAYER_3_HEADER, 8)

    info = read_audio_info(write_mp3(tmp_path, header + bytes(300) + footer + audio))

    assert info.size_bytes == 10 + 300 + 10 + len(audio)
    assert info.audio_bytes == len(audio)


@pytest.mark.parametrize(
    ("seconds", "formatted"),
    [
        (0, "00:00"),
        (4.049, "00:04"),
        (2.5, "00:03"),  # halves go up, where round() would go to the even 2
        (59.5, "01:00"),
        (1441.3, "24:01"),
        (3599.4, "59:59"),
        (3599.5, "1:00:00"),  # rounds up to an hour, so it takes the hour format
        (3600, "1:00:00"),
        (3725, "1:02:05"),
        (36_000, "10:00:00"),
    ],
)
def test_format_duration(seconds: float, formatted: str) -> None:
    assert format_duration(seconds) == formatted
