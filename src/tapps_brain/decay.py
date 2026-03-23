"""Time-based decay engine for memory confidence.

Recalculates confidence on read using exponential decay with
tier-specific half-lives. No background threads or timers -
decay is computed lazily when memories are accessed.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

import structlog
from pydantic import BaseModel, Field, field_validator

from tapps_brain.models import MemoryEntry, MemorySource, MemoryTier

logger = structlog.get_logger(__name__)

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
        default=30,
        ge=1,
        description="Epic 65.11: how-to workflows, steps (between pattern=60, context=14)",
    )
    context_half_life_days: int = Field(default=14, ge=1)

    # Confidence bounds
    confidence_floor: float = Field(default=0.1, ge=0.0, le=1.0)
    human_confidence_ceiling: float = Field(default=0.95, ge=0.0, le=1.0)
    agent_confidence_ceiling: float = Field(default=0.85, ge=0.0, le=1.0)
    inferred_confidence_ceiling: float = Field(default=0.70, ge=0.0, le=1.0)
    system_confidence_ceiling: float = Field(default=0.95, ge=0.0, le=1.0)

    # Profile-based layer half-lives (EPIC-010): maps tier name → half-life
    layer_half_lives: dict[str, int] = Field(default_factory=dict)
    # Profile-based per-layer confidence floors (EPIC-010)
    layer_confidence_floors: dict[str, float] = Field(default_factory=dict)
    # Profile-based source ceilings (EPIC-010)
    profile_source_ceilings: dict[str, float] = Field(default_factory=dict)

    # Decay model configuration (EPIC-010)
    decay_model: str = Field(default="exponential")
    decay_exponent: float = Field(default=1.0, ge=0.1, le=5.0)
    # Per-layer decay model overrides (EPIC-010)
    layer_decay_models: dict[str, str] = Field(default_factory=dict)
    layer_decay_exponents: dict[str, float] = Field(default_factory=dict)

    # Per-layer importance tags (EPIC-010)
    layer_importance_tags: dict[str, dict[str, float]] = Field(default_factory=dict)

    @field_validator("layer_half_lives")
    @classmethod
    def _validate_layer_half_lives(cls, v: dict[str, int]) -> dict[str, int]:
        """Ensure all layer half-life values are at least 1 day.

        Prevents ZeroDivisionError in the exponential/power-law decay formulas
        when a custom layer_half_lives entry is provided with a zero or negative value.
        """
        for name, days in v.items():
            if days < 1:
                raise ValueError(f"layer_half_lives[{name!r}] must be >= 1, got {days}")
        return v


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


def _get_half_life(tier: MemoryTier | str, config: DecayConfig) -> int:
    """Return the half-life in days for a given tier.

    Checks profile-based ``layer_half_lives`` first (EPIC-010), then
    falls back to the hardcoded ``MemoryTier`` → attribute mapping.
    """
    tier_str = tier.value if isinstance(tier, MemoryTier) else str(tier)

    # EPIC-010: profile-based lookup
    if tier_str in config.layer_half_lives:
        return config.layer_half_lives[tier_str]

    # Fallback: enum-based attribute lookup
    if isinstance(tier, MemoryTier):
        attr = _TIER_HALF_LIFE_ATTR[tier]
        return int(getattr(config, attr))

    # Try converting string to MemoryTier
    try:
        tier_enum = MemoryTier(tier_str)
        attr = _TIER_HALF_LIFE_ATTR[tier_enum]
        return int(getattr(config, attr))
    except ValueError:
        pass

    # Unknown tier: use the shortest default half-life (context=14)
    logger.warning(
        "unknown_tier_fallback",
        tier=tier_str,
        fallback_days=config.context_half_life_days,
    )
    return config.context_half_life_days


def _get_ceiling(source: MemorySource | str, config: DecayConfig) -> float:
    """Return the confidence ceiling for a given source type.

    Checks profile-based ``profile_source_ceilings`` first (EPIC-010),
    then falls back to the hardcoded attribute mapping.
    """
    source_str = source.value if isinstance(source, MemorySource) else str(source)

    # EPIC-010: profile-based ceilings
    if source_str in config.profile_source_ceilings:
        return config.profile_source_ceilings[source_str]

    # Fallback: enum-based attribute lookup
    if isinstance(source, MemorySource):
        attr = _SOURCE_CEILING_ATTR.get(source, "agent_confidence_ceiling")
        return float(getattr(config, attr))

    try:
        source_enum = MemorySource(source_str)
        attr = _SOURCE_CEILING_ATTR.get(source_enum, "agent_confidence_ceiling")
        return float(getattr(config, attr))
    except ValueError:
        logger.warning(
            "unknown_source_fallback",
            source=source_str,
            fallback_ceiling=config.agent_confidence_ceiling,
        )
        return config.agent_confidence_ceiling


def _get_confidence_floor(tier: MemoryTier | str, config: DecayConfig) -> float:
    """Return the confidence floor for a given tier.

    Checks profile-based ``layer_confidence_floors`` first (EPIC-010),
    then falls back to the global ``confidence_floor``.
    """
    tier_str = tier.value if isinstance(tier, MemoryTier) else str(tier)
    if tier_str in config.layer_confidence_floors:
        return config.layer_confidence_floors[tier_str]
    return config.confidence_floor


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

    Uses exponential decay by default: ``confidence * 0.5^(days / half_life)``.
    When ``decay_model="power_law"`` (EPIC-010), uses:
    ``confidence * (1 + days / (k * half_life))^(-beta)``.
    The result is clamped to ``[confidence_floor, source_ceiling]``.
    """
    half_life = _get_half_life(entry.tier, config)
    ceiling = _get_ceiling(entry.source, config)
    floor = _get_confidence_floor(entry.tier, config)

    ref_time = _decay_reference_time(entry)
    days = _days_since(ref_time, now)

    # Determine decay model for this tier (EPIC-010)
    tier_str = entry.tier.value if isinstance(entry.tier, MemoryTier) else str(entry.tier)

    # EPIC-010: Importance tags — boost effective half-life
    effective_hl = float(half_life)
    importance_tags = config.layer_importance_tags.get(tier_str, {})
    if importance_tags and entry.tags:
        max_multiplier = 1.0
        for tag in entry.tags:
            if tag in importance_tags:
                max_multiplier = max(max_multiplier, importance_tags[tag])
        effective_hl = half_life * max_multiplier

    decay_model = config.layer_decay_models.get(tier_str, config.decay_model)

    if decay_model == "power_law":
        # Power-law decay: C0 x (1 + t / (k x H))^(-beta)
        beta = config.layer_decay_exponents.get(tier_str, config.decay_exponent)
        k = 9.0  # Scaling constant (from FSRS)
        decay_factor = math.pow(1.0 + days / (k * effective_hl), -beta)
    else:
        # Exponential decay: confidence * 0.5^(days / half_life)
        decay_factor = math.pow(0.5, days / effective_hl)

    decayed = entry.confidence * decay_factor

    # Clamp to [floor, ceiling]
    return max(floor, min(ceiling, decayed))


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


