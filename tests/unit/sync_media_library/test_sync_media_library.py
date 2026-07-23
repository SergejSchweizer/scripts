from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from scripts.cli import main as cli_main
from scripts.config.sync_media_library import SyncMediaLibraryConfig
from scripts.jobs.sync_media_library import (
    keep_only_english_audio_and_subtitles,
    run_job,
    sync_media_files,
)
from scripts.utils.logging import setup_script_logger
from scripts.utils.media import (
    build_stream_map_args,
    MediaCommandAdapter,
    MediaStream,
    collect_relative_files,
    collect_relative_media_files,
    find_non_english_audio_subtitle_streams,
    filter_to_english_audio_and_subtitles,
    format_audio_streams,
)
from scripts.utils.verification_cache import (
    FILTER_POLICY_VERSION,
    build_cache_validation_strategies,
    files_are_definitely_equal_by_stat,
)
from ..factories import make_sync_config as make_config
from ..fakes import DummyLogger

MEDIA_FIXTURE_ROOT = Path("tests/data/sync_media_library")
JOB_MODULE = Path("src/scripts/jobs/sync_media_library.py")


def _require_media_fixtures() -> Path:
    if not MEDIA_FIXTURE_ROOT.exists():
        pytest.skip("tests/data/sync_media_library is not present in this workspace.")
    return MEDIA_FIXTURE_ROOT


def test_collect_relative_media_files_filters_extensions(tmp_path: Path) -> None:
    root = tmp_path / "media"
    root.mkdir()
    (root / "movie.mkv").write_text("a", encoding="utf-8")
    (root / "clip.mp4").write_text("b", encoding="utf-8")
    (root / "note.txt").write_text("c", encoding="utf-8")

    result = collect_relative_media_files(root, ("mkv", "mp4"))

    assert result == ["clip.mp4", "movie.mkv"]


def test_job_module_stays_isolated_from_other_script_modules() -> None:
    source = JOB_MODULE.read_text(encoding="utf-8")

    assert "scripts.jobs.organize_temp_media" not in source
    assert "scripts.config.organize_temp_media" not in source
    assert "scripts.utils.images" not in source


def test_cli_runs_sync_media_library_command(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["scripts", "sync-media-library"])
    monkeypatch.setattr(
        "scripts.cli.sync_media_library_main",
        lambda: 0,
    )
    assert cli_main() == 0


