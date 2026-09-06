"""Small fsync-safe filesystem primitives used by trusted host workflows."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from uuid import uuid4


def read_bounded_regular(path: Path, *, maximum_bytes: int = 1024 * 1024) -> bytes:
    metadata = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"{path} must be a regular non-symlink file")
    if not 1 <= metadata.st_size <= maximum_bytes:
        raise ValueError(f"{path} exceeds its size contract")
    with path.open("rb") as handle:
        content = handle.read(maximum_bytes + 1)
    if len(content) > maximum_bytes:
        raise ValueError(f"{path} exceeds its size contract")
    return content


def atomic_write(path: Path, content: bytes, *, mode: int = 0o640) -> None:
    """Publish one file as write, fsync, rename and directory fsync."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4()}.tmp")
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            mode,
        )
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            temporary.chmod(mode)
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def durable_unlink(path: Path) -> None:
    path.unlink()
    fsync_directory(path.parent)


def fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
