#!/usr/bin/env python3
"""Sync AGENTS.md from central AGENTS fragments."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

RAW_BASE_URL = "https://raw.githubusercontent.com/SergejSchweizer/agents/main"
FRAGMENTS = ['fragments/00_purpose.md', 'fragments/10_core_rules.md', 'fragments/20_architecture.md', 'fragments/30_code_review.md', 'fragments/40_testing.md', 'fragments/50_security_and_end_goal.md']


def download_text(url: str) -> str:
    with urlopen(url, timeout=15) as response:
        return response.read().decode("utf-8")


def build_agents_content() -> str:
    parts = []
    for fragment in FRAGMENTS:
        url = f"{RAW_BASE_URL}/{fragment}"
        print(f"[agents-sync] Downloading {url}")
        parts.append(download_text(url).rstrip())
    return "\n\n".join(parts) + "\n"


def stage_agents_file(repo_root: Path) -> None:
    result = subprocess.run(
        ["git", "add", "AGENTS.md"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "unknown error"
        print(f"[agents-sync] Warning: Could not stage AGENTS.md: {message}")
    else:
        print("[agents-sync] Staged updated AGENTS.md")


def main() -> int:
    repo_root = Path.cwd()
    agents_path = repo_root / "AGENTS.md"

    try:
        remote_content = build_agents_content()
    except URLError as exc:
        print(f"[agents-sync] Warning: Could not download AGENTS fragments: {exc}")
        print("[agents-sync] Continuing without updates.")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[agents-sync] Warning: Unexpected download error: {exc}")
        print("[agents-sync] Continuing without updates.")
        return 0

    local_content = agents_path.read_text(encoding="utf-8") if agents_path.exists() else ""
    if local_content == remote_content:
        print("[agents-sync] AGENTS.md already up to date.")
        return 0

    agents_path.write_text(remote_content, encoding="utf-8")
    print("[agents-sync] Updated AGENTS.md from central fragments.")
    stage_agents_file(repo_root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
