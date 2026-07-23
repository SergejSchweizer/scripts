"""Configuration for the organize_temp_media job.

The organizer sorts temporary photos and videos into month-based folders and
splits them into `raw/`, `img/`, and `vid/` subdirectories.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from scripts.config.env import env_bool, env_choice, env_csv, env_path


DEFAULT_TEMP_DIR = Path("/volume1/Temp/Fotos")
DEFAULT_DOWNLOADS_TEMP_DIR = Path("/volume1/Temp/Downloads")
DEFAULT_LOCK_FILE = Path("/tmp/organize_temp_media.lock")
DEFAULT_DOWNLOADS_LOCK_FILE = Path("/tmp/organize_temp_downloads.lock")
DEFAULT_LOG_DIR = Path(".logs")
DEFAULT_CONFLICT_POLICY = "overwrite"
DEFAULT_DESTINATION_LAYOUT = "categorized"
DEFAULT_DOWNLOADS_DESTINATION_LAYOUT = "month_only"
DEFAULT_DOWNLOADS_FILE_EXTENSIONS = ("*",)
DEFAULT_FILE_EXTENSIONS = (
    "arw",
    "mov",
    "mp4",
    "jpg",
    "jpeg",
    "png",
    "avi",
    "gif",
    "bmp",
    "heic",
    "3gp",
)
DEFAULT_RAW_EXTENSIONS = ("arw",)
DEFAULT_VIDEO_EXTENSIONS = ("mov", "mp4", "avi", "3gp")


@dataclass(frozen=True)
class OrganizeTempMediaConfig:
    """Runtime settings for temporary media organization."""

    script_name: str
    temp_dir: Path
    lock_file: Path
    log_dir: Path
    reorganize_existing: bool
    file_extensions: tuple[str, ...]
    raw_extensions: tuple[str, ...]
    video_extensions: tuple[str, ...]
    owner_user: str | None
    owner_group: str | None
    conflict_policy: str
    destination_layout: str = DEFAULT_DESTINATION_LAYOUT

    @property
    def log_file(self) -> Path:
        """Return the per-script log file path."""
        return self.log_dir / f"{self.script_name}.log"


def _parse_csv_env(value: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    """Parse a comma-separated environment variable into a tuple."""
    return env_csv(value, default)


def _parse_conflict_policy(value: str | None) -> str:
    """Parse and validate conflict handling policy."""
    return env_choice(
        value,
        choices={"overwrite", "skip", "rename"},
        default=DEFAULT_CONFLICT_POLICY,
    )


def _parse_bool_env(value: str | None, default: bool = False) -> bool:
    """Parse a permissive boolean environment variable."""
    return env_bool(value, default=default)


def _load_organize_temp_config(
    *,
    script_name: str,
    default_temp_dir: Path,
    default_lock_file: Path,
    default_file_extensions: tuple[str, ...],
    destination_layout: str,
) -> OrganizeTempMediaConfig:
    """Load shared organizer settings from environment variables."""
    return OrganizeTempMediaConfig(
        script_name=script_name,
        temp_dir=env_path(os.environ.get("TEMP_DIR"), default_temp_dir),
        lock_file=env_path(os.environ.get("LOCK_FILE"), default_lock_file),
        log_dir=DEFAULT_LOG_DIR,
        reorganize_existing=_parse_bool_env(os.environ.get("REORGANIZE_EXISTING")),
        file_extensions=_parse_csv_env(
            os.environ.get("FILE_EXTENSIONS"),
            default_file_extensions,
        ),
        raw_extensions=_parse_csv_env(
            os.environ.get("RAW_EXTENSIONS"),
            DEFAULT_RAW_EXTENSIONS,
        ),
        video_extensions=_parse_csv_env(
            os.environ.get("VIDEO_EXTENSIONS"),
            DEFAULT_VIDEO_EXTENSIONS,
        ),
        owner_user=os.environ.get("OWNER_USER") or None,
        owner_group=os.environ.get("OWNER_GROUP") or None,
        conflict_policy=_parse_conflict_policy(os.environ.get("CONFLICT_POLICY")),
        destination_layout=destination_layout,
    )


def load_organize_temp_media_config() -> OrganizeTempMediaConfig:
    """Load photo organizer settings from environment variables."""
    return _load_organize_temp_config(
        script_name="organize_temp_media",
        default_temp_dir=DEFAULT_TEMP_DIR,
        default_lock_file=DEFAULT_LOCK_FILE,
        default_file_extensions=DEFAULT_FILE_EXTENSIONS,
        destination_layout=DEFAULT_DESTINATION_LAYOUT,
    )


def load_organize_temp_downloads_config() -> OrganizeTempMediaConfig:
    """Load downloads organizer settings from environment variables."""
    return _load_organize_temp_config(
        script_name="organize_temp_downloads",
        default_temp_dir=DEFAULT_DOWNLOADS_TEMP_DIR,
        default_lock_file=DEFAULT_DOWNLOADS_LOCK_FILE,
        default_file_extensions=DEFAULT_DOWNLOADS_FILE_EXTENSIONS,
        destination_layout=DEFAULT_DOWNLOADS_DESTINATION_LAYOUT,
    )
