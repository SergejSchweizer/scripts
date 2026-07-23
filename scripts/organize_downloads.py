"""Thin script wrapper for the organize_downloads job."""

from nas_scripts.jobs.organize_temp_downloads import main


if __name__ == "__main__":
    # Keep wrapper behavior identical to module entrypoint for cron usage.
    raise SystemExit(main())
