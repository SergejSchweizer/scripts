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
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

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

FILTER_POLICY_VERSION = 3


class _CacheValidationStrategy(Protocol):
    """Contract for cache-entry validation strategies."""

    def is_valid(
        self,
        previous: dict[str, Any],
        *,
        current_size: int,
        current_mtime_ns: int,
        current_checksum: str | None,
    ) -> bool:
        """Return whether the previous entry still applies."""


@dataclass(frozen=True)
class _SyncUpdateDecision:
    """Decision object for the source->destination update policy."""

    should_copy: bool
    reason: str


class _SyncUpdatePolicy(Protocol):
    """Strategy contract for source/destination update decisions."""

    def decide(
        self,
        *,
        relpath: str,
        source_path: Path,
        dest_path: Path,
        previous: dict[str, Any] | None,
    ) -> _SyncUpdateDecision:
        """Return whether to copy and why."""


class _DefaultSyncUpdatePolicy:
    """Default update policy that preserves filtered destination outputs."""

    def decide(
        self,
        *,
        relpath: str,
        source_path: Path,
        dest_path: Path,
        previous: dict[str, Any] | None,
    ) -> _SyncUpdateDecision:
        del relpath
        if _files_are_definitely_equal_by_stat(source_path, dest_path):
            return _SyncUpdateDecision(should_copy=False, reason="stat_match")

        source_stat = source_path.stat()
        dest_stat = dest_path.stat()

        if _is_verified_cache_entry_valid(
            previous,
            current_size=dest_stat.st_size,
            current_mtime_ns=dest_stat.st_mtime_ns,
            validation_strategies=(_STAT_VALIDATION_STRATEGY,),
        ) and source_stat.st_mtime_ns <= dest_stat.st_mtime_ns:
            return _SyncUpdateDecision(
                should_copy=False,
                reason="preserve_filtered_verified_current_policy",
            )

        if _is_verified_state_entry(previous) and source_stat.st_mtime_ns <= dest_stat.st_mtime_ns:
            return _SyncUpdateDecision(
                should_copy=False,
                reason="preserve_filtered_verified_legacy_policy",
            )

        return _SyncUpdateDecision(should_copy=True, reason="checksum_required")


class _StatValidationStrategy:
    """Validate cache entries using deterministic file stat fields."""

    def is_valid(
        self,
        previous: dict[str, Any],
        *,
        current_size: int,
        current_mtime_ns: int,
        current_checksum: str | None,
    ) -> bool:
        del current_checksum
        previous_size = previous.get("size")
        previous_mtime_ns = previous.get("mtime_ns")
        if not isinstance(previous_size, int) or not isinstance(previous_mtime_ns, int):
            return False
        if previous_size != current_size:
            return False
        if previous_mtime_ns == current_mtime_ns:
            return True
        # Some NAS/filesystem combinations expose different sub-second precision
        # across runs; accept equal second-level mtime to avoid needless re-hashing.
        return previous_mtime_ns // 1_000_000_000 == current_mtime_ns // 1_000_000_000


class _ChecksumValidationStrategy:
    """Fallback validation for entries that must be reconciled by checksum."""

    def is_valid(
        self,
        previous: dict[str, Any],
        *,
        current_size: int,
        current_mtime_ns: int,
        current_checksum: str | None,
    ) -> bool:
        del current_size, current_mtime_ns
        if current_checksum is None:
            return False
        return previous.get("sha256") == current_checksum


_STAT_VALIDATION_STRATEGY = _StatValidationStrategy()
_CHECKSUM_VALIDATION_STRATEGY = _ChecksumValidationStrategy()
_DEFAULT_SYNC_UPDATE_POLICY = _DefaultSyncUpdatePolicy()


def _cache_is_eligible_for_reuse(previous: dict[str, Any] | None) -> bool:
    """Fast contract check before strategy-based validation."""
    if previous is None:
        return False
    # Reuse is gated by policy version so behavior changes force re-validation.
    if previous.get("policy_version") != FILTER_POLICY_VERSION:
        return False
    return bool(previous.get("verified", False))


