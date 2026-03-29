"""Shared test factories for tapps-brain.

Provides a single ``make_entry`` helper that covers all parameter
variants previously scattered across per-file ``_make_entry`` helpers.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from tapps_brain.models import (
    MemoryEntry,
    MemoryScope,
    MemorySource,
    MemoryTier,
)


def make_entry(
    key: str = "test-key",
    value: str = "test value",
    *,
    tier: MemoryTier | str = MemoryTier.pattern,
    confidence: float = -1.0,
    source: MemorySource | str = MemorySource.agent,
    source_agent: str = "test",
    scope: MemoryScope = MemoryScope.project,
    tags: list[str] | None = None,
    branch: str | None = None,
    created_at: str | None = None,
    updated_at: str | None = None,
    last_accessed: str | None = None,
    access_count: int = 0,
    last_reinforced: str | None = None,
    reinforce_count: int = 0,
    contradicted: bool = False,
    contradiction_reason: str | None = None,
    seeded_from: str | None = None,
    valid_at: str | None = None,
    invalid_at: str | None = None,
    superseded_by: str | None = None,
    stability: float | None = None,
    difficulty: float | None = None,
) -> MemoryEntry:
    """Create a ``MemoryEntry`` with sensible test defaults.

    Every parameter accepted by ``MemoryEntry`` can be overridden.
    Timestamp fields default to *now* when not supplied.

    The ``confidence`` default of ``-1.0`` triggers the model's
    source-based default (0.95 for human, 0.6 for agent, etc.).
    Pass an explicit float to override.
    """
    now_iso = datetime.now(tz=UTC).isoformat()

    kwargs: dict[str, Any] = {
        "key": key,
        "value": value,
        "tier": tier,
        "source": source,
        "source_agent": source_agent,
        "scope": scope,
        "tags": tags or [],
        "branch": branch,
        "created_at": created_at or now_iso,
        "updated_at": updated_at or now_iso,
        "last_accessed": last_accessed or now_iso,
        "access_count": access_count,
        "last_reinforced": last_reinforced,
        "reinforce_count": reinforce_count,
        "contradicted": contradicted,
        "contradiction_reason": contradiction_reason,
        "seeded_from": seeded_from,
        "valid_at": valid_at,
        "invalid_at": invalid_at,
        "superseded_by": superseded_by,
    }

    if confidence != -1.0:
        kwargs["confidence"] = confidence
    if stability is not None:
        kwargs["stability"] = stability
    if difficulty is not None:
        kwargs["difficulty"] = difficulty

    return MemoryEntry(**kwargs)
