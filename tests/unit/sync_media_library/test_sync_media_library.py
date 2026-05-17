from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from nas_scripts.cli import main as cli_main
from nas_scripts.config.sync_media_library import SyncMediaLibraryConfig
from nas_scripts.jobs.sync_media_library import (
    _files_are_definitely_equal_by_stat,
    keep_only_english_audio_and_subtitles,
    run_job,
    sync_media_files,
)
from nas_scripts.utils.logging import setup_script_logger
from nas_scripts.utils.media import (
    build_stream_map_args,
    MediaStream,
    collect_relative_files,
    collect_relative_media_files,
    find_non_english_audio_subtitle_streams,
    filter_to_english_audio_and_subtitles,
    format_audio_streams,
)

MEDIA_FIXTURE_ROOT = Path("tests/data/sync_media_library")
JOB_MODULE = Path("src/nas_scripts/jobs/sync_media_library.py")


def make_config(tmp_path: Path) -> SyncMediaLibraryConfig:
    return SyncMediaLibraryConfig(
        script_name="sync_media_library",
        source_dir=tmp_path / "source",
        dest_dir=tmp_path / "dest",
        lock_file=tmp_path / "media.lock",
        log_dir=tmp_path / "logs",
        state_file=tmp_path / "logs" / "sync_media_library.state.json",
        extensions=("mpg", "avi", "mp4", "mkv"),
        ffmpeg_threads=1,
    )


class DummyLogger:
    def info(self, *args, **_kwargs):
        return None

    def warning(self, *args, **_kwargs):
        return None

    def error(self, *args, **_kwargs):
        return None

    def exception(self, *args, **_kwargs):
        return None


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

    assert "nas_scripts.jobs.ingest_crypto_documents" not in source
    assert "nas_scripts.jobs.organize_temp_media" not in source
    assert "nas_scripts.config.ingest_crypto_documents" not in source
    assert "nas_scripts.config.organize_temp_media" not in source
    assert "nas_scripts.utils.flowrag" not in source
    assert "nas_scripts.utils.images" not in source


def test_cli_runs_sync_media_library_command(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["nas-scripts", "sync-media-library"])
    monkeypatch.setattr(
        "nas_scripts.cli.sync_media_library_main",
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
        "nas_scripts.jobs.sync_media_library.sha256_file",
        lambda path: (_ for _ in ()).throw(AssertionError("checksum should be skipped")),
    )

    copied = sync_media_files(config, logger=DummyLogger())

    assert copied == []


def test_files_are_definitely_equal_by_stat_detects_difference(tmp_path: Path) -> None:
    source = tmp_path / "a.mkv"
    dest = tmp_path / "b.mkv"
    source.write_text("aaaa", encoding="utf-8")
    dest.write_text("bbbbbbb", encoding="utf-8")
    assert not _files_are_definitely_equal_by_stat(source, dest)


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


def test_run_job_fails_when_source_missing(tmp_path: Path, capsys) -> None:
    config = make_config(tmp_path)
    config.dest_dir.mkdir(parents=True)

    assert run_job(config, logger=DummyLogger()) == 1
    assert "source directory does not exist" in capsys.readouterr().err


