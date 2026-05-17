"""Organize temporary image and media files into dated folders.

This module is the workflow facade for the organizer feature. It coordinates
file discovery, routing, move operations, and optional ownership updates while
the helper modules keep those lower-level concerns isolated.
"""

from __future__ import annotations

from dataclasses import replace
import logging
from pathlib import Path
import shutil
import sys
from typing import Protocol

from nas_scripts.config.organize_temp_media import (
    OrganizeTempMediaConfig,
    load_organize_temp_media_config,
)
from nas_scripts.utils.images import (
    apply_ownership,
    build_destination_dir,
    collect_matching_files,
    collect_top_level_matching_files,
    set_path_timestamp_from_source,
)
from nas_scripts.utils.locking import AlreadyLockedError, FileLock
from nas_scripts.utils.logging import setup_script_logger


class ConflictResolver(Protocol):
    """Strategy interface for destination conflict handling."""

    def resolve(self, destination_path: Path, *, logger: logging.Logger) -> Path | None:
        """Return resolved destination path, or None to skip this source file."""


class OverwriteConflictResolver:
    """Conflict strategy that overwrites existing destination files."""

    def resolve(self, destination_path: Path, *, logger: logging.Logger) -> Path:
        destination_path.unlink()
        logger.info("Overwriting existing file: %s", destination_path)
        return destination_path


class SkipConflictResolver:
    """Conflict strategy that keeps the existing destination and skips moving."""

    def resolve(self, destination_path: Path, *, logger: logging.Logger) -> None:
        logger.info("Skipping file because destination exists: %s", destination_path)
        return None


class RenameConflictResolver:
    """Conflict strategy that keeps existing destination and picks a new file name."""

    def resolve(self, destination_path: Path, *, logger: logging.Logger) -> Path:
        stem = destination_path.stem
        suffix = destination_path.suffix
        counter = 1
        while True:
            candidate = destination_path.with_name(f"{stem}.{counter}{suffix}")
            if not candidate.exists():
                logger.info(
                    "Destination exists for %s; renaming to %s",
                    destination_path,
                    candidate,
                )
                return candidate
            counter += 1


def _build_conflict_resolver(policy: str) -> ConflictResolver:
    """Factory for conflict-handling strategy selection."""
    if policy == "skip":
        return SkipConflictResolver()
    if policy == "rename":
        return RenameConflictResolver()
    return OverwriteConflictResolver()


def organize_files(config: OrganizeTempMediaConfig, *, logger: logging.Logger) -> int:
    """Run the organizer facade once and return an exit status."""
    if not config.temp_dir.exists():
        message = f"Error: temp directory does not exist: {config.temp_dir}"
        print(message, file=sys.stderr)
        logger.error(message)
        return 1

    file_collector = (
        collect_matching_files
        if config.reorganize_existing
        else collect_top_level_matching_files
    )
    files = file_collector(config.temp_dir, config.file_extensions)
    conflict_resolver = _build_conflict_resolver(config.conflict_policy)
    logger.info("Found %s matching file(s) in %s", len(files), config.temp_dir)
    if not files:
        logger.info("No matching files found. Nothing to move.")
        logger.info("Organization completed.")
        return 0

    for source_path in files:
        destination_dir = build_destination_dir(
            source_path,
            temp_dir=config.temp_dir,
            raw_extensions=config.raw_extensions,
            video_extensions=config.video_extensions,
        )
        destination_path = destination_dir / source_path.name

        if source_path == destination_path:
            logger.info("Skipping already organized file: %s", source_path)
            continue

        destination_dir.mkdir(parents=True, exist_ok=True)
        if destination_path.exists():
            if destination_path.is_dir():
                message = f"Cannot overwrite directory with file: {destination_path}"
                print(message, file=sys.stderr)
                logger.error(message)
                return 1
            resolved_destination = conflict_resolver.resolve(destination_path, logger=logger)
            if resolved_destination is None:
                continue
            destination_path = resolved_destination

        shutil.move(str(source_path), str(destination_path))

        set_path_timestamp_from_source(destination_dir, destination_path)
        set_path_timestamp_from_source(destination_path, destination_path)

        try:
            apply_ownership(
                destination_dir,
                owner_user=config.owner_user,
                owner_group=config.owner_group,
            )
            apply_ownership(
                destination_path,
                owner_user=config.owner_user,
                owner_group=config.owner_group,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to apply ownership to %s: %s", destination_path, exc)

        logger.info("Moved %s to %s", source_path, destination_path)

    logger.info("Organization completed.")
    return 0


def main(*, reorganize_existing: bool | None = None) -> int:
    """Compose the organizer workflow from config, logging, and locking."""
    config = load_organize_temp_media_config()
    if reorganize_existing is not None:
        config = replace(config, reorganize_existing=reorganize_existing)
    logger = setup_script_logger(config.script_name, config.log_file)
    logger.info("Starting %s", config.script_name)
    try:
        with FileLock(config.lock_file):
            return organize_files(config, logger=logger)
    except AlreadyLockedError:
        print("Another instance is already running. Exiting.")
        logger.warning("Another instance is already running. Exiting.")
        return 0