def test_sync_media_files_copies_new_and_deletes_stale(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.source_dir.mkdir(parents=True)
    config.dest_dir.mkdir(parents=True)
    (config.source_dir / "movie.mkv").write_text("source", encoding="utf-8")
    (config.dest_dir / "old.mkv").write_text("old", encoding="utf-8")

    copied = sync_media_files(config, logger=DummyLogger())

    assert [path.name for path in copied] == ["movie.mkv"]
    assert (config.dest_dir / "movie.mkv").exists()
    assert not (config.dest_dir / "old.mkv").exists()


def test_sync_media_files_overwrites_when_source_content_changes(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.source_dir.mkdir(parents=True)
    config.dest_dir.mkdir(parents=True)
    (config.source_dir / "movie.mkv").write_text("new source", encoding="utf-8")
    (config.dest_dir / "movie.mkv").write_text("old destination", encoding="utf-8")

    copied = sync_media_files(config, logger=DummyLogger())

    assert [path.name for path in copied] == ["movie.mkv"]
    assert (config.dest_dir / "movie.mkv").read_text(encoding="utf-8") == "new source"


def test_sync_fast_path_uses_stat_equality_without_hashing(tmp_path: Path, monkeypatch) -> None:
    config = make_config(tmp_path)
    config.source_dir.mkdir(parents=True)
    config.dest_dir.mkdir(parents=True)
    source = config.source_dir / "movie.mkv"
    dest = config.dest_dir / "movie.mkv"
    source.write_text("same", encoding="utf-8")
    shutil.copy2(source, dest)

    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.sha256_file",
        lambda path: (_ for _ in ()).throw(AssertionError("checksum should be skipped")),
    )

    copied = sync_media_files(config, logger=DummyLogger())

    assert copied == []


def test_sync_preserves_verified_filtered_destination_when_source_not_newer(
    tmp_path: Path, monkeypatch
) -> None:
    config = make_config(tmp_path)
    config.source_dir.mkdir(parents=True)
    config.dest_dir.mkdir(parents=True)
    source = config.source_dir / "movie.mkv"
    dest = config.dest_dir / "movie.mkv"
    source.write_text("source-unfiltered", encoding="utf-8")
    dest.write_text("dest-filtered", encoding="utf-8")

    source_time = 1_700_000_000
    dest_time = source_time + 120
    os.utime(source, (source_time, source_time))
    os.utime(dest, (dest_time, dest_time))
    dest_stat = dest.stat()

    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.load_state",
        lambda _state_file: {
            "movie.mkv": {
                "sha256": "cached",
                "verified": True,
                "policy_version": FILTER_POLICY_VERSION,
                "size": dest_stat.st_size,
                "mtime_ns": dest_stat.st_mtime_ns,
            }
        },
    )
    monkeypatch.setattr(
        "scripts.utils.verification_cache.files_are_definitely_equal_by_stat",
        lambda _source_path, _dest_path: False,
    )
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.sha256_file",
        lambda path: (_ for _ in ()).throw(AssertionError("checksum should be skipped")),
    )
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.copy_file_with_metadata",
        lambda source_path, dest_path: (_ for _ in ()).throw(
            AssertionError("copy should be skipped")
        ),
    )

    copied = sync_media_files(config, logger=DummyLogger())

    assert copied == []
    assert dest.read_text(encoding="utf-8") == "dest-filtered"


def test_sync_rechecks_destination_for_outdated_policy(tmp_path: Path, monkeypatch) -> None:
    config = make_config(tmp_path)
    config.source_dir.mkdir(parents=True)
    config.dest_dir.mkdir(parents=True)
    source = config.source_dir / "movie.mkv"
    dest = config.dest_dir / "movie.mkv"
    source.write_text("source-unfiltered", encoding="utf-8")
    dest.write_text("dest-filtered", encoding="utf-8")

    source_time = 1_700_000_000
    dest_time = source_time + 120
    os.utime(source, (source_time, source_time))
    os.utime(dest, (dest_time, dest_time))

    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.load_state",
        lambda _state_file: {
            "movie.mkv": {
                "sha256": "cached",
                "verified": True,
                "policy_version": FILTER_POLICY_VERSION - 1,
            }
        },
    )
    monkeypatch.setattr(
        "scripts.utils.verification_cache.files_are_definitely_equal_by_stat",
        lambda _source_path, _dest_path: False,
    )
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.sha256_file",
        lambda path: "source" if path == source else "destination",
    )

    copied = sync_media_files(config, logger=DummyLogger())

    assert copied == [dest]
    assert dest.read_text(encoding="utf-8") == "source-unfiltered"


def test_files_are_definitely_equal_by_stat_detects_difference(tmp_path: Path) -> None:
    source = tmp_path / "a.mkv"
    dest = tmp_path / "b.mkv"
    source.write_text("aaaa", encoding="utf-8")
    dest.write_text("bbbbbbb", encoding="utf-8")
    assert not files_are_definitely_equal_by_stat(source, dest)


def test_files_are_definitely_equal_by_stat_accepts_small_mtime_drift(tmp_path: Path) -> None:
    source = tmp_path / "a.mkv"
    dest = tmp_path / "b.mkv"
    source.write_text("same-content", encoding="utf-8")
    dest.write_text("same-content", encoding="utf-8")

    source_stat = source.stat()
    drift_seconds = source_stat.st_mtime + 0.5
    os.utime(dest, (drift_seconds, drift_seconds))

    assert files_are_definitely_equal_by_stat(source, dest)


