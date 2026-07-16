from __future__ import annotations

from pathlib import Path

from nas_scripts.cli import main as cli_main
from nas_scripts.config.organize_temp_media import (
    OrganizeTempMediaConfig,
    load_organize_temp_downloads_config,
)
from nas_scripts.jobs.organize_temp_downloads import main as downloads_main
from nas_scripts.jobs.organize_temp_media import main, organize_files
from nas_scripts.utils.images import (
    build_destination_dir,
    collect_matching_files,
    collect_top_level_matching_files,
    month_folder_name,
)
from nas_scripts.utils.logging import setup_script_logger
from ..factories import make_organize_config as make_config


JOB_MODULE = Path("src/nas_scripts/jobs/organize_temp_media.py")


def _record_cli_call(called: dict[str, bool | None], reorganize_existing: bool | None) -> int:
    called["reorganize_existing"] = reorganize_existing
    return 0


def _record_reorganize_flag(seen: dict[str, bool], reorganize_existing: bool) -> int:
    seen["reorganize_existing"] = reorganize_existing
    return 0


def test_collect_matching_files_filters_extensions(tmp_path: Path) -> None:
    root = tmp_path / "temp"
    root.mkdir()
    (root / "photo.jpg").write_text("jpg", encoding="utf-8")
    (root / "raw.arw").write_text("raw", encoding="utf-8")
    (root / "note.txt").write_text("txt", encoding="utf-8")

    matches = collect_matching_files(root, ("jpg", "arw"))

    assert [path.name for path in matches] == ["photo.jpg", "raw.arw"]


def test_collect_top_level_matching_files_ignores_nested_directories(tmp_path: Path) -> None:
    root = tmp_path / "temp"
    nested = root / "2021-04"
    nested.mkdir(parents=True)
    root.mkdir(exist_ok=True)
    (root / "photo.jpg").write_text("jpg", encoding="utf-8")
    (nested / "old_photo.jpg").write_text("jpg", encoding="utf-8")

    matches = collect_top_level_matching_files(root, ("jpg",))

    assert [path.name for path in matches] == ["photo.jpg"]


def test_job_module_stays_isolated_from_other_script_modules() -> None:
    source = JOB_MODULE.read_text(encoding="utf-8")

    assert "nas_scripts.jobs.sync_media_library" not in source
    assert "nas_scripts.config.sync_media_library" not in source
    assert "nas_scripts.utils.media" not in source


def test_cli_runs_organize_temp_media_command(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["nas-scripts", "organize-temp-media"])
    monkeypatch.setattr(
        "nas_scripts.cli.organize_temp_media_main",
        lambda reorganize_existing=None: 0,
    )
    assert cli_main() == 0


def test_cli_passes_reorganize_existing_flag(monkeypatch) -> None:
    called: dict[str, bool | None] = {}

    monkeypatch.setattr(
        "sys.argv",
        ["nas-scripts", "organize-temp-media", "--reorganize-existing"],
    )
    monkeypatch.setattr(
        "nas_scripts.cli.organize_temp_media_main",
        lambda reorganize_existing=None: _record_cli_call(called, reorganize_existing),
    )

    assert cli_main() == 0
    assert called["reorganize_existing"] is True


def test_cli_runs_organize_temp_downloads_command(monkeypatch) -> None:
    called: dict[str, bool | None] = {}

    monkeypatch.setattr(
        "sys.argv",
        ["nas-scripts", "organize-temp-downloads", "--reorganize-existing"],
    )
    monkeypatch.setattr(
        "nas_scripts.cli.organize_temp_downloads_main",
        lambda reorganize_existing=None: _record_cli_call(called, reorganize_existing),
    )

    assert cli_main() == 0
    assert called["reorganize_existing"] is True


