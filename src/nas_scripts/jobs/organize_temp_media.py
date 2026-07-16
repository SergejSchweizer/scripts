"""Organize temporary image and media files into dated folders.

This module is the workflow facade for the organizer feature. It coordinates
file discovery, routing, move operations, and optional ownership updates while
the helper modules keep those lower-level concerns isolated.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
import logging
from pathlib import Path
import shutil
from typing import Callable, Protocol

from nas_scripts.config.organize_temp_media import (
    OrganizeTempMediaConfig,
    load_organize_temp_media_config,
)
from nas_scripts.utils.file_metadata import (
    apply_ownership,
    apply_path_timestamps,
    capture_path_timestamps,
    PathTimestamps,
)
from nas_scripts.utils.organizer_paths import (
    build_destination_dir,
    collect_matching_files,
    collect_top_level_matching_files,
    collect_top_level_matching_items,
    month_folder_name,
)
from nas_scripts.utils.job import run_locked_job


ConfigLoader = Callable[[], OrganizeTempMediaConfig]


@dataclass(frozen=True)
class MovePlan:
    """Resolved organizer move operation with metadata captured before mutation."""

    source_path: Path
    destination_dir: Path
    destination_path: Path
    source_timestamps: PathTimestamps


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


def _collect_items(config: OrganizeTempMediaConfig) -> list[Path]:
    """Collect source items according to the configured organizer mode."""
    if config.reorganize_existing:
        return collect_matching_files(config.temp_dir, config.file_extensions)
    if config.destination_layout == "month_only":
        return collect_top_level_matching_items(config.temp_dir, config.file_extensions)
    return collect_top_level_matching_files(config.temp_dir, config.file_extensions)


def _build_move_plan(source_path: Path, config: OrganizeTempMediaConfig) -> MovePlan:
    """Resolve a source item into a destination and capture metadata first."""
    destination_dir = _build_destination_dir(source_path, config)
    return MovePlan(
        source_path=source_path,
        destination_dir=destination_dir,
        destination_path=destination_dir / source_path.name,
        source_timestamps=capture_path_timestamps(source_path),
    )


def _resolve_existing_destination(
    plan: MovePlan,
    *,
    config: OrganizeTempMediaConfig,
    conflict_resolver: ConflictResolver,
    logger: logging.Logger,
) -> MovePlan | None:
    """Apply conflict policy and return an executable plan, or None to skip."""
    if not plan.destination_path.exists():
        return plan

    if plan.destination_path.is_dir():
        if plan.source_path.is_file():
            message = f"Cannot overwrite directory with file: {plan.destination_path}"
            logger.error(message)
            raise RuntimeError(message)
        if config.conflict_policy == "overwrite":
            shutil.rmtree(plan.destination_path)
            logger.info("Overwriting existing directory: %s", plan.destination_path)
            return plan
    elif plan.source_path.is_dir() and config.conflict_policy == "overwrite":
        plan.destination_path.unlink()
        logger.info("Overwriting existing file: %s", plan.destination_path)
        return plan

    resolved_destination = conflict_resolver.resolve(plan.destination_path, logger=logger)
    if resolved_destination is None:
        return None
    return MovePlan(
        source_path=plan.source_path,
        destination_dir=resolved_destination.parent,
        destination_path=resolved_destination,
        source_timestamps=plan.source_timestamps,
    )


def _execute_move_plan(
    plan: MovePlan,
    *,
    config: OrganizeTempMediaConfig,
    logger: logging.Logger,
) -> None:
    """Move one source item and restore metadata/ownership policies."""
    plan.destination_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(plan.source_path), str(plan.destination_path))
    apply_path_timestamps(plan.destination_dir, plan.source_timestamps)

    try:
        apply_ownership(
            plan.destination_dir,
            owner_user=config.owner_user,
            owner_group=config.owner_group,
        )
        apply_ownership(
            plan.destination_path,
            owner_user=config.owner_user,
            owner_group=config.owner_group,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to apply ownership to %s: %s", plan.destination_path, exc)

    apply_path_timestamps(plan.destination_path, plan.source_timestamps)


def organize_files(config: OrganizeTempMediaConfig, *, logger: logging.Logger) -> int:
    """Run the organizer facade once and return an exit status."""
    if not config.temp_dir.exists():
        message = f"Error: temp directory does not exist: {config.temp_dir}"
        logger.error(message)
        return 1

    items = _collect_items(config)
    conflict_resolver = _build_conflict_resolver(config.conflict_policy)
    logger.info("Found %s matching item(s) in %s", len(items), config.temp_dir)
    if not items:
        logger.info("No matching items found. Nothing to move.")
        logger.info("Organization completed.")
        return 0

    for source_path in items:
        plan = _build_move_plan(source_path, config)

        if plan.source_path == plan.destination_path:
            logger.info("Skipping already organized file: %s", plan.source_path)
            continue

        try:
            resolved_plan = _resolve_existing_destination(
                plan,
                config=config,
                conflict_resolver=conflict_resolver,
                logger=logger,
            )
        except RuntimeError:
            return 1
        if resolved_plan is None:
            continue

        _execute_move_plan(resolved_plan, config=config, logger=logger)
        logger.info("Moved %s to %s", resolved_plan.source_path, resolved_plan.destination_path)

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
