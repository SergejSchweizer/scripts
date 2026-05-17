# NAS Scripts

Python automation for NAS workflows.

This repository contains two NAS jobs:

- `sync-media-library`: mirror media into a library and filter non-English streams
- `organize-temp-media`: sort temporary photos and videos into dated folders

## Table Of Contents

- [Quick Start](#quick-start)
- [Project Overview](#project-overview)
- [Jobs](#jobs)
- [Operational Behavior](#operational-behavior)
- [Configuration](#configuration)
- [Execution](#execution)
- [System Dependencies](#system-dependencies)
- [Quality Checks](#quality-checks)
- [Testing](#testing)
- [Known Limitations](#known-limitations)
- [Development Rules](#development-rules)
- [Troubleshooting](#troubleshooting)

## Quick Start

Install dependencies and run the test suite:

```bash
. .venv/bin/activate
python -m pip install -r requirements.txt
.venv/bin/pytest -q
```

Run the CLI:

```bash
python -m nas_scripts
```

## Project Overview

The codebase is organized by responsibility:

| Path | Responsibility |
| --- | --- |
| `src/nas_scripts/cli.py` | Top-level command parsing and dispatch |
| `src/nas_scripts/__main__.py` | `python -m nas_scripts` entrypoint |
| `src/nas_scripts/config/` | Environment-driven runtime config |
| `src/nas_scripts/jobs/` | Job orchestration and workflow logic |
| `src/nas_scripts/utils/` | Shared helpers for files, logging, locking, media, and state |
| `scripts/` | Thin direct-execution wrappers |
| `tests/unit/` | Fast unit tests |

Each job follows the same general lifecycle:

1. Load config.
2. Set up a per-script logger.
3. Acquire a file lock.
4. Run the workflow.
5. Persist state if needed.
6. Release the lock.

## Jobs

### `sync-media-library`

Purpose: sync media from a source tree into a destination library, prune stale files, and keep only English audio and subtitle streams.

Behavior:

| Item | Details |
| --- | --- |
| Input | Media files in `SOURCE_DIR` |
| Output | Copied media in `DEST_DIR` |
| Sync strategy | Copies new files and refreshes destination files when source content changed |
| Cleanup | Removes stale files and empty directories |
| Stream filtering | Uses `ffprobe` and `ffmpeg` to remove one non-English audio or subtitle track per run |
| Cache | Stores verification state in a checksum-based JSON file |
| Safety | Uses a lock file to prevent overlapping runs |

Entry points:

```bash
python -m nas_scripts sync-media-library
python scripts/sync_media_library.py
```

Important detail:

- A file may need multiple runs before all non-English audio/subtitle tracks are removed.
- Already verified files are skipped on later runs unless the policy version changes or the file changes.
- Temporary files created by this script (`.nas_scripts_tmp.*`) under `DEST_DIR` are removed during cleanup.

### `organize-temp-media`

Purpose: sort temporary photos and videos into dated folders.

Behavior:

| Item | Details |
| --- | --- |
| Input | Matching files in `TEMP_DIR` |
| Output | `YYYY-MM/raw`, `YYYY-MM/img`, or `YYYY-MM/vid` |
| Default scan mode | Top-level files only |
| Optional scan mode | `--reorganize-existing` scans nested legacy folders too |
| Safety | Uses a lock file to prevent overlapping runs |

Entry points:

```bash
python -m nas_scripts organize-temp-media
python -m nas_scripts organize-temp-media --reorganize-existing
python scripts/organize_temp_media.py
```

## Operational Behavior

Locking:

- Each job uses a dedicated lock file.
- If another instance is already running, the new run exits cleanly.

Logging:

- Each job writes to its own log file under `LOG_DIR`.
- Logs use a shared format with timestamp, level, script name, and process id.
- If the configured log directory cannot be created, the logger falls back to a local `./logs/` directory when possible.

State:

- `sync-media-library` stores checksum-based verification state in JSON.
- `organize-temp-media` does not keep a persistent state file.

## Configuration

### Media Sync

Environment variables:

- `SOURCE_DIR`
- `DEST_DIR`
- `LOCK_FILE`
- `LOG_DIR`
- `STATE_FILE`
- `MEDIA_EXTENSIONS`
- `FFMPEG_THREADS`

Defaults:

- `SOURCE_DIR`: `/volume1/Torrents`
- `DEST_DIR`: `/volume1/Media`
- `LOCK_FILE`: `/tmp/media.lock`
- `LOG_DIR`: `/volume1/Temp/logs`
- `STATE_FILE`: `/volume1/Temp/logs/sync_media_library.state.json`

### Temp Media Organizer

Environment variables:

- `TEMP_DIR`
- `LOCK_FILE`
- `LOG_DIR`
- `REORGANIZE_EXISTING`
- `FILE_EXTENSIONS`
- `RAW_EXTENSIONS`
- `VIDEO_EXTENSIONS`
- `OWNER_USER`
- `OWNER_GROUP`

Defaults:

- `TEMP_DIR`: `/volume1/Temp/Fotos`
- `LOCK_FILE`: `/tmp/organize_temp_media.lock`
- `LOG_DIR`: `/volume1/Temp/logs`

## Execution

### Direct CLI

```bash
python -m nas_scripts sync-media-library
python -m nas_scripts organize-temp-media
```

### Direct Scripts

```bash
python scripts/sync_media_library.py
python scripts/organize_temp_media.py
```

### Cron Examples

Media sync:

```bash
*/5 * * * * cd /path/to/nas-scripts && /path/to/nas-scripts/.venv/bin/python -m nas_scripts sync-media-library
```

Temp organizer:

```bash
15 23 * * * cd /path/to/nas-scripts && /path/to/nas-scripts/.venv/bin/python -m nas_scripts organize-temp-media
```

## System Dependencies

Python dependencies live in `requirements.txt` and `pyproject.toml`.

The media sync job also needs:

- `ffmpeg`
- `ffprobe`

On Debian and Ubuntu systems:

```bash
sudo apt update
sudo apt install -y ffmpeg
```

## Quality Checks

Run Ruff (with fixes):

```bash
.venv/bin/ruff check --fix .
```

Run MyPy:

```bash
.venv/bin/mypy
```

Enable repository pre-commit hooks:

```bash
git config core.hooksPath .githooks
```

The pre-commit hook runs:

- `ruff check --fix .`
- `mypy`
- `pytest -q`

Because Ruff runs in `--fix` mode, it may modify files before the commit
completes. If that happens, review/stage the changes and commit again.

## Testing

Run all tests with coverage enforcement (minimum 92%):

```bash
.venv/bin/pytest -q
```

Run only the unit suites:

```bash
.venv/bin/pytest -q tests/unit

Generate an explicit coverage report:

```bash
.venv/bin/pytest --cov=src/nas_scripts --cov-report=term-missing
```
```

The repository currently includes unit coverage for:

- CLI dispatch
- config loading
- media copy and stream filtering
- temp-file organization and destination routing
- logger setup and file locking

Some media-sync tests depend on local fixture files under `tests/data/sync_media_library`.
If that directory is missing, those fixture-dependent tests are skipped automatically.

## Known Limitations

- `sync-media-library` removes one non-English audio/subtitle stream per run, so files with many such streams may require multiple runs.

## Development Rules

- Keep job-specific behavior in `src/nas_scripts/jobs/`.
- Keep reusable logic in `src/nas_scripts/utils/`.
- Keep `scripts/` thin.
- Keep jobs isolated from each other.
- Update or add tests when behavior changes.
- Keep log output expressive and consistent.

## Troubleshooting

If a job exits immediately:

- Check the lock file for a stale or active run.
- Verify the configured directories exist.
- Confirm the log directory is writable.

If media sync fails:

- Confirm `ffmpeg` and `ffprobe` are installed and on `PATH`.
- Check whether the file has more than one non-English track and needs another run.