def _is_verified_state_entry(previous: dict[str, Any] | None) -> bool:
    """Check whether a state entry marks a file as verified, independent of policy version."""
    if previous is None:
        return False
    return bool(previous.get("verified", False))


def _build_cache_validation_strategies(
    mode: str,
) -> tuple[_CacheValidationStrategy, ...]:
    """Factory for selecting cache validation strategies."""
    if mode == "stat_only":
        return (_STAT_VALIDATION_STRATEGY,)
    return (_STAT_VALIDATION_STRATEGY, _CHECKSUM_VALIDATION_STRATEGY)


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


def _upgrade_verified_state_entry(
    previous: dict[str, Any],
    *,
    size: int,
    mtime_ns: int,
) -> dict[str, Any]:
    """Upgrade a verified cache entry to the current policy version."""
    return {
        **previous,
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
    validation_strategies: tuple[_CacheValidationStrategy, ...]
    | None = None,
) -> bool:
    """Decide whether a cached verification result still applies."""
    if not _cache_is_eligible_for_reuse(previous):
        return False
    assert previous is not None

    # If checksum is unavailable, only stat-based strategies can run.
    strategies = validation_strategies or _build_cache_validation_strategies(
        "stat_then_checksum" if current_checksum is not None else "stat_only"
    )

    return any(
        strategy.is_valid(
            previous,
            current_size=current_size,
            current_mtime_ns=current_mtime_ns,
            current_checksum=current_checksum,
        )
        for strategy in strategies
    )


def _files_are_definitely_equal_by_stat(source_path: Path, dest_path: Path) -> bool:
    """Fast-path equality check based on size and near-equal mtime."""
    source_stat = source_path.stat()
    dest_stat = dest_path.stat()
    mtime_delta_ns = abs(source_stat.st_mtime_ns - dest_stat.st_mtime_ns)
    return (
        source_stat.st_size == dest_stat.st_size
        and mtime_delta_ns <= 1_000_000_000
    )


def sync_media_files(
    config: SyncMediaLibraryConfig,
    *,
    logger: logging.Logger,
) -> list[Path]:
    """Run the copy-and-prune phase of the media sync facade."""
    logger.info("Sync phase: scanning source and destination trees.")
    previous_state = load_state(config.state_file)
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
        # New destination path: copy immediately without policy evaluation.
        if not dest_path.exists():
            copy_file_with_metadata(source_path, dest_path)
            copied_files.append(dest_path)
            logger.info("Copied: %s", relpath)
            continue

        previous = previous_state.get(relpath)
        decision = _DEFAULT_SYNC_UPDATE_POLICY.decide(
            relpath=relpath,
            source_path=source_path,
            dest_path=dest_path,
            previous=previous,
        )
        if decision.reason == "stat_match":
            logger.info("Sync skip by stat match: %s", relpath)
            continue
        if decision.reason == "preserve_filtered_verified_current_policy":
            logger.info(
                "Sync skip to preserve filtered destination (verified + source not newer): %s",
                relpath,
            )
            continue
        if decision.reason == "preserve_filtered_verified_legacy_policy":
            logger.info(
                (
                    "Sync skip to preserve filtered destination with legacy policy "
                    "(verified + source not newer): %s"
                ),
                relpath,
            )
            continue
        if not decision.should_copy:
            logger.info("Sync skip by update policy (%s): %s", decision.reason, relpath)
            continue

        logger.info("Sync stat mismatch; computing checksums: %s", relpath)
        source_checksum = sha256_file(source_path)
        dest_checksum = sha256_file(dest_path)
        if source_checksum != dest_checksum:
            copy_file_with_metadata(source_path, dest_path)
            copied_files.append(dest_path)
            logger.info("Updated changed file: %s", relpath)
        else:
            logger.info("Sync skip by checksum match: %s", relpath)

    for relpath in sorted(dest_set - source_set):
        full_path = config.dest_dir / relpath
        if full_path.is_file():
            full_path.unlink()
            logger.info("Deleted stale file: %s", relpath)

    for removed_dir in remove_empty_directories(config.dest_dir):
        logger.info("Deleted empty directory: %s", removed_dir)

    logger.info(
        "Sync phase summary: source=%s dest=%s copied_or_updated=%s",
        len(source_files),
        len(dest_files),
        len(copied_files),
    )
    return copied_files


