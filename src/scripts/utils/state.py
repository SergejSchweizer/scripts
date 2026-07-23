"""State file helpers.

This module persists sync-media verification metadata:
it stores and restores compact JSON state so media files can be skipped when
they are already verified under the current policy.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from scripts.utils.verification_cache import VerificationState


def load_state(state_file: Path) -> VerificationState:
    """Load persisted media verification state."""
    if not state_file.exists():
        return {}
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        # Corrupt/unreadable state should not block job execution.
        return {}
    if not isinstance(state, dict):
        return {}
    return cast(VerificationState, state)


def save_state(state_file: Path, state: VerificationState) -> None:
    """Persist media verification state atomically."""
    state_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = state_file.with_suffix(f"{state_file.suffix}.tmp")
    tmp_file.write_text(
        json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    # Atomic replace prevents partial-state reads on interruption.
    tmp_file.replace(state_file)
