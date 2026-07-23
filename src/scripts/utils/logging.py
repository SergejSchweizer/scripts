"""Logging helpers for NAS jobs.

This shared module centralizes the cross-cutting logging concern: each job
gets the same format, console output, and weekly rotation so the workflow
modules do not need to duplicate logger setup code.
"""

from __future__ import annotations

import gzip
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
import shutil
import time


LOG_FORMAT = "%(asctime)s | %(levelname)-8s | script=%(name)s | pid=%(process)d | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
SECONDS_PER_DAY = 24 * 60 * 60
COMPRESS_AFTER_SECONDS = 21 * SECONDS_PER_DAY
DELETE_AFTER_SECONDS = 90 * SECONDS_PER_DAY


def _is_rotated_log(path: Path, active_log_file: Path) -> bool:
    """Return whether a path is a rotated artifact for the active log file."""
    return path.name.startswith(f"{active_log_file.name}.")


def _gzip_log_file(source: Path) -> Path:
    """Compress one rotated log and preserve its timestamp for retention cleanup."""
    destination = source.with_name(f"{source.name}.gz")
    with source.open("rb") as source_file, gzip.open(destination, "wb") as archive_file:
        shutil.copyfileobj(source_file, archive_file)
    shutil.copystat(source, destination)
    source.unlink()
    return destination


def _maintain_log_archives(log_file: Path, *, now: float | None = None) -> None:
    """Compress rotated logs after three weeks and delete archives after three months."""
    current_time = time.time() if now is None else now
    if not log_file.parent.exists():
        return

    for path in log_file.parent.iterdir():
        if not path.is_file() or not _is_rotated_log(path, log_file):
            continue
        age_seconds = current_time - path.stat().st_mtime
        if age_seconds >= DELETE_AFTER_SECONDS:
            path.unlink()
            continue
        if path.suffix == ".gz":
            continue
        if age_seconds >= COMPRESS_AFTER_SECONDS:
            _gzip_log_file(path)


def setup_script_logger(script_name: str, log_file: Path) -> logging.Logger:
    """Create the shared logger used by each job facade."""
    logger = logging.getLogger(f"scripts.{script_name}")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    # Reconfigure on each call to avoid duplicate handlers in repeated test/job runs.
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = TimedRotatingFileHandler(
            log_file,
            when="W0",
            interval=1,
            backupCount=0,
            encoding="utf-8",
            delay=False,
            utc=False,
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        _maintain_log_archives(log_file)
    except OSError:
        logger.error("Unable to open local log file for %s", log_file)

    return logger
