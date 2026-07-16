"""Shared unit-test factories."""

from __future__ import annotations

from pathlib import Path

from nas_scripts.config.organize_temp_media import OrganizeTempMediaConfig
from nas_scripts.config.sync_media_library import SyncMediaLibraryConfig


def make_organize_config(tmp_path: Path) -> OrganizeTempMediaConfig:
    """Build a deterministic organizer config for unit tests."""
    return OrganizeTempMediaConfig(
        script_name="organize_temp_media",
        temp_dir=tmp_path / "temp",
        lock_file=tmp_path / "organize_temp_media.lock",
        log_dir=tmp_path / ".logs",
        reorganize_existing=False,
        file_extensions=("jpg", "JPG", "arw", "ARW", "mp4", "MP4"),
        raw_extensions=("arw", "ARW"),
        video_extensions=("mp4", "MP4"),
        owner_user=None,
        owner_group=None,
        conflict_policy="overwrite",
    )


def make_sync_config(tmp_path: Path) -> SyncMediaLibraryConfig:
    """Build a deterministic sync-media config for unit tests."""
    return SyncMediaLibraryConfig(
        script_name="sync_media_library",
        source_dir=tmp_path / "source",
        dest_dir=tmp_path / "dest",
        lock_file=tmp_path / "media.lock",
        log_dir=tmp_path / ".logs",
        state_file=tmp_path / ".logs" / "sync_media_library.state.json",
        extensions=("mpg", "avi", "mp4", "mkv"),
        ffmpeg_threads=1,
    )