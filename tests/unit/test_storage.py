"""LocalStorage: streaming hash, size limit, content-addressed keys and the atomic commit."""

import hashlib
import io
import random
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.errors import FileTooLarge
from app.storage import CHUNK_SIZE, LocalStorage


def payload(size: int) -> bytes:
    """Reproducible bytes, different for every size."""
    return random.Random(size).randbytes(size)


def temp_files(root: Path) -> list[Path]:
    return list((root / "tmp").iterdir())


class RecordingStream(io.BytesIO):
    """Remembers how many bytes each read asked for."""

    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.read_sizes: list[int | None] = []

    def read(self, size: int | None = -1, /) -> bytes:
        self.read_sizes.append(size)
        return super().read(size)


class DisconnectingStream(io.BytesIO):
    """Fails after the first chunk, like a client that goes away mid-upload."""

    def read(self, size: int | None = -1, /) -> bytes:
        if self.tell() >= CHUNK_SIZE:
            raise ConnectionResetError("client disconnected")
        return super().read(size)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "audio"


@pytest.fixture
def storage(root: Path) -> Iterator[LocalStorage]:
    storage = LocalStorage(root)
    yield storage
    # Every case must end with its temp files committed, discarded or cleaned up after an error.
    assert temp_files(root) == []


def test_creates_its_folders_and_reports_them_writable(tmp_path: Path) -> None:
    root = tmp_path / "data" / "audio"

    storage = LocalStorage(root)

    assert (root / "tmp").is_dir()
    assert storage.is_writable()


def test_is_not_writable_once_its_folder_is_gone(root: Path) -> None:
    storage = LocalStorage(root)
    shutil.rmtree(root)

    assert not storage.is_writable()


def test_stage_hashes_while_copying_in_chunks(storage: LocalStorage, root: Path) -> None:
    data = payload(2 * CHUNK_SIZE + 123)
    stream = RecordingStream(data)

    staged = storage.stage(stream, max_bytes=len(data))

    assert staged.sha256 == hashlib.sha256(data).digest()
    assert staged.sha256_hex == hashlib.sha256(data).hexdigest()
    assert staged.size_bytes == len(data)
    assert staged.path.parent == root / "tmp"
    assert staged.path.read_bytes() == data
    # Only bounded reads: the whole upload is never asked for at once.
    assert set(stream.read_sizes) == {CHUNK_SIZE}
    storage.discard(staged)


def test_key_spreads_files_over_two_folder_levels() -> None:
    sha256_hex = "ab12" + "0" * 60

    assert LocalStorage.key_for(sha256_hex) == f"ab/12/{sha256_hex}.mp3"


def test_commit_moves_the_file_to_its_key(storage: LocalStorage, root: Path) -> None:
    data = payload(1000)
    staged = storage.stage(io.BytesIO(data), max_bytes=len(data))

    key = storage.commit(staged)

    sha256_hex = hashlib.sha256(data).hexdigest()
    assert key == f"{sha256_hex[:2]}/{sha256_hex[2:4]}/{sha256_hex}.mp3"
    assert storage.path_for(key) == root / key
    assert storage.path_for(key).read_bytes() == data
    assert not staged.path.exists()


def test_committing_the_same_bytes_twice_keeps_one_file(storage: LocalStorage, root: Path) -> None:
    data = payload(1000)
    first = storage.stage(io.BytesIO(data), max_bytes=len(data))
    second = storage.stage(io.BytesIO(data), max_bytes=len(data))

    key = storage.commit(first)
    assert storage.commit(second) == key

    # Searches the staging folder too, so a leftover temp file would show up here.
    assert [path for path in root.rglob("*") if path.is_file()] == [storage.path_for(key)]
    assert storage.path_for(key).read_bytes() == data


def test_stage_stops_reading_past_the_limit(storage: LocalStorage, root: Path) -> None:
    stream = io.BytesIO(payload(3 * CHUNK_SIZE))

    with pytest.raises(FileTooLarge, match="1 MB"):
        storage.stage(stream, max_bytes=1_000_000)

    assert stream.tell() <= CHUNK_SIZE  # gave up within the first chunk, not at the end
    assert temp_files(root) == []


def test_stage_accepts_exactly_max_bytes(storage: LocalStorage) -> None:
    data = payload(CHUNK_SIZE + 1)  # the limit falls inside the second chunk

    staged = storage.stage(io.BytesIO(data), max_bytes=len(data))

    assert staged.size_bytes == len(data)
    storage.discard(staged)


def test_stage_rejects_one_byte_over_the_limit(storage: LocalStorage, root: Path) -> None:
    data = payload(CHUNK_SIZE + 1)

    with pytest.raises(FileTooLarge):
        storage.stage(io.BytesIO(data), max_bytes=len(data) - 1)

    assert temp_files(root) == []


def test_stage_removes_the_temp_file_when_the_upload_breaks_off(
    storage: LocalStorage, root: Path
) -> None:
    with pytest.raises(ConnectionResetError):
        storage.stage(DisconnectingStream(payload(2 * CHUNK_SIZE)), max_bytes=4 * CHUNK_SIZE)

    assert temp_files(root) == []


def test_discard_deletes_the_temp_file_and_is_safe_after_commit(storage: LocalStorage) -> None:
    data = payload(1000)
    discarded = storage.stage(io.BytesIO(data), max_bytes=len(data))
    committed = storage.stage(io.BytesIO(data), max_bytes=len(data))

    storage.discard(discarded)
    key = storage.commit(committed)
    storage.discard(committed)  # cleanup paths may run after a commit: no error, file kept

    assert not discarded.path.exists()
    assert storage.path_for(key).read_bytes() == data
