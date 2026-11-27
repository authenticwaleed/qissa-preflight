"""Local file storage, content-addressed: every file lives under its own SHA-256.

The same bytes always map to the same path, so a duplicate never takes extra disk space, and the
client's filename never touches the filesystem.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True, slots=True)
class StagedFile:
    path: Path  # a temp file under <root>/tmp, on the same filesystem as the final location
    sha256: bytes  # the 32-byte digest
    size_bytes: int

    @property
    def sha256_hex(self) -> str:
        return self.sha256.hex()


class LocalStorage:
    def __init__(self, root: Path) -> None:
        """Use ``root`` for stored files; create it and root/tmp when missing."""
        raise NotImplementedError

    def stage(self, stream: BinaryIO, max_bytes: int) -> StagedFile:
        """Copy ``stream`` to a new temp file in CHUNK_SIZE pieces, hashing as it goes.

        Never holds the whole file in memory. Past ``max_bytes`` it deletes the temp file and
        raises app.errors.FileTooLarge.
        """
        raise NotImplementedError

    @staticmethod
    def key_for(sha256_hex: str) -> str:
        """'ab/cd/abcd….mp3': two levels of fan-out keep any one folder small."""
        raise NotImplementedError

    def commit(self, staged: StagedFile) -> str:
        """Move the staged file to its key with os.replace (atomic) and return the key.

        If a file is already at that key (same bytes), the temp file is deleted instead.
        """
        raise NotImplementedError

    def discard(self, staged: StagedFile) -> None:
        """Delete the temp file; no error if it is already gone (for example after commit)."""
        raise NotImplementedError

    def path_for(self, key: str) -> Path:
        raise NotImplementedError

    def is_writable(self) -> bool:
        """For the health check."""
        raise NotImplementedError
