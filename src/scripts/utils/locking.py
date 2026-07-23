"""Cross-platform file lock helpers.

This is the concurrency-control layer for the jobs. It keeps overlapping cron
or manual runs from stepping on the same inputs, state files, or destination
paths.
"""

from __future__ import annotations

from typing import IO
import os
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class AlreadyLockedError(RuntimeError):
    """Raised when another process already holds the lock."""


class FileLock:
    """A small cross-platform exclusive file lock for job coordination."""

    def __init__(self, lock_path: Path) -> None:
        """Create a file lock that uses ``lock_path`` as the lock file."""
        self.lock_path = lock_path
        self._handle: IO[str] | None = None

    def acquire(self) -> "FileLock":
        """Acquire the lock so the current job becomes the sole active run."""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+")
        try:
            if os.name == "nt":
                # Windows locking requires a byte-range lock from current offset.
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            handle.seek(0)
            handle.truncate()
            handle.write(str(os.getpid()))
            handle.flush()
        except OSError as exc:
            handle.close()
            raise AlreadyLockedError(str(self.lock_path)) from exc
        self._handle = handle
        return self

    def release(self) -> None:
        """Release the coordination lock and close the lock file handle."""
        if self._handle is None:
            return
        try:
            if os.name == "nt":
                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
            else:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> "FileLock":
        """Enter the context manager and acquire the lock."""
        return self.acquire()

    def __exit__(self, _exc_type, exc, _tb) -> None:
        """Exit the context manager and release the lock."""
        self.release()
