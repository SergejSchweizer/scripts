"""Configuration for the sync_media_library job.

This module applies the same factory/value-object pattern as the other jobs.
It also exposes the state-file path used by the checksum cache so already
verified media files can be skipped on later runs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from scripts.config.env import env_choice, env_csv, env_int, env_path


DEFAULT_SOURCE_DIR = Path("/volume1/Torrents")
DEFAULT_DEST_DIR = Path("/volume1/Media")
DEFAULT_LOCK_FILE = Path("/tmp/media.lock")
DEFAULT_LOG_DIR = Path(".logs")
DEFAULT_STATE_FILE = Path("/volume1/Temp/.logs/sync_media_library.state.json")
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
    return SyncMediaLibraryConfig(
        script_name="sync_media_library",
        source_dir=env_path(os.environ.get("SOURCE_DIR"), DEFAULT_SOURCE_DIR),
        dest_dir=env_path(os.environ.get("DEST_DIR"), DEFAULT_DEST_DIR),
        lock_file=env_path(os.environ.get("LOCK_FILE"), DEFAULT_LOCK_FILE),
        log_dir=DEFAULT_LOG_DIR,
        state_file=env_path(os.environ.get("STATE_FILE"), DEFAULT_STATE_FILE),
        extensions=env_csv(os.environ.get("MEDIA_EXTENSIONS"), DEFAULT_EXTENSIONS),
        ffmpeg_threads=env_int(os.environ.get("FFMPEG_THREADS"), default=1),
        cache_validation_mode=env_choice(
            os.environ.get("CACHE_VALIDATION_MODE"),
            choices={"stat_only", "stat_then_checksum"},
            default=DEFAULT_CACHE_VALIDATION_MODE,
        ),
    )
