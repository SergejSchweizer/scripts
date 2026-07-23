from __future__ import annotations

from pathlib import Path

import pytest

from scripts.config.organize_temp_media import OrganizeTempMediaConfig
from scripts.config.sync_media_library import SyncMediaLibraryConfig, load_sync_media_library_config
from scripts.jobs.organize_temp_media import main as organize_main
from scripts.jobs.organize_temp_media import organize_files
from scripts.jobs.sync_media_library import keep_only_english_audio_and_subtitles
from scripts.jobs.sync_media_library import main as sync_main
from scripts.utils.images import month_folder_name
from scripts.utils.locking import AlreadyLockedError
from scripts.utils.logging import setup_script_logger
from scripts.utils.media import MediaStream


def test_job_main_returns_zero_when_already_locked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.load_sync_media_library_config", lambda: sync_cfg
    )
    monkeypatch.setattr(
        "scripts.utils.job.FileLock",
        lambda _path: (_ for _ in ()).throw(AlreadyLockedError("x")),
    )
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
        "scripts.jobs.organize_temp_media.load_organize_temp_media_config",
        lambda: org_cfg,
    )
    monkeypatch.setattr(
        "scripts.utils.job.FileLock",
        lambda _path: (_ for _ in ()).throw(AlreadyLockedError("x")),
    )
    assert organize_main() == 0


def test_organize_files_returns_error_when_temp_dir_missing(tmp_path: Path) -> None:
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


def test_sync_main_uses_run_job_when_source_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.load_sync_media_library_config", lambda: cfg
    )
    monkeypatch.setattr("scripts.jobs.sync_media_library.run_job", lambda _cfg, logger: 1)
    assert sync_main() == 1


def test_sync_keep_only_handles_probe_and_recheck_exceptions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
        "scripts.jobs.sync_media_library.collect_relative_media_files",
        lambda root, extensions: ["a.mkv", "b.mkv"],
    )
    monkeypatch.setattr("scripts.jobs.sync_media_library.load_state", lambda _state: {})
    monkeypatch.setattr("scripts.jobs.sync_media_library.sha256_file", lambda _path: "s")
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.probe_streams",
        lambda path: (
            (_ for _ in ()).throw(RuntimeError("probe fail"))
            if path == first
            else [MediaStream(index=0, codec_type="audio", language="rus")]
        ),
    )
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.filter_to_english_audio_and_subtitles",
        lambda file_path, ffmpeg_threads, logger=None: True,
    )
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.find_non_english_audio_subtitle_streams",
        lambda streams: [0],
    )
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.remove_leftover_temp_files",
        lambda _root: [],
    )
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.save_state",
        lambda _state, _payload: None,
    )

    keep_only_english_audio_and_subtitles(
        cfg,
        logger=setup_script_logger("sync_exceptions", cfg.log_file),
    )


def test_sync_keep_only_marks_clean_files_in_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
        "scripts.jobs.sync_media_library.collect_relative_media_files",
        lambda root, extensions: ["ok.mkv"],
    )
    monkeypatch.setattr("scripts.jobs.sync_media_library.load_state", lambda _state: {})
    monkeypatch.setattr("scripts.jobs.sync_media_library.sha256_file", lambda _path: "digest")
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.probe_streams",
        lambda _path: [MediaStream(index=0, codec_type="audio", language="eng")],
    )
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.find_non_english_audio_subtitle_streams",
        lambda _streams: [],
    )
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.remove_leftover_temp_files",
        lambda _root: [],
    )
    monkeypatch.setattr(
        "scripts.jobs.sync_media_library.save_state",
        lambda _state, payload: saved.update(payload),
    )

    keep_only_english_audio_and_subtitles(
        cfg,
        logger=setup_script_logger("sync_clean_state", cfg.log_file),
    )
    assert "ok.mkv" in saved
