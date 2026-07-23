"""Sync media files into the library and keep only English audio/subtitles.

This module is the workflow facade for the media sync feature. It coordinates
copying, stale-file cleanup, and stream filtering while the helper modules
handle the filesystem, ffprobe, and ffmpeg details underneath. The filtering
phase uses a checksum cache so already-verified files can be skipped on later
runs.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import time
from pathlib import Path

from scripts.config.sync_media_library import (
    SyncMediaLibraryConfig,
    load_sync_media_library_config,
)
from scripts.utils.filesystem import sha256_file
from scripts.utils.job import run_locked_job
from scripts.utils.media import (
    collect_relative_files,
    collect_relative_media_files,
    copy_file_with_metadata,
    find_non_english_audio_subtitle_streams,
    filter_to_english_audio_and_subtitles,
    probe_streams,
    remove_empty_directories,
    remove_leftover_temp_files,
)
from scripts.utils.state import load_state, save_state
from scripts.utils.verification_cache import (
    DEFAULT_SYNC_UPDATE_POLICY,
    VerificationState,
    VerifiedStateEntry,
    build_cache_validation_strategies,
    build_verified_state_entry,
    is_verified_cache_entry_valid,
    upgrade_verified_state_entry,
)


@dataclass
class FilterStats:
    """Counters emitted by the media filter phase."""

    skipped_verified: int = 0
    ffprobe_failures: int = 0
    filtered: int = 0
    filter_failures: int = 0
    remaining_non_english: int = 0
    newly_verified_clean: int = 0


class MediaFilterProcessor:
    """Per-file processor for English-only stream filtering and cache updates."""

    def __init__(self, config: SyncMediaLibraryConfig, *, logger: logging.Logger) -> None:
        """Create a processor bound to one sync config and logger."""
        self.config = config
        self.logger = logger
        self.previous_state = load_state(config.state_file)
        self.next_state: VerificationState = {}
        self.validation_strategies = build_cache_validation_strategies(config.cache_validation_mode)
        self.stats = FilterStats()

    def run(self) -> None:
        """Process all destination media files and persist the next cache state."""
        self.logger.info("Filter phase: loading state from %s", self.config.state_file)
        self.logger.info(
            "Filter phase: cache validation mode=%s", self.config.cache_validation_mode
        )
        media_files = collect_relative_media_files(self.config.dest_dir, self.config.extensions)
        self.logger.info(
            "Checking %s media file(s) for non-English audio/subtitle streams.",
            len(media_files),
        )

        for relpath in media_files:
            self.process_file(relpath)

        for temp_file in remove_leftover_temp_files(self.config.dest_dir):
            self.logger.info("Removed leftover temp file: %s", temp_file)

        self.logger.info(
            (
                "Filter phase summary: media_files=%s skipped_verified=%s "
                "newly_verified_clean=%s filtered=%s "
                "ffprobe_failures=%s filter_failures=%s remaining_non_english=%s"
            ),
            len(media_files),
            self.stats.skipped_verified,
            self.stats.newly_verified_clean,
            self.stats.filtered,
            self.stats.ffprobe_failures,
            self.stats.filter_failures,
            self.stats.remaining_non_english,
        )
        self.logger.info("Filter phase: saving state to %s", self.config.state_file)
        save_state(self.config.state_file, self.next_state)
        self.logger.info("Filter phase: state saved with %s entrie(s).", len(self.next_state))

    def process_file(self, relpath: str) -> None:
        """Process one destination media file through cache, probe, and filter decisions."""
        file_path = self.config.dest_dir / relpath
        file_stat = file_path.stat()
        current_size = file_stat.st_size
        current_mtime_ns = file_stat.st_mtime_ns
        previous = self.previous_state.get(relpath)
        current_checksum: str | None = None

        if self._reuse_current_cache(relpath, previous, current_size, current_mtime_ns):
            self.logger.info("Skipping already verified file: %s", file_path)
            self.stats.skipped_verified += 1
            return

        if previous is not None and previous.get("verified", False):
            self.logger.info("Filter stat cache miss; computing checksum: %s", file_path)
            current_checksum = sha256_file(file_path)

        if self._reuse_cache_with_checksum(
            relpath,
            previous,
            current_size,
            current_mtime_ns,
            current_checksum,
        ):
            self.logger.info("Skipping already verified file: %s", file_path)
            self.stats.skipped_verified += 1
            return

        try:
            streams = probe_streams(file_path)
        except Exception as exc:  # noqa: BLE001
            self.logger.exception("ffprobe failed for %s: %s", file_path, exc)
            self.stats.ffprobe_failures += 1
            return

        non_english_indexes = find_non_english_audio_subtitle_streams(streams)
        if not non_english_indexes:
            if current_checksum is None:
                current_checksum = sha256_file(file_path)
            self._record_state_entry(
                relpath,
                build_verified_state_entry(
                    checksum=current_checksum,
                    size=current_size,
                    mtime_ns=current_mtime_ns,
                ),
            )
            self.stats.newly_verified_clean += 1
            return

        self._filter_non_english_streams(relpath, file_path, non_english_indexes)

    def _record_state_entry(self, relpath: str, entry: VerifiedStateEntry) -> None:
        """Persist one verified entry immediately for restart-safe progress."""
        self.next_state[relpath] = entry
        save_state(self.config.state_file, self.next_state)

    def _reuse_current_cache(
        self,
        relpath: str,
        previous: VerifiedStateEntry | None,
        current_size: int,
        current_mtime_ns: int,
    ) -> bool:
        """Reuse a current-policy cache entry that matches file stat metadata."""
        if not is_verified_cache_entry_valid(
            previous,
            current_size=current_size,
            current_mtime_ns=current_mtime_ns,
            validation_strategies=self.validation_strategies,
        ):
            return False
        assert previous is not None
        self._record_state_entry(
            relpath,
            upgrade_verified_state_entry(
                previous,
                size=current_size,
                mtime_ns=current_mtime_ns,
            ),
        )
        return True

    def _reuse_cache_with_checksum(
        self,
        relpath: str,
        previous: VerifiedStateEntry | None,
        current_size: int,
        current_mtime_ns: int,
        current_checksum: str | None,
    ) -> bool:
        """Reuse a cache entry after checksum reconciliation."""
        if not is_verified_cache_entry_valid(
            previous,
            current_size=current_size,
            current_mtime_ns=current_mtime_ns,
            current_checksum=current_checksum,
            validation_strategies=self.validation_strategies,
        ):
            return False
        assert previous is not None
        self._record_state_entry(
            relpath,
            upgrade_verified_state_entry(
                previous,
                size=current_size,
                mtime_ns=current_mtime_ns,
            ),
        )
        return True

    def _filter_non_english_streams(
        self,
        relpath: str,
        file_path: Path,
        non_english_indexes: list[int],
    ) -> None:
        """Filter one file and record verification results when filtering converges."""
        self.logger.info(
            "Filtering %s to remove non-English audio/subtitle streams. First stream: %s",
            file_path,
            non_english_indexes[0],
        )
        if not filter_to_english_audio_and_subtitles(
            file_path,
            ffmpeg_threads=self.config.ffmpeg_threads,
            logger=self.logger,
        ):
            self.logger.error("Failed to process %s", file_path)
            self.stats.filter_failures += 1
            return

        self.stats.filtered += 1
        try:
            updated_streams = probe_streams(file_path)
        except Exception as exc:  # noqa: BLE001
            self.logger.exception("ffprobe failed while rechecking %s: %s", file_path, exc)
            self.stats.ffprobe_failures += 1
            return

        remaining_non_english = find_non_english_audio_subtitle_streams(updated_streams)
        if not remaining_non_english:
            updated_stat = file_path.stat()
            self._record_state_entry(
                relpath,
                build_verified_state_entry(
                    checksum=sha256_file(file_path),
                    size=updated_stat.st_size,
                    mtime_ns=updated_stat.st_mtime_ns,
                ),
            )
            self.logger.info("Updated file: %s", file_path)
            return

        self.stats.remaining_non_english += 1
        self.logger.info(
            "Updated file: %s. Remaining non-English stream(s): %s",
            file_path,
            ",".join(str(index) for index in remaining_non_english),
        )


def sync_media_files(
    config: SyncMediaLibraryConfig,
    *,
    logger: logging.Logger,
) -> list[Path]:
    """Run the copy-and-prune phase of the media sync facade."""
    logger.info("Sync phase: scanning source and destination trees.")
    # Previous state is used only for policy-aware copy decisions in this phase.
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
        # Strategy object encapsulates "copy vs preserve" logic and reason codes.
        decision = DEFAULT_SYNC_UPDATE_POLICY.decide(
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
            # Source-of-truth is SOURCE_DIR; destination-only files are stale.
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
    MediaFilterProcessor(config, logger=logger).run()


def run_job(config: SyncMediaLibraryConfig, *, logger: logging.Logger) -> int:
    """Run the media sync facade once and return an exit status."""
    if not config.source_dir.exists():
        message = f"Error: source directory does not exist: {config.source_dir}"
        logger.error(message)
        return 1

    if not config.dest_dir.exists():
        message = f"Error: destination directory does not exist: {config.dest_dir}"
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
    config = load_sync_media_library_config()
    return run_locked_job(
        config,
        lambda logger: run_job(config, logger=logger),
        log_runtime=True,
    )
