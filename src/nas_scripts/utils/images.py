"""Image-sorting helpers.

This module supports the organizer workflow by classifying files into the
month-based destination tree. In pattern terms, it is the routing layer behind
the organizer's simple file-moving strategy.
"""

from __future__ import annotations

import os
from datetime import datetime
from functools import lru_cache
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


def has_extension(path: Path, extensions: tuple[str, ...]) -> bool:
    """Decide whether a file should enter the organizer routing step."""
    normalized_extensions = _normalized_extensions(extensions)
    return "*" in normalized_extensions or path.suffix.lower().lstrip(".") in normalized_extensions


@lru_cache(maxsize=32)
def _normalized_extensions(extensions: tuple[str, ...]) -> frozenset[str]:
    """Normalize extension tuple once for repeated membership checks."""
    return frozenset(ext.lower() for ext in extensions)


def collect_matching_files(root: Path, extensions: tuple[str, ...]) -> list[Path]:
    """Collect all files that participate in the organizer workflow."""
    matches: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and has_extension(path, extensions):
            matches.append(path)
    return matches


def collect_top_level_matching_files(root: Path, extensions: tuple[str, ...]) -> list[Path]:
    """Collect top-level files for the organizer's default, non-recursive mode."""
    matches: list[Path] = []
    for path in sorted(root.iterdir()):
        if path.is_file() and has_extension(path, extensions):
            matches.append(path)
    return matches


def timestamp_for_path(path: Path) -> datetime:
    """Extract the timestamp used to choose the destination month folder."""
    return datetime.fromtimestamp(path.stat().st_mtime)


def month_folder_name(path: Path) -> str:
    """Return the month bucket used by the organizer's routing strategy."""
    return timestamp_for_path(path).strftime("%Y-%m")


def build_destination_dir(
    path: Path,
    *,
    temp_dir: Path,
    raw_extensions: tuple[str, ...],
    video_extensions: tuple[str, ...],
) -> Path:
    """Map a file to the organizer's `raw/`, `img/`, or `vid/` destination."""
    # Routing priority is explicit: raw first, then video, then generic image bucket.
    destination = temp_dir / month_folder_name(path)
    if has_extension(path, raw_extensions):
        return destination / "raw"
    if has_extension(path, video_extensions):
        return destination / "vid"
    return destination / "img"


def set_path_timestamp_from_source(target: Path, source: Path) -> None:
    """Preserve source timestamps after the file has been moved."""
    stat = source.stat()
    os.utime(target, (stat.st_atime, stat.st_mtime))


def apply_ownership(path: Path, *, owner_user: str | None, owner_group: str | None) -> None:
    """Apply the optional ownership policy used by the organizer workflow."""
    if not owner_user or not owner_group or _pwd is None or _grp is None:
        return
    uid = _pwd.getpwnam(owner_user).pw_uid
    gid = _grp.getgrnam(owner_group).gr_gid
    os.chown(path, uid, gid)
