"""Environment parsing helpers for NAS script configuration."""

from __future__ import annotations

from pathlib import Path


def env_path(value: str | None, default: Path) -> Path:
    """Parse a path environment value with a default fallback."""
    return Path(value) if value else default


def env_csv(value: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    """Parse a comma-separated environment value into normalized tokens."""
    if not value:
        return default
    parts = [part.strip().lower() for part in value.split(",") if part.strip()]
    return tuple(parts) if parts else default


def env_bool(value: str | None, *, default: bool = False) -> bool:
    """Parse a permissive boolean environment value."""
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def env_int(value: str | None, *, default: int) -> int:
    """Parse an integer environment value with a safe default fallback."""
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def env_choice(value: str | None, *, choices: set[str], default: str) -> str:
    """Parse a lower-cased choice environment value with a default fallback."""
    if value is None:
        return default
    normalized = value.strip().lower()
    return normalized if normalized in choices else default
