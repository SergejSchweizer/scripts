"""Filesystem helpers for NAS jobs."""

from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Compute a file checksum."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        # Chunked reads avoid loading large media files entirely into memory.
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()
