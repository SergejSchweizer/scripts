"""Compatibility exports for organizer path and metadata helpers."""

from __future__ import annotations

from nas_scripts.utils.extensions import has_extension
from nas_scripts.utils.file_metadata import (
    PathTimestamps,
    apply_ownership,
    apply_path_timestamps,
    capture_path_timestamps,
    set_path_timestamp_from_source,
)
from nas_scripts.utils.organizer_paths import (
    build_destination_dir,
    collect_matching_files,
    collect_top_level_matching_files,
    collect_top_level_matching_items,
    is_month_folder_name,
    month_folder_name,
    timestamp_for_path,
)

__all__ = [
    "PathTimestamps",
    "apply_ownership",
    "apply_path_timestamps",
    "build_destination_dir",
    "capture_path_timestamps",
    "collect_matching_files",
    "collect_top_level_matching_files",
    "collect_top_level_matching_items",
    "has_extension",
    "is_month_folder_name",
    "month_folder_name",
    "set_path_timestamp_from_source",
    "timestamp_for_path",
]