def test_cache_validation_strategy_factory_supports_known_modes() -> None:
    assert len(build_cache_validation_strategies("stat_only")) == 1
    assert len(build_cache_validation_strategies("stat_then_checksum")) == 2


def test_find_non_english_audio_subtitle_streams_returns_matching_indexes() -> None:
    streams = [
        MediaStream(index=0, codec_type="video", language=None),
        MediaStream(index=1, codec_type="audio", language="eng"),
        MediaStream(index=2, codec_type="audio", language="rus"),
        MediaStream(index=3, codec_type="subtitle", language="spa"),
        MediaStream(index=4, codec_type="subtitle", language="en"),
    ]

    assert find_non_english_audio_subtitle_streams(streams) == [2, 3]


def test_build_stream_map_args_keeps_only_english_audio_and_subtitles() -> None:
    streams = [
        MediaStream(index=0, codec_type="video", language=None),
        MediaStream(index=1, codec_type="audio", language="eng"),
        MediaStream(index=2, codec_type="audio", language="rus"),
        MediaStream(index=3, codec_type="subtitle", language="en"),
        MediaStream(index=4, codec_type="subtitle", language="spa"),
    ]

    assert build_stream_map_args(streams, excluded_indexes={2}) == [
        "-map",
        "0:0",
        "-map",
        "0:1",
        "-map",
        "0:3",
    ]


def test_run_job_fails_when_source_missing(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.dest_dir.mkdir(parents=True)
    logger = setup_script_logger("sync_missing_source", config.log_file)

    assert run_job(config, logger=logger) == 1
    for handler in logger.handlers:
        handler.flush()
    assert "source directory does not exist" in config.log_file.read_text(encoding="utf-8")


def test_run_job_fails_when_dest_missing(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.source_dir.mkdir(parents=True)
    logger = setup_script_logger("sync_missing_dest", config.log_file)

    assert run_job(config, logger=logger) == 1
    for handler in logger.handlers:
        handler.flush()
    assert "destination directory does not exist" in config.log_file.read_text(encoding="utf-8")


def test_collect_relative_files_lists_all_files(tmp_path: Path) -> None:
    root = tmp_path / "dest"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "movie.mkv").write_text("x", encoding="utf-8")
    (root / "note.txt").write_text("y", encoding="utf-8")

    assert collect_relative_files(root) == ["note.txt", "sub/movie.mkv"]


def test_run_job_writes_messages_to_log_file(tmp_path: Path, monkeypatch) -> None:
    config = make_config(tmp_path)
    config.source_dir.mkdir(parents=True)
    config.dest_dir.mkdir(parents=True)
    logger = setup_script_logger(f"sync_job_test_{tmp_path.name}", config.log_file)

    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.sync_media_files",
        lambda config, logger: [],
    )
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.keep_only_english_audio_and_subtitles",
        lambda config, logger: None,
    )

    assert run_job(config, logger=logger) == 0

    for handler in logger.handlers:
        handler.flush()

    log_content = config.log_file.read_text(encoding="utf-8")
    assert "Starting media sync" in log_content
    assert "Media sync completed." in log_content


def test_keep_only_english_audio_and_subtitles_updates_matching_files(
    tmp_path: Path, monkeypatch
) -> None:
    config = make_config(tmp_path)
    config.dest_dir.mkdir(parents=True)
    target = config.dest_dir / "movie.mkv"
    target.write_text("media", encoding="utf-8")

    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.collect_relative_media_files",
        lambda root, extensions: ["movie.mkv"],
    )
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.probe_streams",
        lambda file_path: [
            MediaStream(index=0, codec_type="video", language=None),
            MediaStream(index=1, codec_type="audio", language="eng"),
            MediaStream(index=2, codec_type="audio", language="rus"),
            MediaStream(index=3, codec_type="subtitle", language="spa"),
        ],
    )
    filtered: list[Path] = []
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.filter_to_english_audio_and_subtitles",
        lambda file_path, ffmpeg_threads, logger=None: filtered.append(file_path) or True,
    )
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.remove_leftover_temp_files",
        lambda root: [],
    )
    saved_state: dict[str, dict[str, object]] = {}
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.save_state",
        lambda state_file, state: saved_state.update(state),
    )

    keep_only_english_audio_and_subtitles(config, logger=DummyLogger())

    assert filtered == [target]
    assert saved_state == {}


