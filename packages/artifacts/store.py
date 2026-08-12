"""Crash-safe immutable blob storage with content-derived locations."""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from packages.domain import ActionId


@dataclass(frozen=True, slots=True)
class StagedBlob:
    sha256: str
    size_bytes: int
    storage_key: str


class ContentAddressedArtifactStore:
    """Store bytes once; database metadata controls whether they are visible."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve(strict=False)

    def stage_text(self, *, action_id: ActionId, content: str) -> StagedBlob:
        return self.stage_bytes(action_id=action_id, content=content.encode("utf-8"))

    def stage_bytes(self, *, action_id: ActionId, content: bytes) -> StagedBlob:
        digest = hashlib.sha256(content).hexdigest()
        storage_key = f"blobs/{digest[:2]}/{digest}"
        destination = self._storage_path(storage_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = self.root / "staging" / str(action_id)
        staging.mkdir(parents=True, exist_ok=True)

        if destination.exists():
            self._verify_path(destination, digest=digest, size_bytes=len(content))
            return StagedBlob(digest, len(content), storage_key)

        descriptor, temporary_name = tempfile.mkstemp(prefix=f"{digest}.", dir=staging)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            if destination.exists():
                self._verify_path(destination, digest=digest, size_bytes=len(content))
                temporary.unlink(missing_ok=True)
            else:
                os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
            try:
                staging.rmdir()
            except OSError:
                pass

        self._verify_path(destination, digest=digest, size_bytes=len(content))
        return StagedBlob(digest, len(content), storage_key)

    def verify(self, *, storage_key: str, digest: str, size_bytes: int) -> None:
        expected_key = f"blobs/{digest[:2]}/{digest}"
        if storage_key != expected_key:
            raise ValueError("blob storage key does not match its digest")
        self._verify_path(
            self._storage_path(storage_key),
            digest=digest,
            size_bytes=size_bytes,
        )

    def read(self, *, digest: str, max_bytes: int) -> bytes:
        storage_key = f"blobs/{digest[:2]}/{digest}"
        path = self._storage_path(storage_key)
        try:
            size = path.stat().st_size
            if size > max_bytes:
                raise ValueError("blob exceeds the requested read limit")
            content = path.read_bytes()
        except OSError as exc:
            raise ValueError("blob is unavailable") from exc
        self._verify_bytes(content, digest=digest, size_bytes=size)
        return content

    def _storage_path(self, storage_key: str) -> Path:
        key = PurePosixPath(storage_key)
        if key.is_absolute() or ".." in key.parts:
            raise ValueError("invalid content-addressed storage key")
        candidate = self.root.joinpath(*key.parts).resolve(strict=False)
        if not candidate.is_relative_to(self.root):
            raise ValueError("content-addressed storage key escapes its root")
        return candidate

    @classmethod
    def _verify_path(cls, path: Path, *, digest: str, size_bytes: int) -> None:
        try:
            if not path.is_file() or path.is_symlink():
                raise ValueError("content-addressed blob is not a regular file")
            content = path.read_bytes()
        except OSError as exc:
            raise ValueError("content-addressed blob is unavailable") from exc
        cls._verify_bytes(content, digest=digest, size_bytes=size_bytes)

    @staticmethod
    def _verify_bytes(content: bytes, *, digest: str, size_bytes: int) -> None:
        if len(content) != size_bytes or hashlib.sha256(content).hexdigest() != digest:
            raise ValueError("content-addressed blob failed integrity verification")
