"""Shared unit-test fakes."""

from __future__ import annotations


class DummyLogger:
    """Logger test double that accepts standard logging calls."""

    def info(self, *args, **_kwargs):
        return None

    def warning(self, *args, **_kwargs):
        return None

    def error(self, *args, **_kwargs):
        return None

    def exception(self, *args, **_kwargs):
        return None


class DummyResult:
    """Minimal completed-process test double."""

    def __init__(self, returncode: int = 0, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout