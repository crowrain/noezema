"""Bounded cross-process lock for short host-journal critical sections."""

from __future__ import annotations

import os
import random
import stat
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path


class HostTransitionLockTimeoutError(TimeoutError):
    """The stable host-transition lock was not acquired before its deadline."""


class HostTransitionLock:
    def __init__(
        self,
        path: Path,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = lambda: random.uniform(0.9, 1.1),
    ) -> None:
        self._path = path
        self._monotonic = monotonic
        self._sleep = sleeper
        self._jitter = jitter

    @contextmanager
    def acquire(self, *, deadline_seconds: float = 2.0) -> Iterator[None]:
        if not 0 < deadline_seconds <= 60:
            raise ValueError("host lock deadline must be between 0 and 60 seconds")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
        if os.name != "nt":
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self._path, flags, 0o660)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            os.close(descriptor)
            raise OSError("host transition lock must be a regular file")
        if os.name != "nt":
            os.fchmod(descriptor, 0o660)
        handle = os.fdopen(descriptor, "a+b")
        acquired = False
        try:
            deadline = self._monotonic() + deadline_seconds
            delay = 0.05
            while True:
                acquired = _try_lock(handle)
                if acquired:
                    break
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    raise HostTransitionLockTimeoutError("host transition lock deadline expired")
                self._sleep(min(remaining, delay * self._jitter()))
                delay = min(0.25, delay * 2)
            yield
        finally:
            if acquired:
                _unlock(handle)
            handle.close()


def _try_lock(handle: object) -> bool:
    if os.name == "nt":
        import msvcrt

        file = handle
        file.seek(0, os.SEEK_END)  # type: ignore[attr-defined]
        if file.tell() == 0:  # type: ignore[attr-defined]
            file.write(b"\0")  # type: ignore[attr-defined]
            file.flush()  # type: ignore[attr-defined]
        file.seek(0)  # type: ignore[attr-defined]
        try:
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
        except OSError:
            return False
        return True

    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]
    except BlockingIOError:
        return False
    return True


def _unlock(handle: object) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)  # type: ignore[attr-defined]
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]