def keep_only_english_audio_and_subtitles(
    config: SyncMediaLibraryConfig,
    *,
    logger: logging.Logger,
) -> None:
    """Run the post-copy filtering phase that preserves English tracks."""
    logger.info("Filter phase: loading state from %s", config.state_file)
    previous_state = load_state(config.state_file)
    next_state: dict[str, dict[str, Any]] = {}
    validation_strategies = _build_cache_validation_strategies(config.cache_validation_mode)
    logger.info("Filter phase: cache validation mode=%s", config.cache_validation_mode)
    media_files = collect_relative_media_files(config.dest_dir, config.extensions)
    logger.info(
        "Checking %s media file(s) for non-English audio/subtitle streams.",
        len(media_files),
    )
    skipped_verified_count = 0
    migrated_legacy_count = 0
    ffprobe_fail_count = 0
    filtered_count = 0
    filter_fail_count = 0
    still_non_english_count = 0
    newly_verified_clean_count = 0

    def _record_state_entry(relpath: str, entry: dict[str, Any]) -> None:
        """Persist one verified entry immediately for restart-safe progress."""
        next_state[relpath] = entry
        save_state(config.state_file, next_state)

    for relpath in media_files:
        file_path = config.dest_dir / relpath
        file_stat: os.stat_result = file_path.stat()
        current_size = file_stat.st_size
        current_mtime_ns = file_stat.st_mtime_ns
        previous = previous_state.get(relpath)
        current_checksum: str | None = None
        # Fast path: verified cache entry is still valid for current file stats.
        if _is_verified_cache_entry_valid(
            previous,
            current_size=current_size,
            current_mtime_ns=current_mtime_ns,
            validation_strategies=validation_strategies,
        ):
            assert previous is not None
            _record_state_entry(
                relpath,
                _upgrade_verified_state_entry(
                    previous,
                    size=current_size,
                    mtime_ns=current_mtime_ns,
                ),
            )
            logger.info("Skipping already verified file: %s", file_path)
            skipped_verified_count += 1
            continue

        # One-time migration path for legacy entries that are already verified
        # under current policy but miss stat fields; avoid expensive re-hashing.
        if (
            previous is not None
            and previous.get("verified", False)
            and previous.get("policy_version") == FILTER_POLICY_VERSION
            and (not isinstance(previous.get("size"), int) or not isinstance(previous.get("mtime_ns"), int))
        ):
            assert previous is not None
            _record_state_entry(
                relpath,
                _upgrade_verified_state_entry(
                    previous,
                    size=current_size,
                    mtime_ns=current_mtime_ns,
                ),
            )
            logger.info("Skipping verified legacy cache entry without checksum: %s", file_path)
            migrated_legacy_count += 1
            continue

        # Only compute checksum on demand to avoid expensive hashing on every file.
        if previous is not None and previous.get("verified", False):
            logger.info("Filter stat cache miss; computing checksum: %s", file_path)
            current_checksum = sha256_file(file_path)
        if previous is not None and _is_verified_cache_entry_valid(
            previous,
            current_size=current_size,
            current_mtime_ns=current_mtime_ns,
            current_checksum=current_checksum,
            validation_strategies=validation_strategies,
        ):
            _record_state_entry(
                relpath,
                _upgrade_verified_state_entry(
                    previous,
                    size=current_size,
                    mtime_ns=current_mtime_ns,
                ),
            )
            logger.info("Skipping already verified file: %s", file_path)
            skipped_verified_count += 1
            continue

        if (
            previous is not None
            and current_checksum is not None
            and previous.get("sha256") == current_checksum
            and previous.get("policy_version") != FILTER_POLICY_VERSION
        ):
            logger.info(
                "Upgrading cached verification policy without recheck: %s",
                file_path,
            )
            _record_state_entry(
                relpath,
                _upgrade_verified_state_entry(
                    previous,
                    size=current_size,
                    mtime_ns=current_mtime_ns,
                ),
            )
            migrated_legacy_count += 1
            skipped_verified_count += 1
            continue
        try:
            streams = probe_streams(file_path)
        except Exception as exc:  # noqa: BLE001
            logger.exception("ffprobe failed for %s: %s", file_path, exc)
            ffprobe_fail_count += 1
            continue

        non_english_indexes = find_non_english_audio_subtitle_streams(streams)
        if not non_english_indexes:
            if current_checksum is None:
                current_checksum = sha256_file(file_path)
            _record_state_entry(
                relpath,
                _build_verified_state_entry(
                    checksum=current_checksum,
                    size=current_size,
                    mtime_ns=current_mtime_ns,
                ),
            )
            newly_verified_clean_count += 1
            continue

        logger.info(
            "Filtering %s to remove non-English audio/subtitle streams. First stream: %s",
            file_path,
            non_english_indexes[0],
        )
        # Remux/verify logic lives in utils.media; this layer tracks orchestration outcomes.
        if not filter_to_english_audio_and_subtitles(
            file_path,
            ffmpeg_threads=config.ffmpeg_threads,
            logger=logger,
        ):
            logger.error("Failed to process %s", file_path)
            filter_fail_count += 1
            continue

        filtered_count += 1
        try:
            updated_streams = probe_streams(file_path)
        except Exception as exc:  # noqa: BLE001
            logger.exception("ffprobe failed while rechecking %s: %s", file_path, exc)
            ffprobe_fail_count += 1
            continue

        remaining_non_english = find_non_english_audio_subtitle_streams(updated_streams)
        if not remaining_non_english:
            updated_stat: os.stat_result = file_path.stat()
            _record_state_entry(
                relpath,
                _build_verified_state_entry(
                    checksum=sha256_file(file_path),
                    size=updated_stat.st_size,
                    mtime_ns=updated_stat.st_mtime_ns,
                ),
            )
            logger.info("Updated file: %s", file_path)
        else:
            still_non_english_count += 1
            logger.info(
                "Updated file: %s. Remaining non-English stream(s): %s",
                file_path,
                ",".join(str(index) for index in remaining_non_english),
            )

    for temp_file in remove_leftover_temp_files(config.dest_dir):
        logger.info("Removed leftover temp file: %s", temp_file)

    logger.info(
        (
            "Filter phase summary: media_files=%s skipped_verified=%s "
            "migrated_legacy=%s newly_verified_clean=%s filtered=%s "
            "ffprobe_failures=%s filter_failures=%s remaining_non_english=%s"
        ),
        len(media_files),
        skipped_verified_count,
        migrated_legacy_count,
        newly_verified_clean_count,
        filtered_count,
        ffprobe_fail_count,
        filter_fail_count,
        still_non_english_count,
    )
    logger.info("Filter phase: saving state to %s", config.state_file)
    save_state(config.state_file, next_state)
    logger.info("Filter phase: state saved with %s entrie(s).", len(next_state))


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
    sync_start = time.perf_counter()
    sync_media_files(config, logger=logger)
    logger.info("Sync phase runtime: %.2f seconds", time.perf_counter() - sync_start)

    filter_start = time.perf_counter()
    keep_only_english_audio_and_subtitles(config, logger=logger)
    logger.info("Filter phase runtime: %.2f seconds", time.perf_counter() - filter_start)
    logger.info("Media sync completed.")
    return 0


def main() -> int:
    """Compose the media sync workflow from config, logging, and locking."""
    start_time = time.perf_counter()
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
    finally:
        elapsed_seconds = time.perf_counter() - start_time
        logger.info("Total script runtime: %.2f seconds", elapsed_seconds)
