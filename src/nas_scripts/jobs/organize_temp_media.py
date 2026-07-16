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
from typing import Callable, Protocol

from nas_scripts.config.organize_temp_media import (
    OrganizeTempMediaConfig,
    load_organize_temp_media_config,
)
from nas_scripts.utils.images import (
    apply_ownership,
    build_destination_dir,
    collect_matching_files,
    collect_top_level_matching_files,
    month_folder_name,
    set_path_timestamp_from_source,
)
from nas_scripts.utils.job import run_locked_job


ConfigLoader = Callable[[], OrganizeTempMediaConfig]


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
    # Policy defaults to overwrite; parser already normalizes unknown values.
    if policy == "skip":
        return SkipConflictResolver()
    if policy == "rename":
        return RenameConflictResolver()
    return OverwriteConflictResolver()


def _build_destination_dir(source_path: Path, config: OrganizeTempMediaConfig) -> Path:
    """Resolve the destination directory for the configured organizer layout."""
    if config.destination_layout == "month_only":
        return config.temp_dir / month_folder_name(source_path)
    return build_destination_dir(
        source_path,
        temp_dir=config.temp_dir,
        raw_extensions=config.raw_extensions,
        video_extensions=config.video_extensions,
    )


def organize_files(config: OrganizeTempMediaConfig, *, logger: logging.Logger) -> int:
    """Run the organizer facade once and return an exit status."""
    if not config.temp_dir.exists():
        message = f"Error: temp directory does not exist: {config.temp_dir}"
        logger.error(message)
        return 1

    # Default mode only scans top-level files; optional mode reprocesses nested layout.
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
        destination_dir = _build_destination_dir(source_path, config)
        destination_path = destination_dir / source_path.name

        if source_path == destination_path:
            logger.info("Skipping already organized file: %s", source_path)
            continue

        destination_dir.mkdir(parents=True, exist_ok=True)
        if destination_path.exists():
            if destination_path.is_dir():
                message = f"Cannot overwrite directory with file: {destination_path}"
                logger.error(message)
                return 1
            # Conflict policy is centralized behind a strategy to keep workflow linear.
            resolved_destination = conflict_resolver.resolve(destination_path, logger=logger)
            if resolved_destination is None:
                # Skip policy returns None to signal a deliberate no-op.
                continue
            destination_path = resolved_destination

        # shutil.move handles cross-device moves by falling back to copy+unlink.
        shutil.move(str(source_path), str(destination_path))

        # Keep destination folder/file timestamps aligned with moved file metadata.
        set_path_timestamp_from_source(destination_dir, destination_path)
        set_path_timestamp_from_source(destination_path, destination_path)

        # Ownership update is best-effort; move should remain successful if chown fails.
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


def run_organizer(
    config_loader: ConfigLoader,
    *,
    reorganize_existing: bool | None = None,
) -> int:
    """Compose an organizer workflow from config, logging, and locking."""
    config = config_loader()
    if reorganize_existing is not None:
        config = replace(config, reorganize_existing=reorganize_existing)
    return run_locked_job(config, lambda logger: organize_files(config, logger=logger))


def main(*, reorganize_existing: bool | None = None) -> int:
    """Run the temporary photo organizer workflow."""
    return run_organizer(
        load_organize_temp_media_config,
        reorganize_existing=reorganize_existing,
    )