def test_load_organize_temp_downloads_config_uses_downloads_defaults(monkeypatch) -> None:
    monkeypatch.delenv("TEMP_DIR", raising=False)
    monkeypatch.delenv("LOCK_FILE", raising=False)

    config = load_organize_temp_downloads_config()

    assert config.script_name == "organize_temp_downloads"
    assert config.temp_dir == Path("/volume1/Temp/Downloads")
    assert config.lock_file == Path("/tmp/organize_temp_downloads.lock")
    assert config.destination_layout == "month_only"


def test_build_destination_dir_uses_raw_subfolder_for_raw_extensions(tmp_path: Path) -> None:
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    path = temp_dir / "capture.arw"
    path.write_text("raw", encoding="utf-8")

    destination = build_destination_dir(
        path,
        temp_dir=temp_dir,
        raw_extensions=("arw", "ARW"),
        video_extensions=("mp4", "MP4"),
    )

    assert destination.name == "raw"
    assert destination.parent.name == month_folder_name(path)


def test_build_destination_dir_uses_img_subfolder_for_regular_images(tmp_path: Path) -> None:
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    path = temp_dir / "photo.jpg"
    path.write_text("img", encoding="utf-8")

    destination = build_destination_dir(
        path,
        temp_dir=temp_dir,
        raw_extensions=("arw", "ARW"),
        video_extensions=("mp4", "MP4"),
    )

    assert destination.name == "img"
    assert destination.parent.name == month_folder_name(path)


def test_build_destination_dir_uses_vid_subfolder_for_videos(tmp_path: Path) -> None:
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    path = temp_dir / "clip.mp4"
    path.write_text("vid", encoding="utf-8")

    destination = build_destination_dir(
        path,
        temp_dir=temp_dir,
        raw_extensions=("arw", "ARW"),
        video_extensions=("mp4", "MP4"),
    )

    assert destination.name == "vid"
    assert destination.parent.name == month_folder_name(path)


