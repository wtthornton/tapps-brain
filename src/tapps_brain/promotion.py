"""Promotion and demotion engine for memory entries (EPIC-010).

Checks whether a memory entry qualifies for promotion to a higher
tier or demotion to a lower tier based on the active profile's layer
definitions and access patterns.

Key mechanisms:
- **Promotion** checked on reinforcement: if access_count, age, and
  confidence all exceed the layer's promotion threshold, entry moves up.
- **Demotion** checked during GC: if effective confidence is near the
  floor and the entry hasn't been accessed within its half-life, it
  moves down instead of being archived.
- **Desirable difficulty bonus**: reinforcement boost scales with
  ``(1.0 - decayed_confidence)`` — nearly-forgotten memories get bigger
  boosts (Roediger & Karpicke 2006).
- **Stability growth**: effective half-life grows with
  ``log1p(reinforce_count) * 0.3`` multiplier (Jost's First Law).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from tapps_brain.decay import (
    DecayConfig,
    _days_since,
    calculate_decayed_confidence,
)

if TYPE_CHECKING:
    from tapps_brain.models import MemoryEntry


class PromotionEngine:
    """Evaluates promotion and demotion criteria for memory entries."""

    def __init__(self, config: DecayConfig | None = None) -> None:
        self._config = config or DecayConfig()

    def check_promotion(  # noqa: PLR0911
        self,
        entry: MemoryEntry,
        profile: object,  # MemoryProfile
        *,
        now: datetime | None = None,
    ) -> str | None:
        """Check if *entry* qualifies for promotion.

        Returns the target tier name if promotion criteria are met,
        or ``None`` if no promotion should occur.

        Promotion criteria (all must be met):
        - Layer defines ``promotion_to`` (not None)
        - Layer defines ``promotion_threshold``
        - ``entry.access_count >= threshold.min_access_count``
        - ``age_days >= threshold.min_age_days``
        - ``effective_confidence >= threshold.min_confidence``
        """
        from tapps_brain.profile import MemoryProfile

        if not isinstance(profile, MemoryProfile):
            return None

        tier_name = entry.tier.value if hasattr(entry.tier, "value") else str(entry.tier)
        layer = profile.get_layer(tier_name)
        if layer is None or layer.promotion_to is None:
            return None

        if now is None:
            now = datetime.now(tz=UTC)

        # Stability-based promotion strategy
        if layer.promotion_strategy == "stability" and entry.stability > 0:
            promote_score = (
                entry.stability * math.log1p(entry.access_count) * (1 - entry.difficulty / 10)
            )
            if promote_score > layer.promotion_stability_threshold:
                return layer.promotion_to
            return None

        # Threshold-based promotion strategy (default)
        if layer.promotion_threshold is None:
            return None

        threshold = layer.promotion_threshold

        # Check access count
        if entry.access_count < threshold.min_access_count:
            return None

        # Check age
        age_days = _days_since(entry.created_at, now)
        if age_days < threshold.min_age_days:
            return None

        # Check effective confidence
        eff_conf = calculate_decayed_confidence(entry, self._config, now=now)
        if eff_conf < threshold.min_confidence:
            return None

        return layer.promotion_to

    def check_demotion(
        self,
        entry: MemoryEntry,
        profile: object,  # MemoryProfile
        *,
        now: datetime | None = None,
    ) -> str | None:
        """Check if *entry* should be demoted to a lower tier.

        Returns the target tier name if demotion criteria are met,
        or ``None`` if no demotion should occur.

        Demotion criteria (all must be met):
        - Layer defines ``demotion_to`` (not None)
        - Effective confidence near the confidence floor (within 1.5x)
        - No access within the layer's half-life period
        """
        from tapps_brain.profile import MemoryProfile

        if not isinstance(profile, MemoryProfile):
            return None

        tier_name = entry.tier.value if hasattr(entry.tier, "value") else str(entry.tier)
        layer = profile.get_layer(tier_name)
        if layer is None or layer.demotion_to is None:
            return None

        if now is None:
            now = datetime.now(tz=UTC)

        # Stability-based demotion check
        if (
            layer.demotion_min_stability > 0
            and entry.stability > 0
            and entry.stability < layer.demotion_min_stability
        ):
            return layer.demotion_to

        eff_conf = calculate_decayed_confidence(entry, self._config, now=now)

        # Near floor: effective confidence <= floor * 1.5
        if eff_conf > layer.confidence_floor * 1.5:
            return None

        # No access within half-life period
        days_since_access = _days_since(entry.last_accessed, now)
        if days_since_access < layer.half_life_days:
            return None

        return layer.demotion_to

    @staticmethod
    def desirable_difficulty_bonus(
        entry: MemoryEntry,
        base_boost: float,
        config: DecayConfig,
        *,
        now: datetime | None = None,
    ) -> float:
        """Scale reinforcement boost by how decayed the memory is.

        Nearly-forgotten memories get bigger boosts (Roediger & Karpicke 2006).
        Returns the adjusted boost value.
        """
        decayed = calculate_decayed_confidence(entry, config, now=now)
        difficulty_bonus = 1.0 - decayed
        return base_boost * (1.0 + difficulty_bonus)

    @staticmethod
    def effective_half_life(base_half_life: int, reinforce_count: int) -> float:
        """Compute effective half-life with stability growth.

        Reinforced memories decay more slowly (Jost's First Law):
        ``base * (1.0 + log1p(reinforce_count) * 0.3)``

        A memory with reinforce_count=10 gets base x 1.72.
        """
        stability_growth = 1.0 + math.log1p(reinforce_count) * 0.3
        return base_half_life * stability_growth
