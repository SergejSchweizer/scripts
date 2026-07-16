from __future__ import annotations

from pathlib import Path

from nas_scripts.utils.state import load_state


def test_load_state_returns_empty_for_invalid_json(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    state_file.write_text("{broken", encoding="utf-8")
    assert load_state(state_file) == {}