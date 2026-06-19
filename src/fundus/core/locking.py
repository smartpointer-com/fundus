"""Cross-process run lock via ``fcntl.flock`` (advisory, non-blocking by default)."""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class AlreadyRunning(RuntimeError):
    """Raised when the lock is already held by another process."""


@contextmanager
def run_lock(path: str | Path, *, blocking: bool = False) -> Iterator[None]:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT, 0o644)
    try:
        flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        try:
            fcntl.flock(fd, flags)
        except BlockingIOError as exc:
            raise AlreadyRunning(str(path)) from exc
        yield
    finally:
        os.close(fd)
