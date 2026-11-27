# qissa-preflight

Audiobook retailers reject a whole book when one chapter breaks their audio spec or appears twice,
and every rejection costs Qissa two to three weeks. This service checks each chapter MP3 the moment
a narrator uploads it, so those problems are caught before a book ships:

- **duration**, and whether it is an **outlier** for that book,
- a **quality score from 1 to 10**, with the reason for every lost point,
- **exact duplicate detection** by content hash, pointing at the original upload.

## Quick start

Needs Docker.

```bash
docker compose up --build
```

The API is on http://localhost:8000, with interactive docs at http://localhost:8000/docs.

```bash
curl -s -F "file=@tests/fixtures/ch01_192k_cbr_44k_mono.mp3" -F "book_id=demo-book" \
  http://localhost:8000/api/upload
```

```json
{
  "id": "bd0a366a-7f2c-4edc-9b6b-4fb406a25ce0",
  "filename": "ch01_192k_cbr_44k_mono.mp3",
  "book_id": "demo-book",
  "uploaded_at": "2026-11-27T12:25:29.062120Z",
  "file": {
    "size_bytes": 97846,
    "sha256": "d7a07da65de7b9b57f8ff7a8640df6b5dd131ec3f5bacfc6bdeb35afa9af7515"
  },
  "duration": {
    "seconds": 4.05,
    "formatted": "00:04",
    "is_outlier": true,
    "outlier_reason": "Shorter than 00:30, the minimum length; probably an empty or broken export."
  },
  "quality": {
    "score": 10,
    "bitrate_kbps": 192,
    "bitrate_mode": "CBR",
    "sample_rate_hz": 44100,
    "channels": 1,
    "deductions": []
  },
  "duplicate": {
    "is_duplicate": false,
    "original": null
  }
}
```

The demo file is a 4-second test tone, so it is flagged as shorter than the 30-second minimum.
Upload the same bytes again under any other name and the new upload (201, its own id) points
at the first one:

```json
{
  "duplicate": {
    "is_duplicate": true,
    "original": {
      "id": "bd0a366a-7f2c-4edc-9b6b-4fb406a25ce0",
      "filename": "ch01_192k_cbr_44k_mono.mp3",
      "book_id": "demo-book",
      "uploaded_at": "2026-11-27T12:25:29.062120Z",
      "url": "/api/uploads/bd0a366a-7f2c-4edc-9b6b-4fb406a25ce0"
    }
  }
}
```

## Running locally