def test_keep_only_english_audio_and_subtitles_only_verifies_files_once_clean(
    tmp_path: Path, monkeypatch
) -> None:
    config = make_config(tmp_path)
    config.dest_dir.mkdir(parents=True)
    target = config.dest_dir / "movie.mkv"
    target.write_text("media", encoding="utf-8")

    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.collect_relative_media_files",
        lambda root, extensions: ["movie.mkv"],
    )
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.load_state",
        lambda state_file: {},
    )

    probe_results = [
        [
            MediaStream(index=0, codec_type="video", language=None),
            MediaStream(index=1, codec_type="audio", language="eng"),
            MediaStream(index=2, codec_type="audio", language="rus"),
            MediaStream(index=3, codec_type="subtitle", language="spa"),
        ],
        [
            MediaStream(index=0, codec_type="video", language=None),
            MediaStream(index=1, codec_type="audio", language="eng"),
            MediaStream(index=3, codec_type="subtitle", language="spa"),
        ],
    ]
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.probe_streams",
        lambda file_path: probe_results.pop(0),
    )
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.filter_to_english_audio_and_subtitles",
        lambda file_path, ffmpeg_threads, logger=None: True,
    )
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.remove_leftover_temp_files",
        lambda root: [],
    )

    saved_state: dict[str, dict[str, object]] = {}
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.save_state",
        lambda state_file, state: saved_state.update(state),
    )

    keep_only_english_audio_and_subtitles(config, logger=DummyLogger())

    assert saved_state == {}


def test_keep_only_english_audio_and_subtitles_skips_verified_files(
    tmp_path: Path, monkeypatch
) -> None:
    config = make_config(tmp_path)
    config.dest_dir.mkdir(parents=True)
    target = config.dest_dir / "movie.mkv"
    target.write_text("media", encoding="utf-8")

    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.collect_relative_media_files",
        lambda root, extensions: ["movie.mkv"],
    )
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.load_state",
        lambda state_file: {
            "movie.mkv": {
                "sha256": "cached",
                "verified": True,
                "policy_version": FILTER_POLICY_VERSION,
            }
        },
    )
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.sha256_file",
        lambda path: "cached",
    )
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.probe_streams",
        lambda file_path: (_ for _ in ()).throw(AssertionError("probe should be skipped")),
    )
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.filter_to_english_audio_and_subtitles",
        lambda file_path, ffmpeg_threads, logger=None: (_ for _ in ()).throw(
            AssertionError("ffmpeg should be skipped")
        ),
    )
    saved_state: dict[str, dict[str, object]] = {}
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.save_state",
        lambda state_file, state: saved_state.update(state),
    )
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.remove_leftover_temp_files",
        lambda root: [],
    )

    logger = DummyLogger()
    keep_only_english_audio_and_subtitles(config, logger=logger)

    assert saved_state["movie.mkv"]["sha256"] == "cached"
    assert saved_state["movie.mkv"]["verified"] is True
    assert saved_state["movie.mkv"]["policy_version"] == FILTER_POLICY_VERSION
    assert isinstance(saved_state["movie.mkv"]["size"], int)
    assert isinstance(saved_state["movie.mkv"]["mtime_ns"], int)


