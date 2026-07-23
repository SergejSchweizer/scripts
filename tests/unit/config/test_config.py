from __future__ import annotations

import pytest

from scripts.config.organize_temp_media import (
    _parse_bool_env,
    _parse_conflict_policy,
    _parse_csv_env,
    load_organize_temp_downloads_config,
    load_organize_temp_media_config,
)
from scripts.config.sync_media_library import load_sync_media_library_config


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


def test_runtime_configs_ignore_external_log_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_DIR", "/volume1/Temp/.logs")
    assert load_sync_media_library_config().log_dir.as_posix() == ".logs"
    assert load_organize_temp_media_config().log_dir.as_posix() == ".logs"
    assert load_organize_temp_downloads_config().log_dir.as_posix() == ".logs"
