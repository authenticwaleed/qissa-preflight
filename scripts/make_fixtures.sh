#!/usr/bin/env bash
# Rebuilds the small MP3 files in tests/fixtures. Needs ffmpeg built with libmp3lame.
# Usage: scripts/make_fixtures.sh    (FFMPEG=/path/to/ffmpeg to pick a specific binary)
set -euo pipefail

FFMPEG="${FFMPEG:-ffmpeg}"
OUT="$(cd "$(dirname "$0")/.." && pwd)/tests/fixtures"
mkdir -p "$OUT"

tone() { # tone <seconds> <hz>
  echo "sine=frequency=$2:duration=$1:sample_rate=48000"
}

mp3() { # mp3 <file name> <lavfi source> <encoder options...>
  local name=$1 source=$2
  shift 2
  "$FFMPEG" -hide_banner -loglevel error -y -f lavfi -i "$source" -c:a libmp3lame "$@" "$OUT/$name"
}

# Three different 4-second "chapters" that meet the ACX encoding spec (192 kbps CBR, 44.1 kHz).
mp3 ch01_192k_cbr_44k_mono.mp3 "$(tone 4 440)" -ac 1 -ar 44100 -b:a 192k
mp3 ch02_192k_cbr_44k_mono.mp3 "$(tone 4 550)" -ac 1 -ar 44100 -b:a 192k
mp3 ch03_192k_cbr_44k_mono.mp3 "$(tone 4 660)" -ac 1 -ar 44100 -b:a 192k

# Four times as long as the chapters above: a duration outlier within the same book.
mp3 ch04_long_192k_cbr_44k_mono.mp3 "$(tone 16 330)" -ac 1 -ar 44100 -b:a 192k

# Each misses the spec in one way.
mp3 low_128k_cbr_44k_stereo.mp3 "$(tone 4 440)" -ac 2 -ar 44100 -b:a 128k
mp3 rate_48k_192k_cbr_mono.mp3 "$(tone 4 440)" -ac 1 -ar 48000 -b:a 192k
mp3 poor_64k_cbr_22k_mono.mp3 "$(tone 4 440)" -ac 1 -ar 22050 -b:a 64k
mp3 vbr_44k_mono.mp3 "anoisesrc=duration=4:color=pink:amplitude=0.5:sample_rate=44100" \
  -ac 1 -ar 44100 -q:a 0

# Same encoding as ch01, plus an ID3v1 tag at the end (every file has an ID3v2 tag at the start).
mp3 tagged_id3v1_192k_cbr_44k_mono.mp3 "$(tone 4 440)" -ac 1 -ar 44100 -b:a 192k \
  -write_id3v1 1 -metadata title="Chapter 1"

# A 16-second file cut to its first half, like an export that stopped early. Its header still
# promises 16 seconds of audio.
mp3 full_16s.tmp.mp3 "$(tone 16 440)" -ac 1 -ar 44100 -b:a 192k
half=$(($(wc -c <"$OUT/full_16s.tmp.mp3") / 2))
head -c "$half" "$OUT/full_16s.tmp.mp3" >"$OUT/truncated_192k_cbr_44k_mono.mp3"
rm "$OUT/full_16s.tmp.mp3"

# Not audio at all, but named .mp3.
printf 'This is a text file pretending to be an MP3.\n' >"$OUT/not_audio.mp3"

echo "Fixtures written to $OUT"
