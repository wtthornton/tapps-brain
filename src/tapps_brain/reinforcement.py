"""Reinforcement system for memory entries.

Enables agents and humans to reinforce memories, resetting the decay
clock and optionally boosting confidence within source-based ceilings.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog

from tapps_brain.decay import DecayConfig, _get_ceiling
from tapps_brain.models import MemoryEntry  # noqa: TC001

logger = structlog.get_logger(__name__)

# Maximum allowed confidence boost per reinforcement.
_MAX_CONFIDENCE_BOOST = 0.2


def reinforce(
    entry: MemoryEntry,
    config: DecayConfig,
    *,
    confidence_boost: float = 0.0,
    now: datetime | None = None,
) -> dict[str, object]:
    """Reinforce a memory entry, resetting its decay clock.

    Returns a dict of fields to update on the entry via
    ``store.update_fields()``. Does NOT mutate the entry directly.

    Parameters:
        entry: The memory entry to reinforce.
        config: Decay configuration for ceiling enforcement.
        confidence_boost: Optional confidence increase (0.0-0.2).
        now: Override for current time (for testing).

    Returns:
        Dict of field names to new values for ``update_fields()``.
    """
    if now is None:
        now = datetime.now(tz=UTC)

    # Clamp boost
    boost = max(0.0, min(confidence_boost, _MAX_CONFIDENCE_BOOST))

    # Calculate new confidence with ceiling enforcement
    ceiling = _get_ceiling(entry.source, config)
    new_confidence = min(entry.confidence + boost, ceiling)

    now_iso = now.isoformat()

    return {
        "last_reinforced": now_iso,
        "reinforce_count": entry.reinforce_count + 1,
        "confidence": new_confidence,
        "updated_at": now_iso,
    }
