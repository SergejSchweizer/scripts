from __future__ import annotations

import logging
import os
import runpy
from pathlib import Path

import pytest

from nas_scripts.config.organize_temp_media import (
    _parse_bool_env,
    _parse_conflict_policy,
    _parse_csv_env,
)
from nas_scripts.config.sync_media_library import load_sync_media_library_config
from nas_scripts.jobs.organize_temp_media import main as organize_main
from nas_scripts.jobs.sync_media_library import main as sync_main
from nas_scripts.utils.locking import AlreadyLockedError, FileLock
from nas_scripts.utils.logging import setup_script_logger
from nas_scripts.utils.media import (
    MediaCommandAdapter,
    MediaStream,
    SubprocessMediaCommandAdapter,
    build_stream_map_args,
    filter_to_english_audio_and_subtitles,
    remove_empty_directories,
    probe_streams,
)
from nas_scripts.utils.state import load_state
from .fakes import DummyResult


def test_main_module_exits_with_cli_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("nas_scripts.cli.main", lambda: 7)
    with pytest.raises(SystemExit) as exc_info:
        runpy.run_module("nas_scripts.__main__", run_name="__main__")
    assert exc_info.value.code == 7


def test_parse_helpers_cover_invalid_and_empty_inputs() -> None:
    assert _parse_csv_env("", ("jpg",)) == ("jpg",)
    assert _parse_csv_env(" , ", ("jpg",)) == ("jpg",)
    assert _parse_csv_env(" JPG,ARW ", ("jpg",)) == ("jpg", "arw")
    assert _parse_bool_env("maybe", default=True) is True
    assert _parse_bool_env("off", default=True) is False
    assert _parse_conflict_policy("rename") == "rename"
    assert _parse_conflict_policy("invalid") == "overwrite"


def test_load_sync_config_parses_extensions_and_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEDIA_EXTENSIONS", " MKV, mp4 ")
    monkeypatch.setenv("FFMPEG_THREADS", "3")
    monkeypatch.setenv("CACHE_VALIDATION_MODE", "stat_only")
    cfg = load_sync_media_library_config()
    assert cfg.extensions == ("mkv", "mp4")
    assert cfg.ffmpeg_threads == 3
    assert cfg.cache_validation_mode == "stat_only"


