"""File extension matching helpers."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=32)
def normalized_extensions(extensions: tuple[str, ...]) -> frozenset[str]:
    """Normalize extension tuples once for repeated membership checks."""
    return frozenset(extension.lower() for extension in extensions)


def has_extension(path: Path, extensions: tuple[str, ...], *, allow_wildcard: bool = False) -> bool:
    """Decide whether a path suffix matches a configured extension set."""
    normalized = normalized_extensions(extensions)
    return (allow_wildcard and "*" in normalized) or path.suffix.lower().lstrip(".") in normalized