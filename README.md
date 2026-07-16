# NAS Scripts

Python automation for NAS workflows.

This repository contains three NAS commands:

- `sync-media-library`: mirror media into a library and filter non-English streams
- `organize-temp-media`: sort temporary photos and videos into dated folders
- `organize-temp-downloads`: sort temporary downloads into dated folders

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
python -m pip install -e .[dev]
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

Architecture graph:

```text
+---------------------------+
| CLI / Script Entrypoints  |
| (__main__.py, scripts/*)  |
+-------------+-------------+
              |
              v
+---------------------------+
| Jobs (workflow facade)    |
| jobs/sync_media_library   |
| jobs/organize_temp_media  |
| jobs/organize_temp_downloads |
+-------------+-------------+
              |
      +-------+--------+
      |                |
      v                v
+-------------+  +------------------+
| Config      |  | Utils            |
| config/*    |  | media, logging,  |
|             |  | locking, state   |
+-------------+  +------------------+
```

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
| Stream filtering | Uses iterative `ffprobe`/`ffmpeg` passes to remove all non-English audio and subtitle streams |
| Cache | Stores verification state in a checksum-based JSON file |
| Safety | Uses a lock file to prevent overlapping runs |

Entry points:

```bash
python -m nas_scripts sync-media-library
python scripts/sync_media_library.py
```

Flow graph:

```text
SOURCE_DIR ---> sync_media_files() -----------+
                                              |
                                              v
                                DEST_DIR (copied/updated files)
                                              |
                                              v
                           keep_only_english_audio_and_subtitles()
                                              |
                     +------------------------+----------------------+
                     |                                               |
                     v                                               v
         cache hit -> skip                                 non-English streams
                                                             -> ffmpeg pass(es)
                                                             -> verify w/ ffprobe
                                                             -> replace original
                                              |
                                              v
                                  save state JSON + cleanup temp files
```

Important detail:

- Already verified files are skipped on later runs unless the policy version changes or the file changes.
- Temporary files created by this script (`.nas_scripts_tmp.*`) under `DEST_DIR` are removed during cleanup.

Stream filtering internals (`sync-media-library`):

1. The job loads the previous verification state from JSON.
2. For each destination media file, it checks whether the cached state is still valid by size and `mtime_ns`.
3. If the cache entry is valid, the file is skipped immediately (no `ffprobe`, no `ffmpeg`).
4. A `sha256` checksum is computed only when needed for cache reconciliation (not unconditionally).
5. If probing shows only English audio/subtitle streams (`eng`/`en`), the file is marked verified and no remux is executed.
6. If non-English streams exist, the filter runs in iterative passes with a maximum of 20 passes per file.
7. In each pass, exactly one non-English stream is selected (the first matching stream index) and excluded from mapping.
8. `ffmpeg` remuxes with `-c copy` into `.nas_scripts_tmp.<ext>` so streams are copied without re-encoding.
9. The temporary output is re-probed; only if verification succeeds is it atomically moved over the original file.
10. If non-English streams remain, the next pass starts; if none remain, processing for that file stops.
11. Any leftover `.nas_scripts_tmp.*` files are removed at the end of the job.

Why this avoids unnecessary work:

- Verified files are skipped using cached metadata.
- Checksums are lazy (computed only for specific validation paths).
- Clean files never enter `ffmpeg`.
- `ffmpeg` uses stream copy (`-c copy`) instead of expensive transcoding.
- Processing stops as soon as the file is fully clean.

### `organize-temp-media` and `organize-temp-downloads`

Purpose: sort temporary photos, videos, and downloads into dated folders.

Behavior:

| Item | Details |
| --- | --- |
| Input | Matching files in `TEMP_DIR` |
| Output | `organize-temp-media`: `YYYY-MM/raw`, `YYYY-MM/img`, or `YYYY-MM/vid`; `organize-temp-downloads`: `YYYY-MM` only |
| Default scan mode | Top-level files only |
| Optional scan mode | `--reorganize-existing` scans nested legacy folders too |
| Safety | Uses a lock file to prevent overlapping runs |

