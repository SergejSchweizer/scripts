"""Thin script wrapper for the organize_media job."""

from nas_scripts.jobs.sync_media_library import main


if __name__ == "__main__":
    # Keep wrapper behavior identical to module entrypoint for cron usage.
    raise SystemExit(main())
