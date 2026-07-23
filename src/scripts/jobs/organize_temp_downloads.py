"""Organize temporary downloads into dated media folders."""

from __future__ import annotations

from scripts.config.organize_temp_media import load_organize_temp_downloads_config
from scripts.jobs.organize_temp_media import run_organizer


def main(*, reorganize_existing: bool | None = None) -> int:
    """Run the temporary downloads organizer workflow."""
    return run_organizer(
        load_organize_temp_downloads_config,
        reorganize_existing=reorganize_existing,
    )
