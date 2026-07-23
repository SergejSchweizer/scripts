"""Command-line entrypoints for NAS scripts.

The CLI stays intentionally thin: it parses a command, maps it to a job
module, and lets the job own the actual workflow and logging.
"""

from __future__ import annotations

import argparse

from scripts.jobs.organize_temp_downloads import main as organize_temp_downloads_main
from scripts.jobs.organize_temp_media import main as organize_temp_photos_main
from scripts.jobs.sync_media_library import main as sync_media_library_main


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser for all NAS jobs."""
    parser = argparse.ArgumentParser(
        prog="scripts",
        description="Python automation scripts for NAS workflows.",
    )
    # We intentionally keep dispatch data-driven via subparser defaults so the
    # CLI layer stays thin and jobs own workflow behavior.
    subparsers = parser.add_subparsers(dest="command")

    sync_parser = subparsers.add_parser(
        "sync-media-library",
        help="Sync media files into the media library and keep only English audio/subtitle streams.",
    )
    sync_parser.set_defaults(handler=lambda args: sync_media_library_main())

    organize_parser = subparsers.add_parser(
        "organize-temp-photos",
        help="Sort temporary photo and video files into dated folders.",
    )
    organize_parser.add_argument(
        "--reorganize-existing",
        action="store_true",
        help="Also scan existing subdirectories and reorganize older folder layouts into raw/img/vid.",
    )
    organize_parser.set_defaults(
        handler=lambda args: organize_temp_photos_main(
            reorganize_existing=args.reorganize_existing,
        )
    )

    downloads_parser = subparsers.add_parser(
        "organize-temp-downloads",
        help="Sort temporary downloads into dated media folders.",
    )
    downloads_parser.add_argument(
        "--reorganize-existing",
        action="store_true",
        help="Also scan existing subdirectories and reorganize files into month folders.",
    )
    downloads_parser.set_defaults(
        handler=lambda args: organize_temp_downloads_main(
            reorganize_existing=args.reorganize_existing,
        )
    )

    return parser


def main() -> int:
    """Parse CLI arguments and dispatch to the selected job."""
    parser = build_parser()
    args = parser.parse_args()
    # No command should print help and exit successfully for cron/manual usage.
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    return int(handler(args))
