"""Configuration for the organize_temp_media job.

The organizer sorts temporary photos and videos into month-based folders and
splits them into `raw/`, `img/`, and `vid/` subdirectories.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_TEMP_DIR = Path("/volume1/Temp/Fotos")
DEFAULT_LOCK_FILE = Path("/tmp/organize_temp_media.lock")
DEFAULT_LOG_DIR = Path("/volume1/Temp/logs")
DEFAULT_CONFLICT_POLICY = "overwrite"
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

    @property
    def log_file(self) -> Path:
        """Return the per-script log file path."""
        return self.log_dir / f"{self.script_name}.log"


def _parse_csv_env(value: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    """Parse a comma-separated environment variable into a tuple."""
    if not value:
        return default
    parts = [part.strip().lower() for part in value.split(",") if part.strip()]
    return tuple(parts) if parts else default


def _parse_conflict_policy(value: str | None) -> str:
    """Parse and validate conflict handling policy."""
    if value is None:
        return DEFAULT_CONFLICT_POLICY
    normalized = value.strip().lower()
    if normalized in {"overwrite", "skip", "rename"}:
        return normalized
    # Fall back to the default policy rather than failing startup for a bad env var.
    return DEFAULT_CONFLICT_POLICY


def _parse_bool_env(value: str | None, default: bool = False) -> bool:
    """Parse a permissive boolean environment variable."""
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    # Unknown tokens keep the caller-provided default for stable behavior.
    return default


def load_organize_temp_media_config() -> OrganizeTempMediaConfig:
    """Load organizer settings from environment variables."""
    return OrganizeTempMediaConfig(
        script_name="organize_temp_media",
        temp_dir=Path(os.environ.get("TEMP_DIR", str(DEFAULT_TEMP_DIR))),
        lock_file=Path(os.environ.get("LOCK_FILE", str(DEFAULT_LOCK_FILE))),
        log_dir=Path(os.environ.get("LOG_DIR", str(DEFAULT_LOG_DIR))),
        reorganize_existing=_parse_bool_env(os.environ.get("REORGANIZE_EXISTING")),
        file_extensions=_parse_csv_env(
            os.environ.get("FILE_EXTENSIONS"),
            DEFAULT_FILE_EXTENSIONS,
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
    )