def test_keep_only_english_audio_and_subtitles_rechecks_incomplete_cache_entry(
    tmp_path: Path, monkeypatch
) -> None:
    config = make_config(tmp_path)
    config.dest_dir.mkdir(parents=True)
    target = config.dest_dir / "movie.mkv"
    target.write_text("media", encoding="utf-8")

    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.collect_relative_media_files",
        lambda root, extensions: ["movie.mkv"],
    )
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.load_state",
        lambda state_file: {
            "movie.mkv": {
                "sha256": "cached",
                "verified": True,
                "policy_version": FILTER_POLICY_VERSION,
            }
        },
    )
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.sha256_file",
        lambda path: "current",
    )
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.probe_streams",
        lambda file_path: [],
    )
    saved_state: dict[str, dict[str, object]] = {}
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.save_state",
        lambda state_file, state: saved_state.update(state),
    )
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.remove_leftover_temp_files",
        lambda root: [],
    )

    keep_only_english_audio_and_subtitles(config, logger=DummyLogger())

    assert saved_state["movie.mkv"]["sha256"] == "current"
    assert isinstance(saved_state["movie.mkv"]["size"], int)
    assert isinstance(saved_state["movie.mkv"]["mtime_ns"], int)


def test_keep_only_english_audio_and_subtitles_skips_by_stat_without_checksum(
    tmp_path: Path, monkeypatch
) -> None:
    config = make_config(tmp_path)
    config.dest_dir.mkdir(parents=True)
    target = config.dest_dir / "movie.mkv"
    target.write_text("media", encoding="utf-8")
    stat = target.stat()

    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.collect_relative_media_files",
        lambda root, extensions: ["movie.mkv"],
    )
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.load_state",
        lambda state_file: {
            "movie.mkv": {
                "sha256": "cached",
                "verified": True,
                "policy_version": FILTER_POLICY_VERSION,
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
        },
    )
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.sha256_file",
        lambda path: (_ for _ in ()).throw(AssertionError("sha256 should be skipped")),
    )
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.probe_streams",
        lambda file_path: (_ for _ in ()).throw(AssertionError("probe should be skipped")),
    )
    saved_state: dict[str, dict[str, object]] = {}
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.save_state",
        lambda state_file, state: saved_state.update(state),
    )
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.remove_leftover_temp_files",
        lambda root: [],
    )

    keep_only_english_audio_and_subtitles(config, logger=DummyLogger())

    assert saved_state == {
        "movie.mkv": {
            "sha256": "cached",
            "verified": True,
            "policy_version": FILTER_POLICY_VERSION,
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    }


def test_keep_only_english_audio_and_subtitles_skips_when_mtime_ns_precision_differs(
    tmp_path: Path, monkeypatch
) -> None:
    config = make_config(tmp_path)
    config.dest_dir.mkdir(parents=True)
    target = config.dest_dir / "movie.mkv"
    target.write_text("media", encoding="utf-8")
    stat = target.stat()

    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.collect_relative_media_files",
        lambda root, extensions: ["movie.mkv"],
    )
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.load_state",
        lambda state_file: {
            "movie.mkv": {
                "sha256": "cached",
                "verified": True,
                "policy_version": FILTER_POLICY_VERSION,
                "size": stat.st_size,
                "mtime_ns": (int(stat.st_mtime) * 1_000_000_000) + 123,
            }
        },
    )
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.sha256_file",
        lambda path: (_ for _ in ()).throw(AssertionError("sha256 should be skipped")),
    )
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.probe_streams",
        lambda file_path: (_ for _ in ()).throw(AssertionError("probe should be skipped")),
    )
    saved_state: dict[str, dict[str, object]] = {}
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.save_state",
        lambda state_file, state: saved_state.update(state),
    )
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.remove_leftover_temp_files",
        lambda root: [],
    )

    keep_only_english_audio_and_subtitles(config, logger=DummyLogger())

    assert saved_state["movie.mkv"]["sha256"] == "cached"
    assert saved_state["movie.mkv"]["verified"] is True


