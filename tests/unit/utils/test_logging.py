from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from scripts.utils.logging import LOG_DATE_FORMAT, LOG_FORMAT, setup_script_logger


def test_setup_logger_uses_only_requested_local_log_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[Path] = []
    local_log = tmp_path / ".logs" / "local_test.log"

    class DummyFileHandler(logging.Handler):
        def emit(self, _record: logging.LogRecord) -> None:
            return None

    def fake_handler(path, *args, **_kwargs):  # type: ignore[no-untyped-def]
        path_obj = Path(path)
        calls.append(path_obj)
        return DummyFileHandler()

    monkeypatch.setattr("scripts.utils.logging.TimedRotatingFileHandler", fake_handler)
    logger = setup_script_logger("local_test", local_log)
    assert len(logger.handlers) >= 2
    assert calls == [local_log]


def test_setup_logger_uses_one_format_for_all_handlers(tmp_path: Path) -> None:
    log_file = tmp_path / ".logs" / "format_test.log"
    logger = setup_script_logger("format_test", log_file)

    assert len(logger.handlers) == 2
    for handler in logger.handlers:
        assert handler.formatter is not None
        assert handler.formatter._fmt == LOG_FORMAT
        assert handler.formatter.datefmt == LOG_DATE_FORMAT


def test_setup_logger_handles_local_log_path_failing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "scripts.utils.logging.TimedRotatingFileHandler",
        lambda *args, **_kwargs: (_ for _ in ()).throw(OSError("nope")),
    )
    logger = setup_script_logger("no_file_test", tmp_path / ".logs" / "no_file_test.log")
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

    from scripts.utils.logging import _maintain_log_archives

    _maintain_log_archives(log_file, now=now)

    assert active_log.exists()
    assert recent_rotated.exists()
    assert not compressible_rotated.exists()
    assert compressible_rotated.with_name(f"{compressible_rotated.name}.gz").exists()
    assert not expired_archive.exists()
    assert unrelated_file.exists()
