"""Thin script wrapper for the organize_temp_media job."""

from nas_scripts.jobs.organize_temp_media import main


if __name__ == "__main__":
    # Keep wrapper behavior identical to module entrypoint for cron usage.
    raise SystemExit(main())
