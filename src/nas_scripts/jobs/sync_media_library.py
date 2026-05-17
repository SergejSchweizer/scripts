"""Sync media files into the library and keep only English audio/subtitles.

This module is the workflow facade for the media sync feature. It coordinates
copying, stale-file cleanup, and stream filtering while the helper modules
handle the filesystem, ffprobe, and ffmpeg details underneath. The filtering
phase uses a checksum cache so already-verified files can be skipped on later
runs.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

from nas_scripts.config.sync_media_library import (
    SyncMediaLibraryConfig,
    load_sync_media_library_config,
)
from nas_scripts.utils.filesystem import sha256_file
from nas_scripts.utils.locking import AlreadyLockedError, FileLock
from nas_scripts.utils.logging import setup_script_logger
from nas_scripts.utils.media import (
    collect_relative_files,
    collect_relative_media_files,
    copy_file_with_metadata,
    find_non_english_audio_subtitle_streams,
    filter_to_english_audio_and_subtitles,
    probe_streams,
    remove_empty_directories,
    remove_leftover_temp_files,
)
from nas_scripts.utils.state import load_state, save_state

FILTER_POLICY_VERSION = 2


def _build_verified_state_entry(
    *,
    checksum: str,
    size: int,
    mtime_ns: int,
) -> dict[str, Any]:
    """Construct a normalized cache entry for a verified media file."""
    return {
        "sha256": checksum,
        "verified": True,
        "policy_version": FILTER_POLICY_VERSION,
        "size": size,
        "mtime_ns": mtime_ns,
    }


def _is_verified_cache_entry_valid(
    previous: dict[str, Any] | None,
    *,
    current_size: int,
    current_mtime_ns: int,
    current_checksum: str | None = None,
) -> bool:
    """Decide whether a cached verification result still applies."""
    if previous is None:
        return False
    if previous.get("policy_version") != FILTER_POLICY_VERSION:
        return False
    if not previous.get("verified", False):
        return False

    previous_size = previous.get("size")
    previous_mtime_ns = previous.get("mtime_ns")
    if isinstance(previous_size, int) and isinstance(previous_mtime_ns, int):
        return previous_size == current_size and previous_mtime_ns == current_mtime_ns

    if current_checksum is None:
        return False
    return previous.get("sha256") == current_checksum


def _files_are_definitely_equal_by_stat(source_path: Path, dest_path: Path) -> bool:
    """Fast-path equality check based on size and integer-second mtime."""
    source_stat = source_path.stat()
    dest_stat = dest_path.stat()
    return (
        source_stat.st_size == dest_stat.st_size
        and int(source_stat.st_mtime) == int(dest_stat.st_mtime)
    )


def sync_media_files(
    config: SyncMediaLibraryConfig,
    *,
    logger: logging.Logger,
) -> list[Path]:
    """Run the copy-and-prune phase of the media sync facade."""
    source_files = collect_relative_media_files(config.source_dir, config.extensions)
    dest_files = collect_relative_files(config.dest_dir)

    logger.info("Found %s source media file(s).", len(source_files))
    logger.info("Found %s destination file(s).", len(dest_files))

    copied_files: list[Path] = []
    source_set = set(source_files)
    dest_set = set(dest_files)

    for relpath in source_files:
        source_path = config.source_dir / relpath
        dest_path = config.dest_dir / relpath
        if not dest_path.exists():
            copy_file_with_metadata(source_path, dest_path)
            copied_files.append(dest_path)
            logger.info("Copied: %s", relpath)
            continue

        if _files_are_definitely_equal_by_stat(source_path, dest_path):
            continue

        source_checksum = sha256_file(source_path)
        dest_checksum = sha256_file(dest_path)
        if source_checksum != dest_checksum:
            copy_file_with_metadata(source_path, dest_path)
            copied_files.append(dest_path)
            logger.info("Updated changed file: %s", relpath)

    for relpath in sorted(dest_set - source_set):
        full_path = config.dest_dir / relpath
        if full_path.is_file():
            full_path.unlink()
            logger.info("Deleted stale file: %s", relpath)

    for removed_dir in remove_empty_directories(config.dest_dir):
        logger.info("Deleted empty directory: %s", removed_dir)

    return copied_files


def keep_only_english_audio_and_subtitles(
    config: SyncMediaLibraryConfig,
    *,
    logger: logging.Logger,
) -> None:
    """Run the post-copy filtering phase that preserves English tracks."""
    previous_state = load_state(config.state_file)
    next_state: dict[str, dict[str, Any]] = {}
    media_files = collect_relative_media_files(config.dest_dir, config.extensions)
    logger.info(
        "Checking %s media file(s) for non-English audio/subtitle streams.",
        len(media_files),
    )

    for relpath in media_files:
        file_path = config.dest_dir / relpath
        file_stat: os.stat_result = file_path.stat()
        current_size = file_stat.st_size
        current_mtime_ns = file_stat.st_mtime_ns
        previous = previous_state.get(relpath)
        current_checksum: str | None = None
        if _is_verified_cache_entry_valid(
            previous,
            current_size=current_size,
            current_mtime_ns=current_mtime_ns,
        ):
            assert previous is not None
            next_state[relpath] = previous
            logger.info("Skipping already verified file: %s", file_path)
            continue

        if previous is not None and previous.get("policy_version") == FILTER_POLICY_VERSION:
            current_checksum = sha256_file(file_path)
        if previous is not None and _is_verified_cache_entry_valid(
            previous,
            current_size=current_size,
            current_mtime_ns=current_mtime_ns,
            current_checksum=current_checksum,
        ):
            next_state[relpath] = {
                **previous,
                "size": current_size,
                "mtime_ns": current_mtime_ns,
            }
            logger.info("Skipping already verified file: %s", file_path)
            continue

        if (
            previous is not None
            and current_checksum is not None
            and previous.get("sha256") == current_checksum
            and previous.get("policy_version") != FILTER_POLICY_VERSION
        ):
            logger.info(
                "Rechecking %s because the cached verification policy is outdated.",
                file_path,
            )
        try:
            streams = probe_streams(file_path)
        except Exception as exc:  # noqa: BLE001
            logger.exception("ffprobe failed for %s: %s", file_path, exc)
            continue

        non_english_indexes = find_non_english_audio_subtitle_streams(streams)
        if not non_english_indexes:
            if current_checksum is None:
                current_checksum = sha256_file(file_path)
            next_state[relpath] = _build_verified_state_entry(
                checksum=current_checksum,
                size=current_size,
                mtime_ns=current_mtime_ns,
            )
            continue

        logger.info(
            "Filtering %s to remove non-English audio/subtitle streams. First stream: %s",
            file_path,
            non_english_indexes[0],
        )
        if not filter_to_english_audio_and_subtitles(
            file_path,
            ffmpeg_threads=config.ffmpeg_threads,
            logger=logger,
        ):
            logger.error("Failed to process %s", file_path)
            continue

        try:
            updated_streams = probe_streams(file_path)
        except Exception as exc:  # noqa: BLE001
            logger.exception("ffprobe failed while rechecking %s: %s", file_path, exc)
            continue

        remaining_non_english = find_non_english_audio_subtitle_streams(updated_streams)
        if not remaining_non_english:
            updated_stat: os.stat_result = file_path.stat()
            next_state[relpath] = _build_verified_state_entry(
                checksum=sha256_file(file_path),
                size=updated_stat.st_size,
                mtime_ns=updated_stat.st_mtime_ns,
            )
            logger.info("Updated file: %s", file_path)
        else:
            logger.info(
                "Updated file: %s. Remaining non-English stream(s): %s",
                file_path,
                ",".join(str(index) for index in remaining_non_english),
            )

    for temp_file in remove_leftover_temp_files(config.dest_dir):
        logger.info("Removed leftover temp file: %s", temp_file)

    save_state(config.state_file, next_state)


def run_job(config: SyncMediaLibraryConfig, *, logger: logging.Logger) -> int:
    """Run the media sync facade once and return an exit status."""
    if not config.source_dir.exists():
        message = f"Error: source directory does not exist: {config.source_dir}"
        print(message, file=sys.stderr)
        logger.error(message)
        return 1

    if not config.dest_dir.exists():
        message = f"Error: destination directory does not exist: {config.dest_dir}"
        print(message, file=sys.stderr)
        logger.error(message)
        return 1

    logger.info("Starting media sync from %s to %s", config.source_dir, config.dest_dir)
    sync_media_files(config, logger=logger)
    keep_only_english_audio_and_subtitles(config, logger=logger)
    logger.info("Media sync completed.")
    return 0


def main() -> int:
    """Compose the media sync workflow from config, logging, and locking."""
    config = load_sync_media_library_config()
    logger = setup_script_logger(config.script_name, config.log_file)
    logger.info("Starting %s", config.script_name)
    try:
        with FileLock(config.lock_file):
            return run_job(config, logger=logger)
    except AlreadyLockedError:
        print("Another instance is already running. Exiting.")
        logger.warning("Another instance is already running. Exiting.")
        return 0
