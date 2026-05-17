"""Media-related helpers.

This module provides the stream-inspection and remuxing building blocks used
by the media sync workflow. In design-pattern terms, it is the lower-level
service layer behind the job facade.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MediaStream:
    """A minimal stream record used by the media-filtering strategy."""

    index: int
    codec_type: str
    language: str | None


def is_media_file(path: Path, extensions: tuple[str, ...]) -> bool:
    """Decide whether a file should enter the media sync workflow."""
    return path.suffix.lower().lstrip(".") in {ext.lower() for ext in extensions}


def collect_relative_media_files(root: Path, extensions: tuple[str, ...]) -> list[str]:
    """Collect relative media paths for the destination-sync phase."""
    matches: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and is_media_file(path, extensions):
            matches.append(path.relative_to(root).as_posix())
    return matches


def collect_relative_files(root: Path) -> list[str]:
    """Collect all destination files for stale-file comparison."""
    matches: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            matches.append(path.relative_to(root).as_posix())
    return matches


def copy_file_with_metadata(source: Path, destination: Path) -> None:
    """Duplicate a media file while preserving its filesystem metadata."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def remove_empty_directories(root: Path) -> list[Path]:
    """Prune empty directories after the sync and filtering steps."""
    removed: list[Path] = []
    for directory in sorted(
        (path for path in root.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        try:
            directory.rmdir()
        except OSError:
            continue
        removed.append(directory)
    return removed


def probe_streams(file_path: Path) -> list[MediaStream]:
    """Inspect a media file so the filtering strategy can choose kept streams."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=index,codec_type:stream_tags=language",
            "-of",
            "csv=p=0",
            str(file_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    streams: list[MediaStream] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split(",")]
        index = int(parts[0])
        codec_type = parts[1] if len(parts) > 1 else ""
        language = parts[2] if len(parts) > 2 and parts[2] else None
        streams.append(MediaStream(index=index, codec_type=codec_type, language=language))
    return streams


def is_english_language(language: str | None) -> bool:
    """Decide whether a language tag belongs to the English-only policy."""
    if language is None:
        return False
    return language.lower() in {"eng", "en"}


def find_non_english_audio_subtitle_streams(streams: list[MediaStream]) -> list[int]:
    """Identify streams that violate the English-only filtering policy."""
    indexes: list[int] = []
    for stream in streams:
        if stream.codec_type not in {"audio", "subtitle"}:
            continue
        if not is_english_language(stream.language):
            indexes.append(stream.index)
    return indexes


def build_stream_map_args(
    streams: list[MediaStream],
    *,
    excluded_indexes: set[int] | None = None,
) -> list[str]:
    """Translate the filtering decision into ffmpeg mapping arguments."""
    excluded_indexes = excluded_indexes or set()
    map_args: list[str] = []
    for stream in streams:
        if stream.index in excluded_indexes:
            continue
        if stream.codec_type in {"audio", "subtitle"}:
            if not is_english_language(stream.language):
                continue
        map_args.extend(["-map", f"0:{stream.index}"])
    return map_args


def format_audio_streams(streams: list[MediaStream]) -> str:
    """Render audio streams for verification logs."""
    audio_streams = [
        f"{stream.index}:{stream.language or 'unknown'}"
        for stream in streams
        if stream.codec_type == "audio"
    ]
    return ", ".join(audio_streams) if audio_streams else "none"


def filter_to_english_audio_and_subtitles(
    file_path: Path,
    *,
    ffmpeg_threads: int,
    logger: logging.Logger | None = None,
) -> bool:
    """Remove one non-English audio/subtitle track and verify the output."""
    streams = probe_streams(file_path)
    non_english_indexes = find_non_english_audio_subtitle_streams(streams)
    if not non_english_indexes:
        if logger is not None:
            logger.info("No non-English audio/subtitle streams found for %s", file_path)
        return True

    stream_to_remove = non_english_indexes[0]
    map_args = build_stream_map_args(streams, excluded_indexes={stream_to_remove})
    if not map_args:
        if logger is not None:
            logger.error(
                "Skipping %s because filtering would remove every mapped stream.",
                file_path,
            )
        return False

    temp_file = file_path.with_name(f".nas_scripts_tmp.{file_path.suffix.lstrip('.')}")
    result = subprocess.run(
        [
            "ffmpeg",
            "-threads",
            str(ffmpeg_threads),
            "-i",
            str(file_path),
            *map_args,
            "-c",
            "copy",
            str(temp_file),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        temp_file.unlink(missing_ok=True)
        if logger is not None:
            logger.error("ffmpeg failed while filtering %s", file_path)
        return False

    try:
        verified_streams = probe_streams(temp_file)
    except Exception as exc:  # noqa: BLE001
        temp_file.unlink(missing_ok=True)
        if logger is not None:
            logger.exception("ffprobe failed while verifying %s: %s", temp_file, exc)
        return False

    if logger is not None:
        logger.info(
            "Verified audio tracks for %s: %s",
            file_path,
            format_audio_streams(verified_streams),
        )

    remaining_non_english = find_non_english_audio_subtitle_streams(verified_streams)
    if stream_to_remove in remaining_non_english:
        temp_file.unlink(missing_ok=True)
        if logger is not None:
            logger.error(
                "Verification failed for %s. Stream %s was not removed.",
                file_path,
                stream_to_remove,
            )
        return False

    if logger is not None:
        logger.info(
            "Removed one non-English audio/subtitle stream from %s: %s",
            file_path,
            stream_to_remove,
        )

    temp_file.replace(file_path)
    return True


def remove_leftover_temp_files(root: Path) -> list[Path]:
    """Clean up temporary files left behind by the remuxing workflow."""
    removed: list[Path] = []
    for path in root.rglob(".nas_scripts_tmp.*"):
        if path.is_file():
            path.unlink(missing_ok=True)
            removed.append(path)
    return removed