Needs [uv](https://docs.astral.sh/uv/) and Docker (for PostgreSQL).

```bash
uv sync
docker compose up -d db                 # PostgreSQL on localhost:5433
uv run alembic upgrade head
uv run uvicorn app.main:create_app --factory --reload
```

Settings come from environment variables or a `.env` file; `.env.example` lists them all with
their defaults.

## Tests

```bash
docker compose up -d db
uv run pytest                           # unit + integration (creates and migrates qissa_test)
uv run pytest -m "not integration"      # unit tests only, no database needed
uv run ruff check . && uv run ruff format --check .
```

Integration tests run the real app over HTTP against PostgreSQL, the same engine as production.
CI (GitHub Actions) runs the linters and the whole suite against a PostgreSQL service on every
push, and checks that the Docker image builds.

## API

| Endpoint | Success | Errors |
|---|---|---|
| `POST /api/upload` (multipart: `file`, optional `book_id`) | `201` + `Location: /api/uploads/{id}` | `413 FILE_TOO_LARGE` · `415 UNSUPPORTED_FILE_TYPE` · `415 INVALID_MP3` · `422 EMPTY_FILE` · `422 VALIDATION_ERROR` |
| `GET /api/uploads/{id}` | `200`, the same body the upload returned | `404 NOT_FOUND` · `422 VALIDATION_ERROR` |
| `GET /api/health` | `200` | `503`, the same body with the failing check set to `"error"` |

- The response has one block per requirement: `duration`, `quality`, `duplicate`.
- Errors share one shape: `{"error": {"code": "INVALID_MP3", "message": "..."}}`. So do the
  framework's own: an unknown path (`404 NOT_FOUND`), a wrong method (`405 METHOD_NOT_ALLOWED`), a
  malformed multipart body (`400 BAD_REQUEST`), and `500 INTERNAL_ERROR`, which never contains a
  stack trace.
- A duplicate is not an error. It gets `201` because the upload is recorded (an audit trail of who
  uploaded what), and `duplicate.original` says which upload it copies. The file is not stored again.
- `file` must be named `*.mp3` (any case) and contain MP3 audio; the client's Content-Type is
  ignored. Uploads up to **300 MB** are accepted (`MAX_UPLOAD_MB`): deliberately above ACX's 170 MB,
  so an oversized chapter gets a clear `OVER_RETAIL_SIZE_LIMIT` deduction instead of a bare 413.
- `book_id` is optional: at most 100 characters, trimmed, blank means none, control characters
  refused. Without it, everything works except the book-relative outlier check.

## How the checks work

### Quality score (1–10)

Only encoding properties read from the MP3 headers; no audio is decoded. A file starts at 10, loses
points for each problem, and never goes below 1. The response lists every deduction.

| Problem | Points | Why this signal |
|---|---|---|
| Bitrate 160–191 / 128–159 / 96–127 / under 96 kbps | 2 / 3 / 5 / 7 | ACX requires at least 192 kbps; below about 128 kbps speech audibly degrades |
| Sample rate 48 / 32 / under 32 kHz | 1 / 3 / 4 | ACX requires 44.1 kHz; 48 kHz sounds fine but must be converted, 32 kHz and below cut the top of the voice |
| Variable or average bitrate (VBR / ABR) | 2 | ACX requires constant bitrate |
| Bitrate mode unknown (no Xing/Info/LAME header) | 1 | can't confirm CBR |
| Over 170 MB | 2 | ACX's per-file limit |
| Audio data under 80% of what the header promises | 5 | the file was cut off mid-export |

Why these signals:
- The score means **"ready for retail"**, not "sounds nice". Audible's ACX publishes exact numbers
  for per-chapter MP3s (192 kbps or more, CBR, 44.1 kHz, at most 120 minutes and 170 MB per file),
  so the thresholds follow it. That's why 48 kHz and VBR lose points even though they can sound
  fine. All thresholds and points live in `app/scoring.py`, whose docstring repeats this table.
- **File size alone is not a quality signal.** It's just bitrate × duration, so scoring it would count
  the bitrate twice and punish long chapters. It's used for what it does tell us: the retail size
  limit, and whether the file holds as much audio as its own header says.

### Duration and outliers

`duration.seconds` plus `duration.formatted` (`mm:ss`, or `h:mm:ss` from one hour).
A chapter is flagged as an outlier when:

1. it is shorter than **30 seconds** (probably an empty or broken export) or longer than
   **120 minutes** (ACX's per-file limit), always; or
2. it has a `book_id`, that book already has **at least 3** earlier chapters, and it is **under ⅓ or
   over 3×** the book's median chapter length.

Why the median: the one broken chapter we want to catch can't drag it around the way it drags an
average. Why 3×: it's a rule the client can check by hand, and it catches cut-off exports and
runaway recordings. The median is computed in PostgreSQL (`percentile_cont`) over the book's
non-duplicate uploads. All four numbers are settings (`OUTLIER_*`).

### Duplicate detection

The key is the **SHA-256 of the file's bytes**, so the filename never matters. It's checked across all
books. The first upload of some audio is its original; every later upload of the same bytes records
`duplicate_of` = that original. Two narrators uploading the same new file at the same moment can't
both become the original, because the database decides (see below). If a stored file has gone
missing, uploading the same bytes again puts it back.

## Architecture

```
POST /api/upload
  0. Content-Length over the limit?                           413, before the body is read
  1. the filename must end in .mp3                            415
  2. copy the upload to a temp file, hashing as it goes       413 past the limit, 422 if empty
  3. read the MP3 headers (mutagen)                           415 when it isn't MP3 audio
  4. quality score                                            pure function
  5. outlier check: the book's median from PostgreSQL, then the rule
  6. INSERT INTO audio_files … ON CONFLICT (sha256) DO NOTHING
       new audio → rename the temp file to audio/ab/cd/<sha256>.mp3
       known     → keep the stored file (put it back if it went missing),
                   look up the original upload
  7. INSERT INTO uploads …, commit                            201 + JSON
```

**Data model (PostgreSQL).** Two tables, because an upload and its audio are different things:
- `audio_files`: one row per unique audio. Its primary key is the 32-byte SHA-256. It holds the
  analysis (bitrate, sample rate, score, deductions as JSONB), with CHECK constraints on the score
  and the bitrate mode. The analysis depends only on the bytes, so it's computed and stored once.
- `uploads`: one row per upload request: filename, `book_id`, `duplicate_of_id`, the outlier
  flag and the time. A partial unique index (`sha256 WHERE duplicate_of_id IS NULL`) guarantees one
  original per audio and serves the "find the original" lookup; `book_id` is indexed for the median
  query.

**Storage.** Files live on disk under their hash (`ab/cd/<sha256>.mp3`): a duplicate takes no extra
space, and the client's filename never touches the filesystem (no path traversal). A file is written
to a temp file, flushed to disk, and renamed into place before the database commit. So an
interrupted or failed upload can leave a stray file, but never a row without its file. Audio never
goes into the database.

**Code layout.**

| Module | Role |
|---|---|
| `app/api.py` | HTTP routes |
| `app/main.py` | app factory, JSON error handlers, upload size guard |
| `app/service.py` | the upload use case (steps 1–7) |
| `app/audio.py` | reads MP3 headers with mutagen |
| `app/scoring.py` | quality score (pure) |
| `app/outliers.py` | outlier rule (pure) |
| `app/storage.py` | content-addressed file storage |
| `app/repository.py` | the SQL: insert-if-new, original lookup, book median |
| `app/models.py`, `migrations/` | tables and their Alembic migration |
| `app/schemas.py`, `app/errors.py` | the response contract and the error codes |
| `app/config.py` | settings |

## Assumptions

- **One file per request.** The brief says "files"; one per request keeps every response and error
  about exactly one chapter, and a client can upload a book's chapters in parallel.
- `book_id` is an opaque identifier from Qissa's catalogue. This service doesn't manage books.
- **ACX is the quality baseline**, because it publishes exact numbers for per-chapter MP3s. Other
  retailers' rules can be added as more rows in the same table.
- **Duplicates are global**: the same file in two different books is almost always a wrong upload.
- A duplicate or an outlier is **flagged, not rejected**: a producer decides. Credits files are often
  under 30 seconds and will be flagged; that's acceptable for a "take a look" flag.
- The outlier flag is fixed at upload time, against the chapters uploaded before it.
- MB means 1,000,000 bytes, the stricter reading of ACX's "170 MB".

## Trade-offs

- **Headers, not decoding.** Analysis takes milliseconds and needs no ffmpeg. The price: no loudness
  or noise checks, and the duration of a VBR file without a Xing header is an estimate.
- **Analysis inside the request.** Simple, and fast enough because only headers are read. A queue
  becomes worth it once checks that decode the audio are added.
- **Each upload is written to disk twice.** FastAPI spools the multipart body to the system temp
  folder, then the service copies it into `STORAGE_DIR/tmp` while hashing. A request whose
  Content-Length is over the limit is refused before its body is read; a chunked request is only
  counted once it has arrived, so in production a reverse proxy's body limit (for example nginx
  `client_max_body_size 301m`) is the real bound.
- **Local disk behind a small storage class**, not S3: one machine today, but the storage class is
  the only part that would change.
- **Exact duplicates only.** SHA-256 is cheap and never wrong, but a re-tagged or re-encoded copy has
  different bytes and isn't caught. The tests document this limit.
- **PostgreSQL in the tests too**, not SQLite. The integration tests need Docker, but they exercise
  the real `ON CONFLICT`, `percentile_cont`, constraints and migration.
- **Migrations run when the container starts.** Fine for one instance; with several replicas they
  would run as a separate release step.

## What I cut, and what it costs Qissa

| Cut | Cost to Qissa | Next step |
|---|---|---|
| Loudness, peak and noise-floor checks (ACX: −23 to −18 dB RMS, peak ≤ −3 dB, noise floor ≤ −60 dB) | Common ACX rejection reasons still get through: a 2–3 week rejection | Decode with ffmpeg in a background worker |
| Near-duplicates (re-tagged or re-encoded copies) | A chapter exported twice with different tags isn't caught | Hash only the audio frames, then audio fingerprints (Chromaprint) |
| Authentication | Anyone who can reach the service can upload | Put it behind Qissa's SSO or an API gateway; per-narrator keys |
| Book-level report | The first chapters of each book get only the fixed outlier limits; mixed mono/stereo files aren't caught (ACX wants consistent formatting) | `GET /api/books/{id}/report` that re-checks the whole book |
| Object storage and backups | One disk: if it fails, chapters must be uploaded again | S3-compatible storage with the same hash keys, database backups |
| Rate limits, malware scanning, stray-file cleanup | Abuse, or failed uploads leaving stray files and old `tmp/*.part` files, could fill the disk | Limits at the proxy; a periodic sweep of files with no row and of old temp files |

## What I'd do next

1. Loudness, peak and noise-floor checks (the biggest rejection risk still open).
2. The book-level report, so a producer gets one "ready to ship" answer per book.
3. Near-duplicate detection.
4. Authentication and per-narrator attribution.
5. S3 storage, backups, and metrics on rejections by reason.

## Requirements, code and tests

| Requirement | Code | Tests |
|---|---|---|
| 1. Upload API: accept `.mp3`, store it, return the analysis | `app/api.py`, `app/service.py`, `app/storage.py`, `app/main.py` | `tests/integration/test_api_upload.py`, `test_api_errors.py`, `tests/unit/test_storage.py` |
| 2. Duration and outlier flag | `app/audio.py`, `app/outliers.py`, `app/repository.py` (book median) | `tests/unit/test_audio.py`, `test_outliers.py`, `tests/integration/test_api_outliers.py` |
| 3. Quality score 1–10 | `app/scoring.py` | `tests/unit/test_scoring.py`, `tests/integration/test_api_upload.py` |
| 4. Exact duplicates, with the original | `app/repository.py`, `app/service.py`, `app/storage.py` | `tests/integration/test_api_duplicates.py`, `test_repository.py` |
| 5. Unit and integration tests | `tests/unit` (no database), `tests/integration` (HTTP + PostgreSQL) | the whole suite, in CI on every push |
| Health check, error format | `app/api.py`, `app/main.py`, `app/errors.py` | `tests/integration/test_api_health.py`, `test_api_errors.py` |