def test_keep_only_english_audio_and_subtitles_rechecks_outdated_cache_policy(
    tmp_path: Path, monkeypatch
) -> None:
    config = make_config(tmp_path)
    config.dest_dir.mkdir(parents=True)
    target = config.dest_dir / "movie.mkv"
    target.write_text("media", encoding="utf-8")

    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.collect_relative_media_files",
        lambda root, extensions: ["movie.mkv"],
    )
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.load_state",
        lambda state_file: {
            "movie.mkv": {
                "sha256": "cached",
                "verified": True,
                "policy_version": 1,
            }
        },
    )
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.sha256_file",
        lambda path: "cached",
    )

    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.probe_streams",
        lambda file_path: [],
    )

    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.filter_to_english_audio_and_subtitles",
        lambda file_path, ffmpeg_threads, logger=None: (_ for _ in ()).throw(
            AssertionError("ffmpeg should be skipped")
        ),
    )
    saved_state: dict[str, dict[str, object]] = {}
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.save_state",
        lambda state_file, state: saved_state.update(state),
    )
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.remove_leftover_temp_files",
        lambda root: [],
    )

    keep_only_english_audio_and_subtitles(config, logger=DummyLogger())

    assert saved_state["movie.mkv"]["policy_version"] == FILTER_POLICY_VERSION
    assert saved_state["movie.mkv"]["verified"] is True


def test_filter_to_english_audio_and_subtitles_verifies_output_before_replacing(
    tmp_path: Path, monkeypatch
) -> None:
    file_path = tmp_path / "movie.mkv"
    file_path.write_text("original", encoding="utf-8")
    log_file = tmp_path / ".logs" / "sync.log"
    logger = setup_script_logger("sync_verify_success", log_file)

    probe_calls = {"source": 0, "temp": 0}

    def fake_probe(path: Path) -> list[MediaStream]:
        if path == file_path:
            probe_calls["source"] += 1
            if probe_calls["source"] == 1:
                return [
                    MediaStream(index=0, codec_type="video", language=None),
                    MediaStream(index=1, codec_type="audio", language="eng"),
                    MediaStream(index=2, codec_type="audio", language="rus"),
                    MediaStream(index=3, codec_type="subtitle", language="spa"),
                ]
            return [
                MediaStream(index=0, codec_type="video", language=None),
                MediaStream(index=1, codec_type="audio", language="eng"),
            ]
        temp_name = f".scripts_tmp.{file_path.suffix.lstrip('.')}"
        if path.name == temp_name:
            probe_calls["temp"] += 1
            if probe_calls["temp"] == 1:
                return [
                    MediaStream(index=0, codec_type="video", language=None),
                    MediaStream(index=1, codec_type="audio", language="eng"),
                    MediaStream(index=3, codec_type="subtitle", language="spa"),
                ]
            return [
                MediaStream(index=0, codec_type="video", language=None),
                MediaStream(index=1, codec_type="audio", language="eng"),
            ]
        raise AssertionError(f"Unexpected path: {path}")

    class Result:
        returncode = 0

    class FakeAdapter(MediaCommandAdapter):
        def run_ffprobe(self, _file_path: Path):
            raise AssertionError("unused in this test")

        def run_ffmpeg_copy(
            self,
            *,
            source_path: Path,
            map_args: list[str],
            target_path: Path,
            ffmpeg_threads: int,
        ):
            del source_path, map_args, ffmpeg_threads
            target_path.write_text("filtered", encoding="utf-8")
            return Result()

    monkeypatch.setattr("scripts.utils.media.probe_streams", fake_probe)
    monkeypatch.setattr(
        "scripts.utils.media._build_media_command_adapter",
        lambda: FakeAdapter(),
    )

    assert filter_to_english_audio_and_subtitles(
        file_path,
        ffmpeg_threads=1,
        logger=logger,
    )

    for handler in logger.handlers:
        handler.flush()

    assert file_path.read_text(encoding="utf-8") == "filtered"
    log_text = log_file.read_text(encoding="utf-8")
    assert "Removed non-English audio/subtitle stream" in log_text
    assert "2:audio:rus" in log_text
    assert "Continuing filtering for" in log_text
    assert "Verified audio tracks for" in log_text


