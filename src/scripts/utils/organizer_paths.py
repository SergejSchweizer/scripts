"""Path discovery and routing helpers for organizer jobs."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re

from scripts.utils.extensions import has_extension


def collect_matching_files(root: Path, extensions: tuple[str, ...]) -> list[Path]:
    """Collect all files that participate in the organizer workflow."""
    matches: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and has_extension(path, extensions, allow_wildcard=True):
            matches.append(path)
    return matches


def collect_top_level_matching_files(root: Path, extensions: tuple[str, ...]) -> list[Path]:
    """Collect top-level files for the organizer's default, non-recursive mode."""
    matches: list[Path] = []
    for path in sorted(root.iterdir()):
        if path.is_file() and has_extension(path, extensions, allow_wildcard=True):
            matches.append(path)
    return matches


def collect_top_level_matching_items(root: Path, extensions: tuple[str, ...]) -> list[Path]:
    """Collect top-level files and unsorted directories for month-only organization."""
    matches: list[Path] = []
    for path in sorted(root.iterdir()):
        if path.is_file() and has_extension(path, extensions, allow_wildcard=True):
            matches.append(path)
        elif path.is_dir() and not is_month_folder_name(path.name):
            matches.append(path)
    return matches


def is_month_folder_name(name: str) -> bool:
    """Return whether a directory name is already a YYYY-MM month bucket."""
    return re.fullmatch(r"\d{4}-\d{2}", name) is not None


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
    destination = temp_dir / month_folder_name(path)
    if has_extension(path, raw_extensions):
        return destination / "raw"
    if has_extension(path, video_extensions):
        return destination / "vid"
    return destination / "img"
