"""Verification cache policy for the media sync workflow."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


FILTER_POLICY_VERSION = 3


class CacheValidationStrategy(Protocol):
    """Contract for cache-entry validation strategies."""

    def is_valid(
        self,
        previous: dict[str, Any],
        *,
        current_size: int,
        current_mtime_ns: int,
        current_checksum: str | None,
    ) -> bool:
        """Return whether the previous entry still applies."""


@dataclass(frozen=True)
class SyncUpdateDecision:
    """Decision object for the source->destination update policy."""

    should_copy: bool
    reason: str


class SyncUpdatePolicy(Protocol):
    """Strategy contract for source/destination update decisions."""

    def decide(
        self,
        *,
        relpath: str,
        source_path: Path,
        dest_path: Path,
        previous: dict[str, Any] | None,
    ) -> SyncUpdateDecision:
        """Return whether to copy and why."""


class DefaultSyncUpdatePolicy:
    """Default update policy that preserves filtered destination outputs."""

    def decide(
        self,
        *,
        relpath: str,
        source_path: Path,
        dest_path: Path,
        previous: dict[str, Any] | None,
    ) -> SyncUpdateDecision:
        del relpath
        if files_are_definitely_equal_by_stat(source_path, dest_path):
            return SyncUpdateDecision(should_copy=False, reason="stat_match")

        source_stat = source_path.stat()
        dest_stat = dest_path.stat()

        if is_verified_cache_entry_valid(
            previous,
            current_size=dest_stat.st_size,
            current_mtime_ns=dest_stat.st_mtime_ns,
            validation_strategies=(_STAT_VALIDATION_STRATEGY,),
        ) and source_stat.st_mtime_ns <= dest_stat.st_mtime_ns:
            return SyncUpdateDecision(
                should_copy=False,
                reason="preserve_filtered_verified_current_policy",
            )

        if is_verified_state_entry(previous) and source_stat.st_mtime_ns <= dest_stat.st_mtime_ns:
            return SyncUpdateDecision(
                should_copy=False,
                reason="preserve_filtered_verified_legacy_policy",
            )

        return SyncUpdateDecision(should_copy=True, reason="checksum_required")


class StatValidationStrategy:
    """Validate cache entries using deterministic file stat fields."""

    def is_valid(
        self,
        previous: dict[str, Any],
        *,
        current_size: int,
        current_mtime_ns: int,
        current_checksum: str | None,
    ) -> bool:
        del current_checksum
        previous_size = previous.get("size")
        previous_mtime_ns = previous.get("mtime_ns")
        if not isinstance(previous_size, int) or not isinstance(previous_mtime_ns, int):
            return False
        if previous_size != current_size:
            return False
        if previous_mtime_ns == current_mtime_ns:
            return True
        return previous_mtime_ns // 1_000_000_000 == current_mtime_ns // 1_000_000_000


class ChecksumValidationStrategy:
    """Fallback validation for entries that must be reconciled by checksum."""

    def is_valid(
        self,
        previous: dict[str, Any],
        *,
        current_size: int,
        current_mtime_ns: int,
        current_checksum: str | None,
    ) -> bool:
        del current_size, current_mtime_ns
        if current_checksum is None:
            return False
        return previous.get("sha256") == current_checksum


_STAT_VALIDATION_STRATEGY = StatValidationStrategy()
_CHECKSUM_VALIDATION_STRATEGY = ChecksumValidationStrategy()
DEFAULT_SYNC_UPDATE_POLICY = DefaultSyncUpdatePolicy()


def cache_is_eligible_for_reuse(previous: dict[str, Any] | None) -> bool:
    """Fast contract check before strategy-based validation."""
    if previous is None:
        return False
    if previous.get("policy_version") != FILTER_POLICY_VERSION:
        return False
    return bool(previous.get("verified", False))


def is_verified_state_entry(previous: dict[str, Any] | None) -> bool:
    """Check whether a state entry marks a file as verified, independent of policy version."""
    if previous is None:
        return False
    return bool(previous.get("verified", False))


def build_cache_validation_strategies(
    mode: str,
) -> tuple[CacheValidationStrategy, ...]:
    """Factory for selecting cache validation strategies."""
    if mode == "stat_only":
        return (_STAT_VALIDATION_STRATEGY,)
    return (_STAT_VALIDATION_STRATEGY, _CHECKSUM_VALIDATION_STRATEGY)


def build_verified_state_entry(
    *,
    checksum: str,
    size: int,
    mtime_ns: int,
) -> dict[str, Any]:
    """Construct a normalized cache entry for a verified media file."""
    return {
        "sha256": checksum,
        "verified": True,
        "policy_version": FILTER_POLICY_VERSION,
        "size": size,
        "mtime_ns": mtime_ns,
    }


def upgrade_verified_state_entry(
    previous: dict[str, Any],
    *,
    size: int,
    mtime_ns: int,
) -> dict[str, Any]:
    """Upgrade a verified cache entry to the current policy version."""
    return {
        **previous,
        "verified": True,
        "policy_version": FILTER_POLICY_VERSION,
        "size": size,
        "mtime_ns": mtime_ns,
    }


def is_verified_cache_entry_valid(
    previous: dict[str, Any] | None,
    *,
    current_size: int,
    current_mtime_ns: int,
    current_checksum: str | None = None,
    validation_strategies: tuple[CacheValidationStrategy, ...] | None = None,
) -> bool:
    """Decide whether a cached verification result still applies."""
    if not cache_is_eligible_for_reuse(previous):
        return False
    assert previous is not None

    strategies = validation_strategies or build_cache_validation_strategies(
        "stat_then_checksum" if current_checksum is not None else "stat_only"
    )
    return any(
        strategy.is_valid(
            previous,
            current_size=current_size,
            current_mtime_ns=current_mtime_ns,
            current_checksum=current_checksum,
        )
        for strategy in strategies
    )


def files_are_definitely_equal_by_stat(source_path: Path, dest_path: Path) -> bool:
    """Fast-path equality check based on size and near-equal mtime."""
    source_stat = source_path.stat()
    dest_stat = dest_path.stat()
    mtime_delta_ns = abs(source_stat.st_mtime_ns - dest_stat.st_mtime_ns)
    return source_stat.st_size == dest_stat.st_size and mtime_delta_ns <= 1_000_000_000