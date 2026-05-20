"""Configuration for the sync_media_library job.

This module applies the same factory/value-object pattern as the other jobs.
It also exposes the state-file path used by the checksum cache so already
verified media files can be skipped on later runs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_SOURCE_DIR = Path("/volume1/Torrents")
DEFAULT_DEST_DIR = Path("/volume1/Media")
DEFAULT_LOCK_FILE = Path("/tmp/media.lock")
DEFAULT_LOG_DIR = Path("/volume1/Temp/logs")
DEFAULT_STATE_FILE = DEFAULT_LOG_DIR / "sync_media_library.state.json"
DEFAULT_EXTENSIONS = ("mpg", "avi", "mp4", "mkv")
DEFAULT_CACHE_VALIDATION_MODE = "stat_then_checksum"


@dataclass(frozen=True)
class SyncMediaLibraryConfig:
    """Immutable value object for the media sync workflow."""

    script_name: str
    source_dir: Path
    dest_dir: Path
    lock_file: Path
    log_dir: Path
    state_file: Path
    extensions: tuple[str, ...]
    ffmpeg_threads: int
    cache_validation_mode: str = DEFAULT_CACHE_VALIDATION_MODE

    @property
    def log_file(self) -> Path:
        """Return the per-script log file path."""
        return self.log_dir / f"{self.script_name}.log"


def load_sync_media_library_config() -> SyncMediaLibraryConfig:
    """Factory function that builds the media sync runtime configuration."""
    extensions_raw = os.environ.get("MEDIA_EXTENSIONS")
    # Normalize once at the config boundary so downstream modules can assume
    # lower-cased extension tokens.
    extensions = (
        tuple(part.strip().lower() for part in extensions_raw.split(",") if part.strip())
        if extensions_raw
        else DEFAULT_EXTENSIONS
    )
    cache_validation_mode_raw = os.environ.get(
        "CACHE_VALIDATION_MODE",
        DEFAULT_CACHE_VALIDATION_MODE,
    ).strip().lower()
    cache_validation_mode = (
        cache_validation_mode_raw
        if cache_validation_mode_raw in {"stat_only", "stat_then_checksum"}
        else DEFAULT_CACHE_VALIDATION_MODE
    )

    return SyncMediaLibraryConfig(
        script_name="sync_media_library",
        source_dir=Path(os.environ.get("SOURCE_DIR", str(DEFAULT_SOURCE_DIR))),
        dest_dir=Path(os.environ.get("DEST_DIR", str(DEFAULT_DEST_DIR))),
        lock_file=Path(os.environ.get("LOCK_FILE", str(DEFAULT_LOCK_FILE))),
        log_dir=Path(os.environ.get("LOG_DIR", str(DEFAULT_LOG_DIR))),
        state_file=Path(os.environ.get("STATE_FILE", str(DEFAULT_STATE_FILE))),
        extensions=extensions,
        ffmpeg_threads=int(os.environ.get("FFMPEG_THREADS", "1")),
        cache_validation_mode=cache_validation_mode,
    )
