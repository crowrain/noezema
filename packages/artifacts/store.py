"""Content-addressed artifact store (T2.11, §5.11, ADR-0002).

Filesystem layout: ``<root>/<xx>/<sha256>`` (first two hex chars as
subdirectory). Objects are immutable: put is crash-safe (temp file →
fsync → atomic rename), idempotent for the same hash, and a hash mismatch
is an error. Metadata (provenance, trust class, chunk-level origin) lives
in the domain DB, not on disk.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class ArtifactStore(Protocol):
    """Transport-swappable contract (ADR-0002): put/get/exists/head/remove."""

    def put(self, data: bytes, *, origin: str, trust_class: str, mime: str | None = None) -> str: ...

    def get(self, sha256: str) -> bytes: ...

    def exists(self, sha256: str) -> bool: ...

    def head(self, sha256: str) -> ArtifactHead | None: ...

    def remove(self, sha256: str) -> bool:
        """Delete one object (T7.3 GC, §15.3); True if it was present."""
        ...


@dataclass(frozen=True)
class ArtifactHead:
    sha256: str
    size: int


class ArtifactStoreError(RuntimeError):
    pass


class FilesystemArtifactStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, sha256: str) -> Path:
        if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
            raise ArtifactStoreError(f"invalid sha256: {sha256!r}")
        return self.root / sha256[:2] / sha256

    def put(self, data: bytes, *, origin: str, trust_class: str, mime: str | None = None) -> str:
        """Store bytes; returns the SHA-256. Idempotent no-op on duplicate."""
        sha = hashlib.sha256(data).hexdigest()
        path = self._path(sha)
        if path.exists():
            if path.stat().st_size != len(data):
                raise ArtifactStoreError(f"hash collision at {sha}")
            return sha  # immutable object already present

        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, str(path))
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise
        return sha

    def get(self, sha256: str) -> bytes:
        path = self._path(sha256)
        if not path.exists():
            raise ArtifactStoreError(f"artifact not found: {sha256}")
        return path.read_bytes()

    def exists(self, sha256: str) -> bool:
        try:
            return self._path(sha256).exists()
        except ArtifactStoreError:
            return False

    def head(self, sha256: str) -> ArtifactHead | None:
        try:
            path = self._path(sha256)
        except ArtifactStoreError:
            return None
        if not path.exists():
            return None
        return ArtifactHead(sha256=sha256, size=path.stat().st_size)

    def remove(self, sha256: str) -> bool:
        """Delete one object (T7.3 GC). Returns True if it was present."""
        path = self._path(sha256)
        if not path.exists():
            return False
        path.unlink()
        # fsync the parent directory (the unlink is durable only after)
        parent_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        return True

    def remove_all(self) -> None:
        """Test helper: wipe the store root."""
        shutil.rmtree(self.root, ignore_errors=True)
