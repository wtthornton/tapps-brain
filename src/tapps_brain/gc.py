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


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@dataclass
class GCConfig:
    """Runtime-configurable garbage collection thresholds."""

    floor_retention_days: int = field(default=_FLOOR_RETENTION_DAYS)
    session_expiry_days: int = field(default=_SESSION_EXPIRY_DAYS)
    contradicted_threshold: float = field(default=_CONTRADICTED_ARCHIVE_THRESHOLD)

    def to_dict(self) -> dict[str, object]:
        """Return config as a plain dict."""
        return {
            "floor_retention_days": self.floor_retention_days,
            "session_expiry_days": self.session_expiry_days,
            "contradicted_threshold": self.contradicted_threshold,
        }


class GCResult(BaseModel):
    """Result of a garbage collection run."""

    archived_count: int = Field(default=0, ge=0)
    remaining_count: int = Field(default=0, ge=0)
    archived_keys: list[str] = Field(default_factory=list)


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
            if self._should_archive(entry, now):
                candidates.append(entry)
        return candidates

    def _should_archive(self, entry: MemoryEntry, now: datetime) -> bool:
        """Check if a single entry meets any archival criterion."""
        effective = calculate_decayed_confidence(entry, self._config, now=now)

        # Criterion 1: at floor confidence for extended period
        if effective <= self._config.confidence_floor:
            days_at_floor = self._days_at_floor(entry, now)
            if days_at_floor >= self._floor_retention_days:
                return True

        # Criterion 2: contradicted and low confidence
        if entry.contradicted and effective < self._contradicted_threshold:
            return True

        # Criterion 3: expired session-scoped memory
        if entry.scope == "session":
            days_since_update = _days_since_timestamp(entry.updated_at, now)
            if days_since_update >= self._session_expiry_days:
                return True

        return False

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
    ) -> None:
        """Append archived entries to a JSONL file for external visibility."""
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        now_iso = datetime.now(tz=UTC).isoformat()

        try:
            with archive_path.open("a", encoding="utf-8") as fh:
                for entry in entries:
                    record = entry.model_dump()
                    record["archived_at"] = now_iso
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            logger.warning("archive_write_failed", path=str(archive_path), exc_info=True)


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