# ---------------------------------------------------------------------------
# Profile-driven config factory (EPIC-010)
# ---------------------------------------------------------------------------


def decay_config_from_profile(profile: object) -> DecayConfig:
    """Build a ``DecayConfig`` from a ``MemoryProfile`` instance.

    Populates ``layer_half_lives``, ``layer_confidence_floors``,
    ``profile_source_ceilings``, and per-layer decay model overrides
    from the profile's layer definitions.

    The four legacy ``*_half_life_days`` fields are set from matching
    layer names (architectural/pattern/procedural/context) so that code
    using the old enum-based lookup still works.
    """
    from tapps_brain.profile import MemoryProfile

    if not isinstance(profile, MemoryProfile):
        return DecayConfig()

    layer_half_lives: dict[str, int] = {}
    layer_floors: dict[str, float] = {}
    layer_models: dict[str, str] = {}
    layer_exponents: dict[str, float] = {}
    layer_importance_tags: dict[str, dict[str, float]] = {}

    for layer in profile.layers:
        layer_half_lives[layer.name] = layer.half_life_days
        layer_floors[layer.name] = layer.confidence_floor
        if layer.decay_model != "exponential":
            layer_models[layer.name] = layer.decay_model
        if layer.decay_exponent != 1.0:
            layer_exponents[layer.name] = layer.decay_exponent
        if layer.importance_tags:
            layer_importance_tags[layer.name] = dict(layer.importance_tags)

    # Map legacy fields from matching layer names (values are always int)
    legacy: dict[str, int] = {}
    legacy_map = {
        "architectural": "architectural_half_life_days",
        "pattern": "pattern_half_life_days",
        "procedural": "procedural_half_life_days",
        "context": "context_half_life_days",
    }
    for name, attr in legacy_map.items():
        if name in layer_half_lives:
            legacy[attr] = layer_half_lives[name]

    # Source ceilings from profile
    ceilings = dict(profile.source_ceilings)

    # Global confidence floor (min of all layer floors, or profile default)
    global_floor = min(layer_floors.values()) if layer_floors else 0.1

    return DecayConfig(
        architectural_half_life_days=legacy.get("architectural_half_life_days", 180),
        pattern_half_life_days=legacy.get("pattern_half_life_days", 60),
        procedural_half_life_days=legacy.get("procedural_half_life_days", 30),
        context_half_life_days=legacy.get("context_half_life_days", 14),
        confidence_floor=global_floor,
        human_confidence_ceiling=ceilings.get("human", 0.95),
        agent_confidence_ceiling=ceilings.get("agent", 0.85),
        inferred_confidence_ceiling=ceilings.get("inferred", 0.70),
        system_confidence_ceiling=ceilings.get("system", 0.95),
        layer_half_lives=layer_half_lives,
        layer_confidence_floors=layer_floors,
        profile_source_ceilings=ceilings,
        layer_decay_models=layer_models,
        layer_decay_exponents=layer_exponents,
        layer_importance_tags=layer_importance_tags,
    )
