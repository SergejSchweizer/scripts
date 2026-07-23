"""Module entrypoint for ``python -m scripts``."""

from scripts.cli import main


if __name__ == "__main__":
    # Preserve CLI exit status for shell/cron observability.
    raise SystemExit(main())