Entry points:

```bash
python -m nas_scripts organize-temp-media
python -m nas_scripts organize-temp-media --reorganize-existing
python -m nas_scripts organize-temp-downloads
python -m nas_scripts organize-temp-downloads --reorganize-existing
python scripts/organize_temp_media.py
python scripts/organize_temp_downloads.py
```

Flow graph:

```text
TEMP_DIR --> collect matching files --> build destination bucket (YYYY-MM or YYYY-MM/raw|img|vid)
                                         |
                                         v
                            resolve conflicts (overwrite|skip|rename)
                                         |
                                         v
                                       move file
                                         |
                                         v
                           preserve timestamps + optional chown
```

## Operational Behavior

Locking:

- Each job uses a dedicated lock file.
- If another instance is already running, the new run exits cleanly.

Logging:

- Each job writes to its own log file under `LOG_DIR`.
- Logs use a shared format with timestamp, level, script name, and process id.
- If the configured log directory cannot be created, the logger falls back to a local `./.logs/` directory when possible.

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
- `LOG_DIR`: `/volume1/Temp/.logs`
- `STATE_FILE`: `/volume1/Temp/.logs/sync_media_library.state.json`

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
- `CONFLICT_POLICY` (`overwrite`, `skip`, or `rename`)

Defaults:

- `TEMP_DIR`: `/volume1/Temp/Fotos` for `organize-temp-media`, `/volume1/Temp/Downloads` for `organize-temp-downloads`
- `LOCK_FILE`: `/tmp/organize_temp_media.lock` for `organize-temp-media`, `/tmp/organize_temp_downloads.lock` for `organize-temp-downloads`
- `LOG_DIR`: `/volume1/Temp/.logs`
- `CONFLICT_POLICY`: `overwrite`

Notes:

- `FILE_EXTENSIONS`, `RAW_EXTENSIONS`, and `VIDEO_EXTENSIONS` are matched case-insensitively.
- `organize-temp-downloads` uses the same file extensions but does not split files into `img/`, `vid/`, or `raw/` subfolders.

## Execution

### Direct CLI

```bash
python -m nas_scripts sync-media-library
python -m nas_scripts organize-temp-media
python -m nas_scripts organize-temp-downloads
```

### Direct Scripts

```bash
python scripts/sync_media_library.py
python scripts/organize_temp_media.py
python scripts/organize_temp_downloads.py
```

### Cron Examples

Media sync:

```bash
*/5 * * * * cd /path/to/nas-scripts && /path/to/nas-scripts/.venv/bin/python -m nas_scripts sync-media-library
```

Temp organizer:

```bash
15 23 * * * cd /path/to/nas-scripts && /path/to/nas-scripts/.venv/bin/python -m nas_scripts organize-temp-media
30 23 * * * cd /path/to/nas-scripts && /path/to/nas-scripts/.venv/bin/python -m nas_scripts organize-temp-downloads
```

## System Dependencies

Python dependencies live in `pyproject.toml`.

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
```

Generate an explicit coverage report:

```bash
.venv/bin/pytest --cov=src/nas_scripts --cov-report=term-missing
```

Run verbose coverage with test output and branch details:

```bash
.venv/bin/pytest -vv --cov=src/nas_scripts --cov-branch --cov-report=term-missing
```

Generate an HTML coverage report:

```bash
.venv/bin/pytest --cov=src/nas_scripts --cov-branch --cov-report=html
```

Then open `htmlcov/index.html` in your browser.

The repository currently includes unit coverage for:

- CLI dispatch
- config loading
- media copy and stream filtering
- temp-file organization and destination routing
- logger setup and file locking

Some media-sync tests depend on local fixture files under `tests/data/sync_media_library`.
If that directory is missing, those fixture-dependent tests are skipped automatically.

## Known Limitations

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
- Check the job log for verification or remuxing errors for the specific file.
