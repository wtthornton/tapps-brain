"""Conflict detection helpers for ``MemoryStore.save``.

Extracted from ``store.py`` (TAP-602) to keep the save path readable.  The
functions here are stateless: callers snapshot ``_entries`` under the store
lock and pass the list in, then apply the returned mutations back under the
lock themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tapps_brain.models import MemoryEntry


@dataclass(frozen=True)
class ConflictPlan:
    """Plan describing how the save path should invalidate conflicting entries.

    ``invalidations`` is a list of (conflict_key, reason) pairs.  ``now`` is
    the shared ISO timestamp used for both ``invalid_at`` on each conflict
    and ``valid_at`` on the new incoming entry — keeping the temporal chain
    (EPIC-004) coherent.  ``conflict_keys`` is pre-computed for the structured
    warning log the caller emits.
    """

    now: str
    invalidations: list[tuple[str, str]]
    conflict_keys: list[str]
    audit: list[dict[str, object]]
    similarity_threshold: float


def resolve_similarity_threshold(profile: object | None) -> float:
    """Return the effective similarity threshold for save-time conflict checks.

    Falls back to :class:`ConflictCheckConfig` defaults when no profile or
    ``conflict_check`` block is available.
    """
    cc = getattr(profile, "conflict_check", None) if profile is not None else None
    if cc is not None:
        return float(cc.effective_similarity_threshold())
    from tapps_brain.profile import ConflictCheckConfig

    return float(ConflictCheckConfig().effective_similarity_threshold())


def plan_conflicts(
    *,
    key: str,
    value: str,
    tier: str,
    entries_snapshot: list[MemoryEntry],
    similarity_threshold: float,
    now: str,
) -> ConflictPlan | None:
    """Detect conflicts and return a :class:`ConflictPlan`, or ``None``.

    The returned plan only references entries whose ``invalid_at`` is still
    ``None``, so the caller never has to re-check that invariant.  The plan
    is pure data — callers are responsible for lock-held mutation and
    (best-effort) persistence of the invalidated entries.
    """
    from tapps_brain.contradictions import (
        detect_save_conflicts,
        format_save_conflict_reason,
    )

    hits = detect_save_conflicts(
        value,
        tier,
        entries_snapshot,
        similarity_threshold,
        exclude_key=key,
    )
    if not hits:
        return None

    conflict_keys = [h.entry.key for h in hits]
    audit: list[dict[str, object]] = [
        {
            "key": h.entry.key,
            "similarity": round(h.similarity, 4),
            "tier": (h.entry.tier.value if hasattr(h.entry.tier, "value") else str(h.entry.tier)),
        }
        for h in hits
    ]

    invalidations: list[tuple[str, str]] = []
    for hit in hits:
        if hit.entry.invalid_at is not None:
            continue
        reason = format_save_conflict_reason(
            incoming_key=key,
            tier=tier,
            similarity=hit.similarity,
        )
        invalidations.append((hit.entry.key, reason))

    return ConflictPlan(
        now=now,
        invalidations=invalidations,
        conflict_keys=conflict_keys,
        audit=audit,
        similarity_threshold=similarity_threshold,
    )