def test_load_state_returns_empty_for_invalid_json(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    state_file.write_text("{broken", encoding="utf-8")
    assert load_state(state_file) == {}


def test_probe_streams_parses_ffprobe_output(tmp_path: Path) -> None:
    file_path = tmp_path / "movie.mkv"
    file_path.write_text("x", encoding="utf-8")

    class FakeAdapter(MediaCommandAdapter):
        def run_ffprobe(self, _file_path: Path):
            return DummyResult(stdout="0,video\n1,audio,eng\n2,subtitle,spa\n")

        def run_ffmpeg_copy(
            self,
            *,
            source_path: Path,
            map_args: list[str],
            target_path: Path,
            ffmpeg_threads: int,
        ):
            del source_path, map_args, target_path, ffmpeg_threads
            return DummyResult(returncode=0)

    streams = probe_streams(file_path, adapter=FakeAdapter())
    assert streams == [
        MediaStream(index=0, codec_type="video", language=None),
        MediaStream(index=1, codec_type="audio", language="eng"),
        MediaStream(index=2, codec_type="subtitle", language="spa"),
    ]


def test_subprocess_media_adapter_sets_safe_text_decoding(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_calls: list[dict[str, object]] = []

    def fake_run(*args, **kwargs):
        del args
        captured_calls.append(kwargs)
        return DummyResult(returncode=0)

    monkeypatch.setattr("nas_scripts.utils.media.subprocess.run", fake_run)
    adapter = SubprocessMediaCommandAdapter()
    source = Path("/tmp/source.mkv")
    target = Path("/tmp/target.mkv")

    adapter.run_ffprobe(source)
    adapter.run_ffmpeg_copy(
        source_path=source,
        map_args=["-map", "0:0"],
        target_path=target,
        ffmpeg_threads=1,
    )

    assert len(captured_calls) == 2
    for call in captured_calls:
        assert call["text"] is True
        assert call["encoding"] == "utf-8"
        assert call["errors"] == "replace"


def test_filter_to_english_returns_true_when_already_clean(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    file_path = tmp_path / "movie.mkv"
    file_path.write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        "nas_scripts.utils.media.probe_streams",
        lambda _path: [
            MediaStream(index=0, codec_type="video", language=None),
            MediaStream(index=1, codec_type="audio", language="eng"),
        ],
    )
    assert filter_to_english_audio_and_subtitles(file_path, ffmpeg_threads=1)


def test_filter_to_english_returns_false_when_ffmpeg_fails(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "movie.mkv"
    file_path.write_text("x", encoding="utf-8")
    class FakeAdapter(MediaCommandAdapter):
        def run_ffprobe(self, _file_path: Path):
            return DummyResult(stdout="0,video\n1,audio,rus\n")

        def run_ffmpeg_copy(
            self,
            *,
            source_path: Path,
            map_args: list[str],
            target_path: Path,
            ffmpeg_threads: int,
        ):
            del source_path, map_args, target_path, ffmpeg_threads
            return DummyResult(returncode=1)

    assert not filter_to_english_audio_and_subtitles(
        file_path,
        ffmpeg_threads=1,
        adapter=FakeAdapter(),
    )


def test_filter_to_english_returns_false_when_map_args_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    file_path = tmp_path / "movie.mkv"
    file_path.write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        "nas_scripts.utils.media.probe_streams",
        lambda _path: [MediaStream(index=1, codec_type="audio", language="rus")],
    )
    assert not filter_to_english_audio_and_subtitles(file_path, ffmpeg_threads=1)


def test_filter_to_english_returns_false_when_verify_probe_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    file_path = tmp_path / "movie.mkv"
    file_path.write_text("x", encoding="utf-8")

    temp_name = f".nas_scripts_tmp.{file_path.suffix.lstrip('.')}"

    def fake_probe(path: Path) -> list[MediaStream]:
        if path == file_path:
            return [
                MediaStream(index=0, codec_type="video", language=None),
                MediaStream(index=1, codec_type="audio", language="rus"),
            ]
        if path.name == temp_name:
            raise RuntimeError("verify failed")
        return []

    monkeypatch.setattr("nas_scripts.utils.media.probe_streams", fake_probe)
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
            target_path.write_text("tmp", encoding="utf-8")
            return DummyResult(returncode=0)

    monkeypatch.setattr("nas_scripts.utils.media._build_media_command_adapter", lambda: FakeAdapter())
    assert not filter_to_english_audio_and_subtitles(file_path, ffmpeg_threads=1)


def test_build_stream_map_args_can_become_empty() -> None:
    streams = [MediaStream(index=1, codec_type="audio", language="rus")]
    assert build_stream_map_args(streams, excluded_indexes={1}) == []


def test_remove_empty_directories_skips_non_empty_and_removes_empty(tmp_path: Path) -> None:
    root = tmp_path / "root"
    empty_dir = root / "empty"
    non_empty_dir = root / "not_empty"
    empty_dir.mkdir(parents=True)
    non_empty_dir.mkdir(parents=True)
    (non_empty_dir / "file.txt").write_text("x", encoding="utf-8")

    removed = remove_empty_directories(root)

    assert empty_dir in removed
    assert non_empty_dir not in removed
    assert non_empty_dir.exists()


def test_file_lock_raises_when_underlying_lock_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    lock_path = tmp_path / "x.lock"
    lock = FileLock(lock_path)

    if "nas_scripts.utils.locking.fcntl" in globals():
        pass

    def raise_oserror(*args, **_kwargs):  # type: ignore[no-untyped-def]
        raise OSError("busy")

    monkeypatch.setattr("nas_scripts.utils.locking.fcntl.flock", raise_oserror)
    with pytest.raises(AlreadyLockedError):
        lock.acquire()


def test_file_lock_release_without_acquire_is_noop(tmp_path: Path) -> None:
    lock = FileLock(tmp_path / "x.lock")
    lock.release()


def test_setup_logger_falls_back_to_cwd_logs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[Path] = []
    workdir = tmp_path / "wd"
    workdir.mkdir()
    primary_log = tmp_path / "primary" / "fallback_test.log"

    class DummyFileHandler(logging.Handler):
        def emit(self, _record: logging.LogRecord) -> None:
            return None

    def fake_handler(path, *args, **_kwargs):  # type: ignore[no-untyped-def]
        path_obj = Path(path)
        calls.append(path_obj)
        if len(calls) == 1:
            raise OSError("primary path unavailable")
        return DummyFileHandler()

    monkeypatch.setattr("nas_scripts.utils.logging.TimedRotatingFileHandler", fake_handler)
    monkeypatch.chdir(workdir)
    logger = setup_script_logger("fallback_test", primary_log)
    assert len(logger.handlers) >= 2
    assert calls[0] == primary_log
    assert calls[1] == workdir / ".logs" / "fallback_test.log"


def test_setup_logger_handles_both_file_paths_failing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "nas_scripts.utils.logging.TimedRotatingFileHandler",
        lambda *args, **_kwargs: (_ for _ in ()).throw(OSError("nope")),
    )
    monkeypatch.chdir(tmp_path)
    logger = setup_script_logger("no_file_test", Path("/nonwritable/.logs/no_file_test.log"))
    assert len(logger.handlers) == 1


def test_setup_logger_compresses_and_deletes_rotated_logs(tmp_path: Path) -> None:
    log_file = tmp_path / ".logs" / "retention.log"
    log_file.parent.mkdir()
    active_log = log_file
    active_log.write_text("active", encoding="utf-8")
    recent_rotated = log_file.with_name("retention.log.2026-07-01")
    compressible_rotated = log_file.with_name("retention.log.2026-06-01")
    expired_archive = log_file.with_name("retention.log.2026-03-01.gz")
    unrelated_file = log_file.with_name("other.log.2026-03-01")

    for path in (recent_rotated, compressible_rotated, expired_archive, unrelated_file):
        path.write_text(path.name, encoding="utf-8")

    now = 1_000_000_000
    os.utime(recent_rotated, (now - 10 * 24 * 60 * 60, now - 10 * 24 * 60 * 60))
    os.utime(compressible_rotated, (now - 30 * 24 * 60 * 60, now - 30 * 24 * 60 * 60))
    os.utime(expired_archive, (now - 100 * 24 * 60 * 60, now - 100 * 24 * 60 * 60))
    os.utime(unrelated_file, (now - 100 * 24 * 60 * 60, now - 100 * 24 * 60 * 60))

    from nas_scripts.utils.logging import _maintain_log_archives

    _maintain_log_archives(log_file, now=now)

    assert active_log.exists()
    assert recent_rotated.exists()
    assert not compressible_rotated.exists()
    assert compressible_rotated.with_name(f"{compressible_rotated.name}.gz").exists()
    assert not expired_archive.exists()
    assert unrelated_file.exists()


def test_job_main_returns_zero_when_already_locked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    source.mkdir()
    dest.mkdir()
    sync_cfg = load_sync_media_library_config()
    sync_cfg = type(sync_cfg)(
        script_name=sync_cfg.script_name,
        source_dir=source,
        dest_dir=dest,
        lock_file=tmp_path / "sync.lock",
        log_dir=tmp_path / ".logs",
        state_file=tmp_path / ".logs" / "state.json",
        extensions=sync_cfg.extensions,
        ffmpeg_threads=1,
    )
    monkeypatch.setattr("nas_scripts.jobs.sync_media_library.load_sync_media_library_config", lambda: sync_cfg)
    monkeypatch.setattr("nas_scripts.utils.job.FileLock", lambda _path: (_ for _ in ()).throw(AlreadyLockedError("x")))
    assert sync_main() == 0

    org_cfg = type("Cfg", (), {})()
    org_cfg.script_name = "organize_temp_media"
    org_cfg.temp_dir = tmp_path / "temp"
    org_cfg.temp_dir.mkdir()
    org_cfg.lock_file = tmp_path / "org.lock"
    org_cfg.log_dir = tmp_path / ".logs"
    org_cfg.log_file = org_cfg.log_dir / "organize_temp_media.log"
    org_cfg.reorganize_existing = False
    org_cfg.file_extensions = ("jpg",)
    org_cfg.raw_extensions = ("arw",)
    org_cfg.video_extensions = ("mp4",)
    org_cfg.owner_user = None
    org_cfg.owner_group = None
    org_cfg.conflict_policy = "overwrite"

    monkeypatch.setattr(
        "nas_scripts.jobs.organize_temp_media.load_organize_temp_media_config",
        lambda: org_cfg,
    )
    monkeypatch.setattr("nas_scripts.utils.job.FileLock", lambda _path: (_ for _ in ()).throw(AlreadyLockedError("x")))
    assert organize_main() == 0


def test_organize_files_returns_error_when_temp_dir_missing(tmp_path: Path) -> None:
    from nas_scripts.config.organize_temp_media import OrganizeTempMediaConfig
    from nas_scripts.jobs.organize_temp_media import organize_files

    cfg = OrganizeTempMediaConfig(
        script_name="organize_temp_media",
        temp_dir=tmp_path / "missing",
        lock_file=tmp_path / "lock",
        log_dir=tmp_path / ".logs",
        reorganize_existing=False,
        file_extensions=("jpg",),
        raw_extensions=("arw",),
        video_extensions=("mp4",),
        owner_user=None,
        owner_group=None,
        conflict_policy="overwrite",
    )
    logger = setup_script_logger("organize_missing_dir", cfg.log_file)
    assert organize_files(cfg, logger=logger) == 1


def test_organize_files_returns_error_if_destination_is_directory(tmp_path: Path) -> None:
    from nas_scripts.config.organize_temp_media import OrganizeTempMediaConfig
    from nas_scripts.jobs.organize_temp_media import organize_files
    from nas_scripts.utils.images import month_folder_name

    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    source = temp_dir / "photo.jpg"
    source.write_text("x", encoding="utf-8")
    target = temp_dir / month_folder_name(source) / "img" / source.name
    target.mkdir(parents=True)

    cfg = OrganizeTempMediaConfig(
        script_name="organize_temp_media",
        temp_dir=temp_dir,
        lock_file=tmp_path / "lock",
        log_dir=tmp_path / ".logs",
        reorganize_existing=False,
        file_extensions=("jpg",),
        raw_extensions=("arw",),
        video_extensions=("mp4",),
        owner_user=None,
        owner_group=None,
        conflict_policy="overwrite",
    )
    logger = setup_script_logger("organize_dest_dir_error", cfg.log_file)
    assert organize_files(cfg, logger=logger) == 1


def test_sync_main_uses_run_job_when_source_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = load_sync_media_library_config()
    cfg = type(cfg)(
        script_name=cfg.script_name,
        source_dir=tmp_path / "missing",
        dest_dir=tmp_path / "dest",
        lock_file=tmp_path / "sync.lock",
        log_dir=tmp_path / ".logs",
        state_file=tmp_path / ".logs" / "state.json",
        extensions=cfg.extensions,
        ffmpeg_threads=1,
    )
    cfg.dest_dir.mkdir()
    monkeypatch.setattr("nas_scripts.jobs.sync_media_library.load_sync_media_library_config", lambda: cfg)
    monkeypatch.setattr("nas_scripts.jobs.sync_media_library.run_job", lambda _cfg, logger: 1)
    assert sync_main() == 1


def test_sync_keep_only_handles_probe_and_recheck_exceptions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from nas_scripts.config.sync_media_library import SyncMediaLibraryConfig
    from nas_scripts.jobs.sync_media_library import keep_only_english_audio_and_subtitles

    cfg = SyncMediaLibraryConfig(
        script_name="sync_media_library",
        source_dir=tmp_path / "source",
        dest_dir=tmp_path / "dest",
        lock_file=tmp_path / "sync.lock",
        log_dir=tmp_path / ".logs",
        state_file=tmp_path / ".logs" / "state.json",
        extensions=("mkv",),
        ffmpeg_threads=1,
    )
    cfg.dest_dir.mkdir(parents=True)
    first = cfg.dest_dir / "a.mkv"
    second = cfg.dest_dir / "b.mkv"
    first.write_text("x", encoding="utf-8")
    second.write_text("x", encoding="utf-8")

    monkeypatch.setattr(
        "nas_scripts.jobs.sync_media_library.collect_relative_media_files",
        lambda root, extensions: ["a.mkv", "b.mkv"],
    )
    monkeypatch.setattr("nas_scripts.jobs.sync_media_library.load_state", lambda _state: {})
    monkeypatch.setattr("nas_scripts.jobs.sync_media_library.sha256_file", lambda _path: "s")
    monkeypatch.setattr(
        "nas_scripts.jobs.sync_media_library.probe_streams",
        lambda path: (_ for _ in ()).throw(RuntimeError("probe fail"))
        if path == first
        else [MediaStream(index=0, codec_type="audio", language="rus")],
    )
    monkeypatch.setattr(
        "nas_scripts.jobs.sync_media_library.filter_to_english_audio_and_subtitles",
        lambda file_path, ffmpeg_threads, logger=None: True,
    )
    monkeypatch.setattr(
        "nas_scripts.jobs.sync_media_library.find_non_english_audio_subtitle_streams",
        lambda streams: [0],
    )
    monkeypatch.setattr(
        "nas_scripts.jobs.sync_media_library.remove_leftover_temp_files",
        lambda _root: [],
    )
    monkeypatch.setattr(
        "nas_scripts.jobs.sync_media_library.save_state",
        lambda _state, _payload: None,
    )

    keep_only_english_audio_and_subtitles(cfg, logger=setup_script_logger("sync_exceptions", cfg.log_file))


def test_sync_keep_only_marks_clean_files_in_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from nas_scripts.config.sync_media_library import SyncMediaLibraryConfig
    from nas_scripts.jobs.sync_media_library import keep_only_english_audio_and_subtitles

    cfg = SyncMediaLibraryConfig(
        script_name="sync_media_library",
        source_dir=tmp_path / "source",
        dest_dir=tmp_path / "dest",
        lock_file=tmp_path / "sync.lock",
        log_dir=tmp_path / ".logs",
        state_file=tmp_path / ".logs" / "state.json",
        extensions=("mkv",),
        ffmpeg_threads=1,
    )
    cfg.dest_dir.mkdir(parents=True)
    media = cfg.dest_dir / "ok.mkv"
    media.write_text("x", encoding="utf-8")
    saved: dict[str, dict[str, object]] = {}

    monkeypatch.setattr(
        "nas_scripts.jobs.sync_media_library.collect_relative_media_files",
        lambda root, extensions: ["ok.mkv"],
    )
    monkeypatch.setattr("nas_scripts.jobs.sync_media_library.load_state", lambda _state: {})
    monkeypatch.setattr("nas_scripts.jobs.sync_media_library.sha256_file", lambda _path: "digest")
    monkeypatch.setattr(
        "nas_scripts.jobs.sync_media_library.probe_streams",
        lambda _path: [MediaStream(index=0, codec_type="audio", language="eng")],
    )
    monkeypatch.setattr(
        "nas_scripts.jobs.sync_media_library.find_non_english_audio_subtitle_streams",
        lambda _streams: [],
    )
    monkeypatch.setattr(
        "nas_scripts.jobs.sync_media_library.remove_leftover_temp_files",
        lambda _root: [],
    )
    monkeypatch.setattr(
        "nas_scripts.jobs.sync_media_library.save_state",
        lambda _state, payload: saved.update(payload),
    )

    keep_only_english_audio_and_subtitles(cfg, logger=setup_script_logger("sync_clean_state", cfg.log_file))
    assert "ok.mkv" in saved
