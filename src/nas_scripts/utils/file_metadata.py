"""Filesystem metadata helpers shared by organizer workflows."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

_grp: Any
_pwd: Any
try:
    import grp as _grp
    import pwd as _pwd
except ImportError:  # pragma: no cover - platform-specific
    _grp = None
    _pwd = None


@dataclass(frozen=True)
class PathTimestamps:
    """Portable timestamp snapshot that can be restored after a move."""

    atime_ns: int
    mtime_ns: int


def capture_path_timestamps(path: Path) -> PathTimestamps:
    """Capture portable file timestamps before a move changes path metadata."""
    stat = path.stat()
    return PathTimestamps(atime_ns=stat.st_atime_ns, mtime_ns=stat.st_mtime_ns)


def apply_path_timestamps(path: Path, timestamps: PathTimestamps) -> None:
    """Restore access and modification timestamps on a moved path."""
    os.utime(path, ns=(timestamps.atime_ns, timestamps.mtime_ns))


def set_path_timestamp_from_source(target: Path, source: Path) -> None:
    """Preserve source timestamps on a target path."""
    apply_path_timestamps(target, capture_path_timestamps(source))


def apply_ownership(path: Path, *, owner_user: str | None, owner_group: str | None) -> None:
    """Apply the optional ownership policy used by the organizer workflow."""
    if not owner_user or not owner_group or _pwd is None or _grp is None:
        return
    uid = _pwd.getpwnam(owner_user).pw_uid
    gid = _grp.getgrnam(owner_group).gr_gid
    os.chown(path, uid, gid)