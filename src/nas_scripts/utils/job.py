"""Shared job lifecycle helpers."""

from __future__ import annotations

import logging
from pathlib import Path
import time
from typing import Callable, Protocol

from nas_scripts.utils.locking import AlreadyLockedError, FileLock
from nas_scripts.utils.logging import setup_script_logger


class LockedJobConfig(Protocol):
    """Minimal config contract required by the locked job runner."""

    @property
    def script_name(self) -> str:
        """Return the script name used for logging."""

    @property
    def lock_file(self) -> Path:
        """Return the lock file path."""

    @property
    def log_file(self) -> Path:
        """Return the per-script log file path."""


def run_locked_job(
    config: LockedJobConfig,
    job: Callable[[logging.Logger], int],
    *,
    log_runtime: bool = False,
) -> int:
    """Run a job with shared logging, locking, and lock-conflict handling."""
    start_time = time.perf_counter()
    logger = setup_script_logger(config.script_name, config.log_file)
    logger.info("Starting %s", config.script_name)
    try:
        with FileLock(config.lock_file):
            return job(logger)
    except AlreadyLockedError:
        logger.warning("Another instance is already running. Exiting.")
        return 0
    finally:
        if log_runtime:
            elapsed_seconds = time.perf_counter() - start_time
            logger.info("Total script runtime: %.2f seconds", elapsed_seconds)