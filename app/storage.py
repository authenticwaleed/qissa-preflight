"""Local file storage, content-addressed: every file lives under its own SHA-256.

The same bytes always map to the same path, so a duplicate never takes extra disk space, and the
client's filename never touches the filesystem.
"""

import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from app.errors import FileTooLarge

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
        self.root = root
        # Inside root rather than the system temp folder: os.replace is only atomic within one
        # filesystem, and root may be a mounted volume.
        self.tmp_dir = root / "tmp"
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

    def stage(self, stream: BinaryIO, max_bytes: int) -> StagedFile:
        """Copy ``stream`` to a new temp file in CHUNK_SIZE pieces, hashing as it goes.

        Never holds the whole file in memory. Past ``max_bytes`` it deletes the temp file and
        raises app.errors.FileTooLarge.
        """
        # A staging folder removed at runtime (by a cleanup job, say) comes back. A missing root
        # does not: that is a lost volume, which the health check should report.
        self.tmp_dir.mkdir(exist_ok=True)
        path = self.tmp_dir / f"{uuid.uuid4().hex}.part"
        digest = hashlib.sha256()
        size = 0
        try:
            with path.open("wb") as out:
                while chunk := stream.read(CHUNK_SIZE):
                    size += len(chunk)
                    if size > max_bytes:
                        # Stop at the first chunk past the limit rather than reading the rest.
                        raise FileTooLarge(
                            f"The file is larger than the {max_bytes / 1_000_000:g} MB limit."
                        )
                    digest.update(chunk)
                    out.write(chunk)
        except BaseException:
            # Any failure, a client disconnecting mid-upload included, leaves no temp file behind.
            path.unlink(missing_ok=True)
            raise
        return StagedFile(path=path, sha256=digest.digest(), size_bytes=size)

    @staticmethod
    def key_for(sha256_hex: str) -> str:
        """'ab/cd/abcd….mp3': two levels of fan-out keep any one folder small."""
        return f"{sha256_hex[:2]}/{sha256_hex[2:4]}/{sha256_hex}.mp3"

    def commit(self, staged: StagedFile) -> str:
        """Move the staged file to its key with os.replace (atomic) and return the key.

        If a file is already at that key (same bytes), the temp file is deleted instead.
        """
        key = self.key_for(staged.sha256_hex)
        target = self.path_for(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            # Already stored, for example by an upload whose database transaction failed after
            # its file was moved here.
            staged.path.unlink(missing_ok=True)
        else:
            # The database row pointing at this key is committed next, so the bytes and the
            # rename must reach the disk first: os.replace alone makes the switch atomic, not
            # durable.
            _fsync(staged.path)
            os.replace(staged.path, target)
            _fsync(target.parent)
        return key

    def discard(self, staged: StagedFile) -> None:
        """Delete the temp file; no error if it is already gone (for example after commit)."""
        staged.path.unlink(missing_ok=True)

    def path_for(self, key: str) -> Path:
        return self.root / key

    def is_writable(self) -> bool:
        """For the health check."""
        return self.root.is_dir() and os.access(self.root, os.W_OK)


def _fsync(path: Path) -> None:
    """Flush a file's data, or a folder's entries, to disk."""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
