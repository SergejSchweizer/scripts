from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from nas_scripts.utils.logging import setup_script_logger


def test_setup_logger_falls_back_to_cwd_logs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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


def test_setup_logger_handles_both_file_paths_failing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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