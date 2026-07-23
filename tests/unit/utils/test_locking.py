from __future__ import annotations

from pathlib import Path

import pytest

from scripts.utils.locking import AlreadyLockedError, FileLock


def test_file_lock_raises_when_underlying_lock_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lock = FileLock(tmp_path / "x.lock")

    def raise_oserror(*args, **_kwargs):  # type: ignore[no-untyped-def]
        raise OSError("busy")

    monkeypatch.setattr("scripts.utils.locking.fcntl.flock", raise_oserror)
    with pytest.raises(AlreadyLockedError):
        lock.acquire()


def test_file_lock_release_without_acquire_is_noop(tmp_path: Path) -> None:
    lock = FileLock(tmp_path / "x.lock")
    lock.release()
