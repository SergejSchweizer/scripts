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
from functools import lru_cache
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class MediaStream:
    """A minimal stream record used by the media-filtering strategy."""

    index: int
    codec_type: str
    language: str | None


class MediaCommandAdapter(Protocol):
    """Port interface for ffprobe/ffmpeg command execution."""

    def run_ffprobe(self, file_path: Path) -> subprocess.CompletedProcess[str]:
        """Run ffprobe and return its completed process output."""

    def run_ffmpeg_copy(
        self,
        *,
        source_path: Path,
        map_args: list[str],
        target_path: Path,
        ffmpeg_threads: int,
    ) -> subprocess.CompletedProcess[str]:
        """Run ffmpeg stream-copy with explicit map arguments."""


class SubprocessMediaCommandAdapter:
    """Default adapter that delegates to subprocess for media commands."""

    def run_ffprobe(self, file_path: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
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
            encoding="utf-8",
            errors="replace",
            check=True,
        )

    def run_ffmpeg_copy(
        self,
        *,
        source_path: Path,
        map_args: list[str],
        target_path: Path,
        ffmpeg_threads: int,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "ffmpeg",
                "-threads",
                str(ffmpeg_threads),
                "-i",
                str(source_path),
                *map_args,
                "-c",
                "copy",
                str(target_path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )


def _build_media_command_adapter() -> MediaCommandAdapter:
    """Factory for media command adapter selection."""
    return SubprocessMediaCommandAdapter()


def is_media_file(path: Path, extensions: tuple[str, ...]) -> bool:
    """Decide whether a file should enter the media sync workflow."""
    return path.suffix.lower().lstrip(".") in _normalized_extensions(extensions)


@lru_cache(maxsize=32)
def _normalized_extensions(extensions: tuple[str, ...]) -> frozenset[str]:
    """Normalize extension tuple once for repeated membership checks."""
    return frozenset(ext.lower() for ext in extensions)


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
    adapter = _build_media_command_adapter()
    result = adapter.run_ffprobe(file_path)
    streams: list[MediaStream] = []
    # ffprobe csv rows are `index,codec_type,language` (language may be missing).
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
    # Keep non audio/subtitle streams untouched; filter policy only targets
    # language-tagged audio/subtitle tracks.
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


def format_stream(stream: MediaStream) -> str:
    """Render a single stream for removal logs.

    Output shape is ``index:codec_type:language`` where language falls back
    to ``unknown`` when ffprobe did not report a tag.
    """
    return f"{stream.index}:{stream.codec_type}:{stream.language or 'unknown'}"


def filter_to_english_audio_and_subtitles(
    file_path: Path,
    *,
    ffmpeg_threads: int,
    logger: logging.Logger | None = None,
) -> bool:
    """Remove non-English audio/subtitle streams and verify each pass.

    Behavior:
    - Removes one non-English audio/subtitle stream per pass.
    - Probes temporary output before replacing the original file.
    - Logs removed stream details as ``index:codec_type:language``.
    """
    max_passes = 20
    temp_file = file_path.with_name(f".nas_scripts_tmp.{file_path.suffix.lstrip('.')}")
    adapter = _build_media_command_adapter()

    for _ in range(max_passes):
        streams = probe_streams(file_path)
        non_english_indexes = find_non_english_audio_subtitle_streams(streams)
        if not non_english_indexes:
            if logger is not None:
                logger.info("No non-English audio/subtitle streams found for %s", file_path)
            return True

        stream_to_remove = non_english_indexes[0]
        stream_to_remove_details = next(
            (stream for stream in streams if stream.index == stream_to_remove),
            None,
        )
        map_args = build_stream_map_args(streams, excluded_indexes={stream_to_remove})
        if not map_args:
            if logger is not None:
                logger.error(
                    "Skipping %s because filtering would remove every mapped stream.",
                    file_path,
                )
            return False

        # Remux into a temp file first; original file is replaced only after probe verification.
        result = adapter.run_ffmpeg_copy(
            source_path=file_path,
            map_args=map_args,
            target_path=temp_file,
            ffmpeg_threads=ffmpeg_threads,
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
        if len(remaining_non_english) >= len(non_english_indexes):
            temp_file.unlink(missing_ok=True)
            if logger is not None:
                logger.error(
                    "Verification failed for %s. Non-English stream count did not decrease.",
                    file_path,
                )
            return False

        temp_file.replace(file_path)
        if logger is not None:
            logger.info(
                "Removed non-English audio/subtitle stream from %s: %s",
                file_path,
                (
                    format_stream(stream_to_remove_details)
                    if stream_to_remove_details is not None
                    else str(stream_to_remove)
                ),
            )

        if not remaining_non_english:
            return True

        if logger is not None:
            logger.info(
                "Continuing filtering for %s. Remaining non-English stream(s): %s",
                file_path,
                ",".join(str(index) for index in remaining_non_english),
            )

    temp_file.unlink(missing_ok=True)
    if logger is not None:
        logger.error("Filtering exceeded max passes for %s", file_path)
    return False


def remove_leftover_temp_files(root: Path) -> list[Path]:
    """Clean up temporary files left behind by the remuxing workflow."""
    removed: list[Path] = []
    for path in root.rglob(".nas_scripts_tmp.*"):
        if path.is_file():
            path.unlink(missing_ok=True)
            removed.append(path)
    return removed
