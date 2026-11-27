# Test fixtures

Small MP3s made by `scripts/make_fixtures.sh` (ffmpeg with libmp3lame). The values are what mutagen
reads from them. "Audio bytes" is the file size without its ID3 tags.

mutagen reports ch01's bitrate as 191,999 bps, so kbps must be rounded, never floored.

| File | Duration (s) | kbps | Mode | Sample rate | Ch | Size (B) | Audio bytes | Score | Deductions |
|---|---|---|---|---|---|---|---|---|---|
| ch01_192k_cbr_44k_mono.mp3 | 4.049 | 192 | CBR | 44,100 | 1 | 97,846 | 97,801 | 10 | none |
| ch02_192k_cbr_44k_mono.mp3 | 4.049 | 192 | CBR | 44,100 | 1 | 97,846 | 97,801 | 10 | none |
| ch03_192k_cbr_44k_mono.mp3 | 4.049 | 192 | CBR | 44,100 | 1 | 97,846 | 97,801 | 10 | none |
| ch04_long_192k_cbr_44k_mono.mp3 | 16.039 | 192 | CBR | 44,100 | 1 | 385,611 | 385,566 | 10 | none |
| low_128k_cbr_44k_stereo.mp3 | 4.049 | 128 | CBR | 44,100 | 2 | 65,245 | 65,200 | 7 | LOW_BITRATE 3 |
| rate_48k_192k_cbr_mono.mp3 | 4.032 | 192 | CBR | 48,000 | 1 | 97,389 | 97,344 | 9 | SAMPLE_RATE_OFF_SPEC 1 |
| poor_64k_cbr_22k_mono.mp3 | 4.075 | 64 | CBR | 22,050 | 1 | 32,853 | 32,808 | 1 | LOW_BITRATE 7, SAMPLE_RATE_OFF_SPEC 4 |
| vbr_44k_mono.mp3 | 4.049 | 122 (average) | VBR | 44,100 | 1 | 62,213 | 62,168 | 3 | LOW_BITRATE 5, NOT_CBR 2 |
| tagged_id3v1_192k_cbr_44k_mono.mp3 | 4.049 | 192 | CBR | 44,100 | 1 | 97,995 | 97,801 | 10 | none |
| truncated_192k_cbr_44k_mono.mp3 | 16.039 (from its header) | 192 | CBR | 44,100 | 1 | 192,805 | 192,760 | 5 | TRUNCATED 5 |
| not_audio.mp3 | | | | | | 45 | | | rejected: 415 INVALID_MP3 |

Notes:
- ch01, ch02 and ch03 are different tones, so their bytes (and SHA-256) differ: three distinct
  chapters of one book. ch04 is 3.96 times their median length, so it is a book outlier.
- tagged_id3v1 has the same audio as ch01 with a bigger ID3v2 tag (66 bytes) and an ID3v1 tag
  (128 bytes) at the end. Its file bytes differ from ch01, so it is not an exact duplicate.
- truncated is the first half of a 16-second file: its header promises 16 seconds, but only half
  of the audio bytes are there (50% of bitrate × duration).
- Every fixture is shorter than the default 30-second minimum, so tests that upload them set
  `outlier_min_seconds=1` (the `settings` fixture in tests/conftest.py does this).