def test_organize_files_moves_images_raw_files_and_videos(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.temp_dir.mkdir(parents=True)
    photo = config.temp_dir / "photo.jpg"
    raw = config.temp_dir / "capture.arw"
    video = config.temp_dir / "clip.mp4"
    photo.write_text("jpg", encoding="utf-8")
    raw.write_text("raw", encoding="utf-8")
    video.write_text("vid", encoding="utf-8")
    photo_month = month_folder_name(photo)
    logger = setup_script_logger(f"organize_temp_media_test_{tmp_path.name}", config.log_file)

    assert organize_files(config, logger=logger) == 0

    month_dir = config.temp_dir / photo_month
    assert (month_dir / "img" / "photo.jpg").exists()
    assert (month_dir / "raw" / "capture.arw").exists()
    assert (month_dir / "vid" / "clip.mp4").exists()
    assert not photo.exists()
    assert not raw.exists()
    assert not video.exists()


def test_organize_files_moves_downloads_into_month_folder_only(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config = OrganizeTempMediaConfig(
        script_name="organize_temp_downloads",
        temp_dir=config.temp_dir,
        lock_file=config.lock_file,
        log_dir=config.log_dir,
        reorganize_existing=config.reorganize_existing,
        file_extensions=config.file_extensions,
        raw_extensions=config.raw_extensions,
        video_extensions=config.video_extensions,
        owner_user=config.owner_user,
        owner_group=config.owner_group,
        conflict_policy=config.conflict_policy,
        destination_layout="month_only",
    )
    config.temp_dir.mkdir(parents=True)
    photo = config.temp_dir / "photo.jpg"
    video = config.temp_dir / "clip.mp4"
    photo.write_text("jpg", encoding="utf-8")
    video.write_text("vid", encoding="utf-8")
    photo_month = month_folder_name(photo)
    video_month = month_folder_name(video)
    logger = setup_script_logger(f"organize_temp_downloads_test_{tmp_path.name}", config.log_file)

    assert organize_files(config, logger=logger) == 0

    assert (config.temp_dir / photo_month / "photo.jpg").exists()
    assert (config.temp_dir / video_month / "clip.mp4").exists()
    assert not (config.temp_dir / photo_month / "img" / "photo.jpg").exists()
    assert not (config.temp_dir / video_month / "vid" / "clip.mp4").exists()
    assert not photo.exists()
    assert not video.exists()


def test_organize_files_overwrites_existing_destination_file(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.temp_dir.mkdir(parents=True)
    source = config.temp_dir / "photo.jpg"
    source.write_text("new", encoding="utf-8")
    month_dir = config.temp_dir / month_folder_name(source) / "img"
    month_dir.mkdir(parents=True)
    destination = month_dir / source.name
    destination.write_text("old", encoding="utf-8")
    logger = setup_script_logger(
        f"organize_temp_media_overwrite_{tmp_path.name}",
        config.log_file,
    )

    assert organize_files(config, logger=logger) == 0

    assert destination.read_text(encoding="utf-8") == "new"
    assert not source.exists()


def test_organize_files_skips_already_organized_file(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.temp_dir.mkdir(parents=True)
    month_dir = config.temp_dir / "2021-04" / "img"
    month_dir.mkdir(parents=True)
    destination = month_dir / "photo.jpg"
    destination.write_text("jpg", encoding="utf-8")
    logger = setup_script_logger(
        f"organize_temp_media_skip_{tmp_path.name}",
        config.log_file,
    )

    assert organize_files(config, logger=logger) == 0
    assert destination.read_text(encoding="utf-8") == "jpg"


def test_organize_files_does_not_reorganize_nested_files_by_default(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.temp_dir.mkdir(parents=True)
    legacy_dir = config.temp_dir / "2021-04"
    legacy_dir.mkdir()
    nested_photo = legacy_dir / "photo.jpg"
    nested_photo.write_text("jpg", encoding="utf-8")
    logger = setup_script_logger(
        f"organize_temp_media_default_nested_{tmp_path.name}",
        config.log_file,
    )

    assert organize_files(config, logger=logger) == 0
    assert nested_photo.exists()
    assert not (legacy_dir / "img" / "photo.jpg").exists()


def test_organize_files_logs_when_no_files_are_found(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.temp_dir.mkdir(parents=True)
    logger = setup_script_logger(
        f"organize_temp_media_empty_{tmp_path.name}",
        config.log_file,
    )

    assert organize_files(config, logger=logger) == 0

    for handler in logger.handlers:
        handler.flush()

    log_content = config.log_file.read_text(encoding="utf-8")
    assert "Found 0 matching file(s)" in log_content
    assert "No matching files found. Nothing to move." in log_content
    assert "Organization completed." in log_content


def test_organize_files_reorganizes_nested_files_when_enabled(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config = OrganizeTempMediaConfig(
        script_name=config.script_name,
        temp_dir=config.temp_dir,
        lock_file=config.lock_file,
        log_dir=config.log_dir,
        reorganize_existing=True,
        file_extensions=config.file_extensions,
        raw_extensions=config.raw_extensions,
        video_extensions=config.video_extensions,
        owner_user=config.owner_user,
        owner_group=config.owner_group,
        conflict_policy=config.conflict_policy,
    )
    config.temp_dir.mkdir(parents=True)
    legacy_dir = config.temp_dir / "2021-04"
    legacy_dir.mkdir()
    nested_photo = legacy_dir / "photo.jpg"
    nested_photo.write_text("jpg", encoding="utf-8")
    expected_destination = build_destination_dir(
        nested_photo,
        temp_dir=config.temp_dir,
        raw_extensions=config.raw_extensions,
        video_extensions=config.video_extensions,
    )
    logger = setup_script_logger(
        f"organize_temp_media_reorganize_nested_{tmp_path.name}",
        config.log_file,
    )

    assert organize_files(config, logger=logger) == 0
    assert not nested_photo.exists()
    assert (expected_destination / "photo.jpg").exists()


def test_main_can_override_reorganize_existing(monkeypatch, tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.temp_dir.mkdir(parents=True)
    seen: dict[str, bool] = {}

    monkeypatch.setattr(
        "nas_scripts.jobs.organize_temp_media.load_organize_temp_media_config",
        lambda: config,
    )
    monkeypatch.setattr(
        "nas_scripts.jobs.organize_temp_media.organize_files",
        lambda cfg, logger: _record_reorganize_flag(seen, cfg.reorganize_existing),
    )

    assert main(reorganize_existing=True) == 0
    assert seen["reorganize_existing"] is True


def test_downloads_main_uses_downloads_config(monkeypatch, tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.temp_dir.mkdir(parents=True)
    seen: dict[str, bool] = {}

    monkeypatch.setattr(
        "nas_scripts.jobs.organize_temp_downloads.load_organize_temp_downloads_config",
        lambda: config,
    )
    monkeypatch.setattr(
        "nas_scripts.jobs.organize_temp_media.organize_files",
        lambda cfg, logger: _record_reorganize_flag(seen, cfg.reorganize_existing),
    )

    assert downloads_main(reorganize_existing=True) == 0
    assert seen["reorganize_existing"] is True


def test_organize_files_logs_progress(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.temp_dir.mkdir(parents=True)
    photo = config.temp_dir / "photo.jpg"
    photo.write_text("jpg", encoding="utf-8")
    logger = setup_script_logger(f"organize_temp_media_log_{tmp_path.name}", config.log_file)

    assert organize_files(config, logger=logger) == 0

    for handler in logger.handlers:
        handler.flush()

    log_content = config.log_file.read_text(encoding="utf-8")
    assert "Found 1 matching file(s)" in log_content
    assert "Moved" in log_content


def test_organize_files_skips_existing_destination_when_policy_is_skip(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config = OrganizeTempMediaConfig(
        script_name=config.script_name,
        temp_dir=config.temp_dir,
        lock_file=config.lock_file,
        log_dir=config.log_dir,
        reorganize_existing=config.reorganize_existing,
        file_extensions=config.file_extensions,
        raw_extensions=config.raw_extensions,
        video_extensions=config.video_extensions,
        owner_user=config.owner_user,
        owner_group=config.owner_group,
        conflict_policy="skip",
    )
    config.temp_dir.mkdir(parents=True)
    source = config.temp_dir / "photo.jpg"
    source.write_text("new", encoding="utf-8")
    destination = config.temp_dir / month_folder_name(source) / "img" / source.name
    destination.parent.mkdir(parents=True)
    destination.write_text("old", encoding="utf-8")
    logger = setup_script_logger(f"organize_temp_media_skip_conflict_{tmp_path.name}", config.log_file)

    assert organize_files(config, logger=logger) == 0
    assert source.exists()
    assert destination.read_text(encoding="utf-8") == "old"


def test_organize_files_renames_existing_destination_when_policy_is_rename(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config = OrganizeTempMediaConfig(
        script_name=config.script_name,
        temp_dir=config.temp_dir,
        lock_file=config.lock_file,
        log_dir=config.log_dir,
        reorganize_existing=config.reorganize_existing,
        file_extensions=config.file_extensions,
        raw_extensions=config.raw_extensions,
        video_extensions=config.video_extensions,
        owner_user=config.owner_user,
        owner_group=config.owner_group,
        conflict_policy="rename",
    )
    config.temp_dir.mkdir(parents=True)
    source = config.temp_dir / "photo.jpg"
    source.write_text("new", encoding="utf-8")
    destination = config.temp_dir / month_folder_name(source) / "img" / source.name
    destination.parent.mkdir(parents=True)
    destination.write_text("old", encoding="utf-8")
    logger = setup_script_logger(
        f"organize_temp_media_rename_conflict_{tmp_path.name}",
        config.log_file,
    )

    assert organize_files(config, logger=logger) == 0
    assert not source.exists()
    assert destination.read_text(encoding="utf-8") == "old"
    assert (destination.parent / "photo.1.jpg").read_text(encoding="utf-8") == "new"