def test_format_audio_streams_renders_audio_indexes_and_languages() -> None:
    streams = [
        MediaStream(index=0, codec_type="video", language=None),
        MediaStream(index=1, codec_type="audio", language="eng"),
        MediaStream(index=2, codec_type="audio", language=None),
        MediaStream(index=3, codec_type="subtitle", language="en"),
    ]

    assert format_audio_streams(streams) == "1:eng, 2:unknown"


def test_filter_to_english_audio_and_subtitles_rejects_unverified_output(
    tmp_path: Path, monkeypatch
) -> None:
    file_path = tmp_path / "movie.mkv"
    file_path.write_text("original", encoding="utf-8")
    log_file = tmp_path / ".logs" / "sync.log"
    logger = setup_script_logger("sync_verify_failure", log_file)

    def fake_probe(path: Path) -> list[MediaStream]:
        if path == file_path:
            return [
                MediaStream(index=0, codec_type="video", language=None),
                MediaStream(index=1, codec_type="audio", language="eng"),
                MediaStream(index=2, codec_type="audio", language="rus"),
                MediaStream(index=3, codec_type="subtitle", language="spa"),
            ]
        temp_name = f".scripts_tmp.{file_path.suffix.lstrip('.')}"
        if path.name == temp_name:
            return [
                MediaStream(index=0, codec_type="video", language=None),
                MediaStream(index=1, codec_type="audio", language="eng"),
                MediaStream(index=2, codec_type="audio", language="rus"),
                MediaStream(index=3, codec_type="subtitle", language="spa"),
            ]
        raise AssertionError(f"Unexpected path: {path}")

    class Result:
        returncode = 0

    class FakeAdapter(MediaCommandAdapter):
        def run_ffprobe(self, _file_path: Path):
            raise AssertionError("unused in this test")

        def run_ffmpeg_copy(
            self,
            *,
            source_path: Path,
            map_args: list[str],
            target_path: Path,
            ffmpeg_threads: int,
        ):
            del source_path, map_args, ffmpeg_threads
            target_path.write_text("filtered", encoding="utf-8")
            return Result()

    monkeypatch.setattr("scripts.utils.media.probe_streams", fake_probe)
    monkeypatch.setattr(
        "scripts.utils.media._build_media_command_adapter",
        lambda: FakeAdapter(),
    )

    assert not filter_to_english_audio_and_subtitles(
        file_path,
        ffmpeg_threads=1,
        logger=logger,
    )

    for handler in logger.handlers:
        handler.flush()

    assert file_path.read_text(encoding="utf-8") == "original"
    assert "Non-English stream count did not decrease" in log_file.read_text(encoding="utf-8")


def test_filter_to_english_audio_and_subtitles_handles_stream_index_renumbering(
    tmp_path: Path, monkeypatch
) -> None:
    file_path = tmp_path / "movie.mkv"
    file_path.write_text("original", encoding="utf-8")

    probe_calls = {"source": 0}

    def fake_probe(path: Path) -> list[MediaStream]:
        if path == file_path:
            probe_calls["source"] += 1
            if probe_calls["source"] == 1:
                return [
                    MediaStream(index=0, codec_type="video", language=None),
                    MediaStream(index=1, codec_type="audio", language="eng"),
                    MediaStream(index=2, codec_type="audio", language="rus"),
                    MediaStream(index=3, codec_type="subtitle", language="spa"),
                ]
            return [
                MediaStream(index=0, codec_type="video", language=None),
                MediaStream(index=1, codec_type="audio", language="eng"),
            ]
        temp_name = f".scripts_tmp.{file_path.suffix.lstrip('.')}"
        if path.name == temp_name:
            return [
                MediaStream(index=0, codec_type="video", language=None),
                MediaStream(index=1, codec_type="audio", language="eng"),
                MediaStream(index=2, codec_type="subtitle", language="spa"),
            ]
        raise AssertionError(f"Unexpected path: {path}")

    class Result:
        returncode = 0

    class FakeAdapter(MediaCommandAdapter):
        def run_ffprobe(self, _file_path: Path):
            raise AssertionError("unused in this test")

        def run_ffmpeg_copy(
            self,
            *,
            source_path: Path,
            map_args: list[str],
            target_path: Path,
            ffmpeg_threads: int,
        ):
            del source_path, map_args, ffmpeg_threads
            target_path.write_text("filtered", encoding="utf-8")
            return Result()

    monkeypatch.setattr("scripts.utils.media.probe_streams", fake_probe)
    monkeypatch.setattr(
        "scripts.utils.media._build_media_command_adapter",
        lambda: FakeAdapter(),
    )

    assert filter_to_english_audio_and_subtitles(
        file_path,
        ffmpeg_threads=1,
        logger=DummyLogger(),
    )
    assert file_path.read_text(encoding="utf-8") == "filtered"


