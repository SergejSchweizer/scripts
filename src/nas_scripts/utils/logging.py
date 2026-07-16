"""Logging helpers for NAS jobs.

This shared module centralizes the cross-cutting logging concern: each job
gets the same format, console output, and weekly rotation so the workflow
modules do not need to duplicate logger setup code.
"""

from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


LOG_FORMAT = (
    "%(asctime)s | %(levelname)-8s | script=%(name)s | pid=%(process)d | %(message)s"
)
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_script_logger(script_name: str, log_file: Path) -> logging.Logger:
    """Create the shared logger used by each job facade."""
    logger = logging.getLogger(f"nas_scripts.{script_name}")
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

    # Fallback keeps observability even when the configured log path is not writable.
    candidate_files = [log_file, Path.cwd() / ".logs" / log_file.name]
    for candidate in candidate_files:
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            file_handler = TimedRotatingFileHandler(
                candidate,
                when="W0",
                interval=1,
                backupCount=0,
                encoding="utf-8",
                delay=False,
                utc=False,
            )
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
            if candidate != log_file:
                logger.warning(
                    "Unable to open log file: %s. Falling back to %s",
                    log_file,
                    candidate,
                )
            break
        except OSError:
            continue
    else:
        logger.error("Unable to open any log file for %s", log_file)

    return logger
