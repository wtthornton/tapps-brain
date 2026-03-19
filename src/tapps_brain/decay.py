"""Time-based decay engine for memory confidence.

Recalculates confidence on read using exponential decay with
tier-specific half-lives. No background threads or timers -
decay is computed lazily when memories are accessed.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from tapps_brain.models import MemoryEntry, MemorySource, MemoryTier

# Default threshold below which a memory is considered stale.
_DEFAULT_STALE_THRESHOLD = 0.3

# Seconds per day for time calculations.
_SECONDS_PER_DAY = 86400.0

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class DecayConfig(BaseModel):
    """Configuration for memory confidence decay."""

    # Tier-specific half-lives (days)
    architectural_half_life_days: int = Field(default=180, ge=1)
    pattern_half_life_days: int = Field(default=60, ge=1)
    procedural_half_life_days: int = Field(
        default=30, ge=1, description="Epic 65.11: how-to workflows, steps (between pattern=60, context=14)"
    )
    context_half_life_days: int = Field(default=14, ge=1)

    # Confidence bounds
    confidence_floor: float = Field(default=0.1, ge=0.0, le=1.0)
    human_confidence_ceiling: float = Field(default=0.95, ge=0.0, le=1.0)
    agent_confidence_ceiling: float = Field(default=0.85, ge=0.0, le=1.0)
    inferred_confidence_ceiling: float = Field(default=0.70, ge=0.0, le=1.0)
    system_confidence_ceiling: float = Field(default=0.95, ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# Half-life lookup
# ---------------------------------------------------------------------------

_TIER_HALF_LIFE_ATTR: dict[MemoryTier, str] = {
    MemoryTier.architectural: "architectural_half_life_days",
    MemoryTier.pattern: "pattern_half_life_days",
    MemoryTier.procedural: "procedural_half_life_days",
    MemoryTier.context: "context_half_life_days",
}

_SOURCE_CEILING_ATTR: dict[MemorySource, str] = {
    MemorySource.human: "human_confidence_ceiling",
    MemorySource.agent: "agent_confidence_ceiling",
    MemorySource.inferred: "inferred_confidence_ceiling",
    MemorySource.system: "system_confidence_ceiling",
}


# ---------------------------------------------------------------------------
# Core decay functions
# ---------------------------------------------------------------------------


def _get_half_life(tier: MemoryTier, config: DecayConfig) -> int:
    """Return the half-life in days for a given tier."""
    attr = _TIER_HALF_LIFE_ATTR[tier]
    return int(getattr(config, attr))


def _get_ceiling(source: MemorySource, config: DecayConfig) -> float:
    """Return the confidence ceiling for a given source type."""
    attr = _SOURCE_CEILING_ATTR.get(source, "agent_confidence_ceiling")
    return float(getattr(config, attr))


def _days_since(iso_timestamp: str, now: datetime | None = None) -> float:
    """Return fractional days elapsed since an ISO-8601 timestamp."""
    if now is None:
        now = datetime.now(tz=UTC)
    try:
        ts = datetime.fromisoformat(iso_timestamp)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return 0.0
    delta = now - ts
    return max(delta.total_seconds() / _SECONDS_PER_DAY, 0.0)


def _decay_reference_time(entry: MemoryEntry) -> str:
    """Return the timestamp from which decay is measured.

    Uses ``last_reinforced`` if set, otherwise ``updated_at``.
    """
    if entry.last_reinforced:
        return entry.last_reinforced
    return entry.updated_at


def calculate_decayed_confidence(
    entry: MemoryEntry,
    config: DecayConfig,
    *,
    now: datetime | None = None,
) -> float:
    """Calculate the time-decayed confidence for a memory entry.

    Uses exponential decay: ``confidence * 0.5^(days / half_life)``.
    The result is clamped to ``[confidence_floor, source_ceiling]``.
    """
    half_life = _get_half_life(entry.tier, config)
    ceiling = _get_ceiling(entry.source, config)

    ref_time = _decay_reference_time(entry)
    days = _days_since(ref_time, now)

    # Exponential decay: confidence * 0.5^(days / half_life)
    decay_factor = math.pow(0.5, days / half_life)
    decayed = entry.confidence * decay_factor

    # Clamp to [floor, ceiling]
    return max(config.confidence_floor, min(ceiling, decayed))


def is_stale(
    entry: MemoryEntry,
    config: DecayConfig,
    threshold: float = _DEFAULT_STALE_THRESHOLD,
    *,
    now: datetime | None = None,
) -> bool:
    """Return True if the memory's effective confidence is below *threshold*."""
    effective = calculate_decayed_confidence(entry, config, now=now)
    return effective < threshold


def get_effective_confidence(
    entry: MemoryEntry,
    config: DecayConfig,
    *,
    now: datetime | None = None,
) -> tuple[float, bool]:
    """Return ``(decayed_confidence, is_stale)`` for a memory entry.

    This is the primary read-time API. The *is_stale* flag uses the
    default stale threshold.
    """
    decayed = calculate_decayed_confidence(entry, config, now=now)
    stale = decayed < _DEFAULT_STALE_THRESHOLD
    return (decayed, stale)
