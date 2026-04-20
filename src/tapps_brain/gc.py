"""Garbage collection and archival for memory entries.

Archives memories that have decayed below usefulness for sustained
periods. Archived memories are moved to a separate table and appended
to an external JSONL file for visibility.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from pydantic import BaseModel, Field

from tapps_brain.decay import (
    DecayConfig,
    _days_since,
    _decay_reference_time,
    _get_half_life,
    calculate_decayed_confidence,
)

if TYPE_CHECKING:
    from pathlib import Path

    from tapps_brain.models import MemoryEntry

logger = structlog.get_logger(__name__)

# Days a memory must remain at floor confidence before archival.
_FLOOR_RETENTION_DAYS = 30

# Days after session end before session-scoped memories are archived.
_SESSION_EXPIRY_DAYS = 7

# Confidence threshold for contradicted memory archival.
_CONTRADICTED_ARCHIVE_THRESHOLD = 0.2

# Default TTL for session index (FTS5) rows — pruned during gc().
_SESSION_INDEX_TTL_DAYS = 90


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@dataclass
class GCConfig:
    """Runtime-configurable garbage collection thresholds."""

    floor_retention_days: int = field(default=_FLOOR_RETENTION_DAYS)
    session_expiry_days: int = field(default=_SESSION_EXPIRY_DAYS)
    contradicted_threshold: float = field(default=_CONTRADICTED_ARCHIVE_THRESHOLD)
    session_index_ttl_days: int = field(default=_SESSION_INDEX_TTL_DAYS)

    def to_dict(self) -> dict[str, object]:
        """Return config as a plain dict."""
        return {
            "floor_retention_days": self.floor_retention_days,
            "session_expiry_days": self.session_expiry_days,
            "contradicted_threshold": self.contradicted_threshold,
            "session_index_ttl_days": self.session_index_ttl_days,
        }


class GCResult(BaseModel):
    """Result of a garbage collection run."""

    archived_count: int = Field(default=0, ge=0)
    remaining_count: int = Field(default=0, ge=0)
    archived_keys: list[str] = Field(default_factory=list)
    dry_run: bool = Field(
        default=False,
        description="When True, no rows were archived; archived_keys are candidates.",
    )
    reason_counts: dict[str, int] = Field(
        default_factory=dict,
        description="Counts of archive reason codes (dry-run and live runs).",
    )
    archive_bytes: int = Field(
        default=0,
        ge=0,
        description="UTF-8 bytes appended to archive JSONL this run (live runs only).",
    )
    estimated_archive_bytes: int = Field(
        default=0,
        ge=0,
        description="UTF-8 size of JSONL that would be appended (dry-run only).",
    )
    session_chunks_deleted: int = Field(
        default=0,
        ge=0,
        description="Session index (FTS5) rows pruned by TTL this run (live runs only).",
    )


class StaleCandidateDetail(BaseModel):
    """One memory entry that garbage collection would archive, with review metadata."""

    key: str
    tier: str
    reasons: list[str]
    effective_confidence: float = Field(ge=0.0, le=1.0)
    stored_confidence: float
    contradicted: bool
    scope: str
    days_at_floor: float | None = None
    days_since_update: float | None = None
    updated_at: str = ""


def aggregate_gc_reason_counts(details: list[StaleCandidateDetail]) -> dict[str, int]:
    """Count archive reason codes across stale candidates (EPIC-044 STORY-044.5)."""
    counts: dict[str, int] = {}
    for d in details:
        for reason in d.reasons:
            counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))


def archive_entries_jsonl_utf8_bytes(entries: list[MemoryEntry], archived_at_iso: str) -> int:
    """UTF-8 byte size of JSONL lines ``append_to_archive`` would write."""
    total = 0
    for entry in entries:
        record = entry.model_dump()
        record["archived_at"] = archived_at_iso
        line = json.dumps(record, ensure_ascii=False) + "\n"
        total += len(line.encode("utf-8"))
    return total


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------


class MemoryGarbageCollector:
    """Archives stale memories from the active store."""

    def __init__(
        self,
        config: DecayConfig | None = None,
        *,
        floor_retention_days: int | None = None,
        session_expiry_days: int | None = None,
        contradicted_threshold: float | None = None,
        gc_config: GCConfig | None = None,
    ) -> None:
        self._config = config or DecayConfig()
        # GCConfig takes precedence; individual kwargs override its defaults.
        _gc = gc_config or GCConfig()
        self._floor_retention_days = (
            floor_retention_days if floor_retention_days is not None else _gc.floor_retention_days
        )
        self._session_expiry_days = (
            session_expiry_days if session_expiry_days is not None else _gc.session_expiry_days
        )
        self._contradicted_threshold = (
            contradicted_threshold
            if contradicted_threshold is not None
            else _gc.contradicted_threshold
        )

    def identify_candidates(
        self,
        entries: list[MemoryEntry],
        *,
        now: datetime | None = None,
    ) -> list[MemoryEntry]:
        """Return entries that should be archived.

        Archive criteria (any one triggers archival):
        - Effective confidence <= floor for 30+ consecutive days
        - Contradicted AND effective confidence < 0.2
        - Session-scoped AND session ended 7+ days ago
        """
        if now is None:
            now = datetime.now(tz=UTC)

        candidates: list[MemoryEntry] = []
        for entry in entries:
            if self._archive_reasons(entry, now):
                candidates.append(entry)
        return candidates

    def stale_candidate_details(
        self,
        entries: list[MemoryEntry],
        *,
        now: datetime | None = None,
    ) -> list[StaleCandidateDetail]:
        """Return structured GC stale candidates (same set as :meth:`identify_candidates`)."""
        if now is None:
            now = datetime.now(tz=UTC)

        from tapps_brain.models import MemoryScope, MemoryTier

        out: list[StaleCandidateDetail] = []
        for entry in entries:
            reasons = self._archive_reasons(entry, now)
            if not reasons:
                continue
            effective = calculate_decayed_confidence(entry, self._config, now=now)
            tier_s = entry.tier.value if isinstance(entry.tier, MemoryTier) else str(entry.tier)
            scope_s = (
                entry.scope.value if isinstance(entry.scope, MemoryScope) else str(entry.scope)
            )
            days_at_floor: float | None = None
            if "floor_retention" in reasons:
                days_at_floor = self._days_at_floor(entry, now)
            days_since_update: float | None = None
            if "session_expired" in reasons:
                days_since_update = _days_since_timestamp(entry.updated_at, now)
            out.append(
                StaleCandidateDetail(
                    key=entry.key,
                    tier=tier_s,
                    reasons=reasons,
                    effective_confidence=float(effective),
                    stored_confidence=float(entry.confidence),
                    contradicted=bool(entry.contradicted),
                    scope=scope_s,
                    days_at_floor=days_at_floor,
                    days_since_update=days_since_update,
                    updated_at=entry.updated_at,
                )
            )
        return out

    def _archive_reasons(self, entry: MemoryEntry, now: datetime) -> list[str]:
        """Return non-empty list when the entry should be archived; each item is a reason code."""
        # TAP-732: stale entries are explicitly flagged for human review; never auto-archive.
        # They will remain visible (filtered from brain_recall by default) until a replacement
        # is written and the entry is superseded or manually archived.
        from tapps_brain.models import MemoryStatus

        if getattr(entry, "status", MemoryStatus.active) == MemoryStatus.stale:
            return []

        reasons: list[str] = []
        effective = calculate_decayed_confidence(entry, self._config, now=now)

        if effective <= self._config.confidence_floor:
            days_at_floor = self._days_at_floor(entry, now)
            if days_at_floor >= self._floor_retention_days:
                reasons.append("floor_retention")

        if entry.contradicted and effective < self._contradicted_threshold:
            reasons.append("contradicted_low_confidence")

        if entry.scope == "session":
            days_since_update = _days_since_timestamp(entry.updated_at, now)
            if days_since_update >= self._session_expiry_days:
                reasons.append("session_expired")

        return reasons

    def _days_at_floor(self, entry: MemoryEntry, now: datetime) -> float:
        """Estimate how long a memory has been at the confidence floor.

        Uses the decay formula in reverse to find when confidence
        first reached the floor.
        """
        half_life = _get_half_life(entry.tier, self._config)
        ref_time = _decay_reference_time(entry)
        total_days = _days_since(ref_time, now)

        if entry.confidence <= 0 or self._config.confidence_floor <= 0:
            return total_days

        # Solve: floor = confidence * 0.5^(days_to_floor / half_life)
        # days_to_floor = half_life * log2(confidence / floor)
        ratio = entry.confidence / self._config.confidence_floor
        if ratio <= 1.0:
            return total_days  # was already at or below floor from the start

        days_to_floor = half_life * math.log2(ratio)
        return max(total_days - days_to_floor, 0.0)

    @staticmethod
    def append_to_archive(
        entries: list[MemoryEntry],
        archive_path: Path,
    ) -> int:
        """Append archived entries to a JSONL file for external visibility.

        Returns:
            UTF-8 byte length appended (0 if nothing written or on I/O error).
        """
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        now_iso = datetime.now(tz=UTC).isoformat()

        try:
            total = 0
            with archive_path.open("a", encoding="utf-8") as fh:
                for entry in entries:
                    record = entry.model_dump()
                    record["archived_at"] = now_iso
                    line = json.dumps(record, ensure_ascii=False) + "\n"
                    raw = line.encode("utf-8")
                    fh.write(line)
                    total += len(raw)
            return total
        except OSError:
            logger.warning("archive_write_failed", path=str(archive_path), exc_info=True)
            return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _days_since_timestamp(iso_timestamp: str, now: datetime) -> float:
    """Return fractional days since an ISO-8601 timestamp."""
    try:
        ts = datetime.fromisoformat(iso_timestamp)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return 0.0
    delta = now - ts
    return max(delta.total_seconds() / 86400.0, 0.0)
