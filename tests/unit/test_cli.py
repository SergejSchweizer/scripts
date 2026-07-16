from __future__ import annotations

import runpy

import pytest


def test_main_module_exits_with_cli_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("nas_scripts.cli.main", lambda: 7)
    with pytest.raises(SystemExit) as exc_info:
        runpy.run_module("nas_scripts.__main__", run_name="__main__")
    assert exc_info.value.code == 7