def test_remove_leftover_temp_files_only_deletes_script_temp_prefix(tmp_path: Path) -> None:
    root = tmp_path / "dest"
    root.mkdir()
    ours = root / ".scripts_tmp.mkv"
    legacy = root / "temp.mkv"
    ours.write_text("tmp", encoding="utf-8")
    legacy.write_text("keep", encoding="utf-8")

    from scripts.utils.media import remove_leftover_temp_files

    removed = remove_leftover_temp_files(root)

    assert removed == [ours]
    assert not ours.exists()
    assert legacy.exists()


def test_collect_relative_media_files_uses_real_media_fixtures() -> None:
    fixture_root = _require_media_fixtures()
    result = collect_relative_media_files(fixture_root, ("mkv", "mp4", "avi", "mpg"))

    assert "09_Dergileva_Dobrynskaja_Gurov_Sokolova.pdf" not in result
    assert "Avatar.Fire.and.Ash.2025.x265.WEB-DL.2160p.HDR-DV.mkv" in result
    assert "Balls.Up.2026.2160p.AMZN.WEB-DL.DDP5.1.DV.HDR.H.265.mkv" in result
    assert "Mike.and.Nick.and.Nick.and.Alice.2026.x265.WEB-DL.2160p.HDR-DV.mkv" in result
    assert "Podlasie.2026.x265.WEB-DL.2160p.SDR.mkv" in result


def test_sync_media_files_with_real_fixture_names_without_copying_large_files(
    tmp_path: Path, monkeypatch
) -> None:
    fixture_root = _require_media_fixtures()
    config = SyncMediaLibraryConfig(
        script_name="sync_media_library",
        source_dir=fixture_root,
        dest_dir=tmp_path / "dest",
        lock_file=tmp_path / "media.lock",
        log_dir=tmp_path / ".logs",
        state_file=tmp_path / ".logs" / "sync_media_library.state.json",
        extensions=("mpg", "avi", "mp4", "mkv"),
        ffmpeg_threads=1,
    )
    config.dest_dir.mkdir(parents=True)
    (config.dest_dir / "stale_file.mkv").write_text("stale", encoding="utf-8")

    copied: list[str] = []

    def fake_copy(source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(source.name, encoding="utf-8")
        copied.append(destination.name)

    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.copy_file_with_metadata",
        fake_copy,
    )

    sync_media_files(config, logger=DummyLogger())

    assert "stale_file.mkv" not in collect_relative_files(config.dest_dir)
    assert "Avatar.Fire.and.Ash.2025.x265.WEB-DL.2160p.HDR-DV.mkv" in copied
    assert "Podlasie.2026.x265.WEB-DL.2160p.SDR.mkv" in copied


def test_probe_streams_on_real_fixture_if_ffprobe_is_available() -> None:
    ffprobe_path = shutil.which("ffprobe")
    if not ffprobe_path:
        return

    fixture_root = _require_media_fixtures()
    media_files = collect_relative_media_files(fixture_root, ("mkv",))
    assert media_files, "Expected at least one MKV fixture in tests/data/sync_media_library"

    from scripts.utils.media import probe_streams

    streams = probe_streams(fixture_root / media_files[0])

    assert streams