def test_run_job_fails_when_dest_missing(tmp_path: Path, capsys) -> None:
    config = make_config(tmp_path)
    config.source_dir.mkdir(parents=True)

    assert run_job(config, logger=DummyLogger()) == 1
    assert "destination directory does not exist" in capsys.readouterr().err


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
        "nas_scripts.jobs.sync_media_library.sync_media_files",
        lambda config, logger: [],
    )
    monkeypatch.setattr(
        "nas_scripts.jobs.sync_media_library.keep_only_english_audio_and_subtitles",
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
        "nas_scripts.jobs.sync_media_library.collect_relative_media_files",
        lambda root, extensions: ["movie.mkv"],
    )
    monkeypatch.setattr(
        "nas_scripts.jobs.sync_media_library.probe_streams",
        lambda file_path: [
            MediaStream(index=0, codec_type="video", language=None),
            MediaStream(index=1, codec_type="audio", language="eng"),
            MediaStream(index=2, codec_type="audio", language="rus"),
            MediaStream(index=3, codec_type="subtitle", language="spa"),
        ],
    )
    filtered: list[Path] = []
    monkeypatch.setattr(
        "nas_scripts.jobs.sync_media_library.filter_to_english_audio_and_subtitles",
        lambda file_path, ffmpeg_threads, logger=None: filtered.append(file_path) or True,
    )
    monkeypatch.setattr(
        "nas_scripts.jobs.sync_media_library.remove_leftover_temp_files",
        lambda root: [],
    )
    saved_state: dict[str, dict[str, object]] = {}
    monkeypatch.setattr(
        "nas_scripts.jobs.sync_media_library.save_state",
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
        "nas_scripts.jobs.sync_media_library.collect_relative_media_files",
        lambda root, extensions: ["movie.mkv"],
    )
    monkeypatch.setattr(
        "nas_scripts.jobs.sync_media_library.load_state",
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
        "nas_scripts.jobs.sync_media_library.probe_streams",
        lambda file_path: probe_results.pop(0),
    )
    monkeypatch.setattr(
        "nas_scripts.jobs.sync_media_library.filter_to_english_audio_and_subtitles",
        lambda file_path, ffmpeg_threads, logger=None: True,
    )
    monkeypatch.setattr(
        "nas_scripts.jobs.sync_media_library.remove_leftover_temp_files",
        lambda root: [],
    )

    saved_state: dict[str, dict[str, object]] = {}
    monkeypatch.setattr(
        "nas_scripts.jobs.sync_media_library.save_state",
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
        "nas_scripts.jobs.sync_media_library.collect_relative_media_files",
        lambda root, extensions: ["movie.mkv"],
    )
    monkeypatch.setattr(
        "nas_scripts.jobs.sync_media_library.load_state",
        lambda state_file: {
            "movie.mkv": {
                "sha256": "cached",
                "verified": True,
                "policy_version": 2,
            }
        },
    )
    monkeypatch.setattr(
        "nas_scripts.jobs.sync_media_library.sha256_file",
        lambda path: "cached",
    )
    monkeypatch.setattr(
        "nas_scripts.jobs.sync_media_library.probe_streams",
        lambda file_path: (_ for _ in ()).throw(AssertionError("probe should be skipped")),
    )
    monkeypatch.setattr(
        "nas_scripts.jobs.sync_media_library.filter_to_english_audio_and_subtitles",
        lambda file_path, ffmpeg_threads, logger=None: (_ for _ in ()).throw(
            AssertionError("ffmpeg should be skipped")
        ),
    )
    saved_state: dict[str, dict[str, object]] = {}
    monkeypatch.setattr(
        "nas_scripts.jobs.sync_media_library.save_state",
        lambda state_file, state: saved_state.update(state),
    )
    monkeypatch.setattr(
        "nas_scripts.jobs.sync_media_library.remove_leftover_temp_files",
        lambda root: [],
    )

    logger = DummyLogger()
    keep_only_english_audio_and_subtitles(config, logger=logger)

    assert saved_state["movie.mkv"]["sha256"] == "cached"
    assert saved_state["movie.mkv"]["verified"] is True
    assert saved_state["movie.mkv"]["policy_version"] == 2
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
        "nas_scripts.jobs.sync_media_library.collect_relative_media_files",
        lambda root, extensions: ["movie.mkv"],
    )
    monkeypatch.setattr(
        "nas_scripts.jobs.sync_media_library.load_state",
        lambda state_file: {
            "movie.mkv": {
                "sha256": "cached",
                "verified": True,
                "policy_version": 2,
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
        },
    )
    monkeypatch.setattr(
        "nas_scripts.jobs.sync_media_library.sha256_file",
        lambda path: (_ for _ in ()).throw(AssertionError("sha256 should be skipped")),
    )
    monkeypatch.setattr(
        "nas_scripts.jobs.sync_media_library.probe_streams",
        lambda file_path: (_ for _ in ()).throw(AssertionError("probe should be skipped")),
    )
    saved_state: dict[str, dict[str, object]] = {}
    monkeypatch.setattr(
        "nas_scripts.jobs.sync_media_library.save_state",
        lambda state_file, state: saved_state.update(state),
    )
    monkeypatch.setattr(
        "nas_scripts.jobs.sync_media_library.remove_leftover_temp_files",
        lambda root: [],
    )

    keep_only_english_audio_and_subtitles(config, logger=DummyLogger())

    assert saved_state == {
        "movie.mkv": {
            "sha256": "cached",
            "verified": True,
            "policy_version": 2,
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    }


def test_keep_only_english_audio_and_subtitles_reprocesses_outdated_cached_files(
    tmp_path: Path, monkeypatch
) -> None:
    config = make_config(tmp_path)
    config.dest_dir.mkdir(parents=True)
    target = config.dest_dir / "movie.mkv"
    target.write_text("media", encoding="utf-8")

    monkeypatch.setattr(
        "nas_scripts.jobs.sync_media_library.collect_relative_media_files",
        lambda root, extensions: ["movie.mkv"],
    )
    monkeypatch.setattr(
        "nas_scripts.jobs.sync_media_library.load_state",
        lambda state_file: {
            "movie.mkv": {
                "sha256": "cached",
                "verified": True,
                "policy_version": 1,
            }
        },
    )
    monkeypatch.setattr(
        "nas_scripts.jobs.sync_media_library.sha256_file",
        lambda path: "cached",
    )

    probe_calls: list[Path] = []

    monkeypatch.setattr(
        "nas_scripts.jobs.sync_media_library.probe_streams",
        lambda file_path: probe_calls.append(file_path) or [
            MediaStream(index=0, codec_type="video", language=None),
            MediaStream(index=1, codec_type="audio", language="eng"),
            MediaStream(index=2, codec_type="audio", language="rus"),
        ],
    )

    filtered: list[Path] = []
    monkeypatch.setattr(
        "nas_scripts.jobs.sync_media_library.filter_to_english_audio_and_subtitles",
        lambda file_path, ffmpeg_threads, logger=None: filtered.append(file_path) or True,
    )
    monkeypatch.setattr(
        "nas_scripts.jobs.sync_media_library.remove_leftover_temp_files",
        lambda root: [],
    )

    keep_only_english_audio_and_subtitles(config, logger=DummyLogger())

    assert probe_calls == [target, target]
    assert filtered == [target]


def test_filter_to_english_audio_and_subtitles_verifies_output_before_replacing(
    tmp_path: Path, monkeypatch
) -> None:
    file_path = tmp_path / "movie.mkv"
    file_path.write_text("original", encoding="utf-8")
    log_file = tmp_path / "logs" / "sync.log"
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
        temp_name = f".nas_scripts_tmp.{file_path.suffix.lstrip('.')}"
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

    def fake_run(cmd, **_kwargs):  # type: ignore[no-untyped-def]
        temp_path = Path(cmd[-1])
        temp_path.write_text("filtered", encoding="utf-8")
        return Result()

    monkeypatch.setattr("nas_scripts.utils.media.probe_streams", fake_probe)
    monkeypatch.setattr("nas_scripts.utils.media.subprocess.run", fake_run)

    assert filter_to_english_audio_and_subtitles(
        file_path,
        ffmpeg_threads=1,
        logger=logger,
    )

    for handler in logger.handlers:
        handler.flush()

    assert file_path.read_text(encoding="utf-8") == "filtered"
    assert "Removed non-English audio/subtitle stream" in log_file.read_text(encoding="utf-8")
    assert "Continuing filtering for" in log_file.read_text(encoding="utf-8")
    assert "Verified audio tracks for" in log_file.read_text(encoding="utf-8")


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
    log_file = tmp_path / "logs" / "sync.log"
    logger = setup_script_logger("sync_verify_failure", log_file)

    def fake_probe(path: Path) -> list[MediaStream]:
        if path == file_path:
            return [
                MediaStream(index=0, codec_type="video", language=None),
                MediaStream(index=1, codec_type="audio", language="eng"),
                MediaStream(index=2, codec_type="audio", language="rus"),
                MediaStream(index=3, codec_type="subtitle", language="spa"),
            ]
        temp_name = f".nas_scripts_tmp.{file_path.suffix.lstrip('.')}"
        if path.name == temp_name:
            return [
                MediaStream(index=0, codec_type="video", language=None),
                MediaStream(index=1, codec_type="audio", language="eng"),
                MediaStream(index=2, codec_type="audio", language="rus"),
            ]
        raise AssertionError(f"Unexpected path: {path}")

    class Result:
        returncode = 0

    def fake_run(cmd, **_kwargs):  # type: ignore[no-untyped-def]
        temp_path = Path(cmd[-1])
        temp_path.write_text("filtered", encoding="utf-8")
        return Result()

    monkeypatch.setattr("nas_scripts.utils.media.probe_streams", fake_probe)
    monkeypatch.setattr("nas_scripts.utils.media.subprocess.run", fake_run)

    assert not filter_to_english_audio_and_subtitles(
        file_path,
        ffmpeg_threads=1,
        logger=logger,
    )

    for handler in logger.handlers:
        handler.flush()

    assert file_path.read_text(encoding="utf-8") == "original"
    assert "Stream 2 was not removed" in log_file.read_text(encoding="utf-8")


def test_remove_leftover_temp_files_only_deletes_script_temp_prefix(tmp_path: Path) -> None:
    root = tmp_path / "dest"
    root.mkdir()
    ours = root / ".nas_scripts_tmp.mkv"
    legacy = root / "temp.mkv"
    ours.write_text("tmp", encoding="utf-8")
    legacy.write_text("keep", encoding="utf-8")

    from nas_scripts.utils.media import remove_leftover_temp_files

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
        log_dir=tmp_path / "logs",
        state_file=tmp_path / "logs" / "sync_media_library.state.json",
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
        "nas_scripts.jobs.sync_media_library.copy_file_with_metadata",
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

    from nas_scripts.utils.media import probe_streams

    streams = probe_streams(fixture_root / media_files[0])

    assert streams
