"""Time-based decay engine for memory confidence.

Recalculates confidence on read using exponential decay with
tier-specific half-lives. No background threads or timers —
decay is computed lazily when memories are accessed.

**FSRS-lite:** Optional ``update_stability()`` adjusts per-entry ``stability``
(days) when profile ``adaptive_stability`` is enabled on ``record_access`` and
``reinforce``. Product stance (full FSRS vs tier-only vs hybrid) is documented
in ``docs/guides/memory-decay-and-fsrs.md`` (EPIC-042.8).
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

# TAP-735: Half-life multipliers for per-entry temporal sensitivity.
# "high"   -> x0.25 -- fact changes quickly; decays 4x faster than the tier baseline.
# "medium" -> x1.0  -- no change (explicit or absent).
# "low"    -> x4.0  -- fact is stable; decays 4x slower than the tier baseline.
# None     -> x1.0  -- backward-compatible default; no behaviour change for existing entries.
_TEMPORAL_SENSITIVITY_MULTIPLIERS: dict[str | None, float] = {
    "high": 0.25,
    "medium": 1.0,
    "low": 4.0,
    None: 1.0,
}

# Seconds per day for time calculations.
_SECONDS_PER_DAY = 86400.0

# FSRS-canonical scaling constant for the power-law forgetting curve.
# FSRS expresses retrievability as R = (1 + F·t/S)^C with F = 19/81; here
# we use the equivalent form C0 * (1 + t / (k*H))^(-beta) where k = 1/F = 81/19.
# Source: https://github.com/open-spaced-repetition/fsrs4anki/wiki/The-Algorithm
_FSRS_DEFAULT_K = 81.0 / 19.0  # ≈ 4.2632

# Calibration anchor for power-law that preserves R(t=H) = 0.5 (the half-life
# semantic). Solving (1 + 1/k)^(-β) = 0.5 → β = ln 2 / ln(1 + 1/k).
# With k = 81/19 this gives β ≈ 3.292. Use this when migrating an existing
# exponential-tier profile to power-law without shifting median retention.
_FSRS_HALF_LIFE_ANCHOR_BETA = math.log(2.0) / math.log(1.0 + 1.0 / _FSRS_DEFAULT_K)

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
    ephemeral_half_life_days: int = Field(
        default=1,
        ge=1,
        description=(
            "Momentary context / current conversation state (personal-assistant profile: 1 day)."
        ),
    )
    session_half_life_days: int = Field(
        default=1,
        ge=1,
        description="Ephemeral, current session only. Same default as ephemeral.",
    )

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

    # Decay model configuration (EPIC-010, STORY-SC02)
    decay_model: str = Field(default="exponential")
    decay_exponent: float = Field(default=1.0, ge=0.1, le=10.0)
    decay_k: float = Field(
        default=_FSRS_DEFAULT_K,
        gt=0.0,
        description=(
            "Power-law scaling constant. Default = 81/19 ≈ 4.263 (FSRS canonical, "
            "from https://github.com/open-spaced-repetition/fsrs4anki/wiki/The-Algorithm). "
            "Only used when decay_model == 'power_law'."
        ),
    )
    # Per-layer decay model overrides (EPIC-010 + STORY-SC02)
    layer_decay_models: dict[str, str] = Field(default_factory=dict)
    layer_decay_exponents: dict[str, float] = Field(default_factory=dict)
    layer_decay_k: dict[str, float] = Field(default_factory=dict)

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
    MemoryTier.ephemeral: "ephemeral_half_life_days",
    MemoryTier.session: "session_half_life_days",
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


def power_law_decay(
    days: float,
    half_life: float,
    decay_exponent: float,
    k: float = _FSRS_DEFAULT_K,
) -> float:
    """Return the power-law decay multiplier ``(1 + days / (k·half_life))^(-decay_exponent)``.

    The Ebbinghaus / Wixted power-law shape; matches the FSRS family of
    spaced-repetition curves with ``k = 81/19`` (FSRS-4 canonical).

    Args:
        days: Elapsed time since the reference timestamp, in days. Negative
            values are clamped to ``0.0``.
        half_life: Layer half-life in days. When ``decay_exponent`` is the
            half-life-anchor value (``ln 2 / ln(1 + 1/k)``), the curve passes
            through ``R(half_life) = 0.5`` — preserving the exponential-mode
            half-life semantic. With ``decay_exponent < that value`` the tail
            is fatter; with ``> that value``, the tail is thinner.
        decay_exponent: ``β`` in the formula above. ``0.5`` matches the FSRS
            ``C = -0.5`` canonical default (treats ``half_life`` as FSRS
            "stability", i.e. the 90 % retrievability point).
        k: FSRS scaling constant. Default ``81/19 ≈ 4.263``.

    Returns:
        Decay multiplier in ``(0, 1]``. ``1.0`` at ``days == 0``.
    """
    if days <= 0.0:
        return 1.0
    return math.pow(1.0 + days / (k * half_life), -decay_exponent)


def exponential_decay(days: float, half_life: float) -> float:
    """Return the exponential decay multiplier ``0.5^(days / half_life)``.

    The simpler-but-empirically-poorer-fit decay model. Retained as the
    default for backward compatibility on any profile that hasn't opted into
    ``decay_model: power_law``.
    """
    if days <= 0.0:
        return 1.0
    return math.pow(0.5, days / half_life)


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

    msg = f"Unknown tier {tier_str!r} — not in profile layers or MemoryTier enum"
    raise ValueError(msg)


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
    except ValueError:
        msg = f"Unknown source {source_str!r} — not in profile ceilings or MemorySource enum"
        raise ValueError(msg) from None
    attr = _SOURCE_CEILING_ATTR.get(source_enum, "agent_confidence_ceiling")
    return float(getattr(config, attr))


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
    """Return fractional days elapsed since an ISO-8601 timestamp.

    If the timestamp is malformed or unparseable, emits a structured warning
    and returns ``float("inf")`` so the entry decays to its confidence floor
    rather than being silently treated as freshly updated (TAP-725).
    """
    if now is None:
        now = datetime.now(tz=UTC)
    try:
        ts = datetime.fromisoformat(iso_timestamp)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        logger.warning(
            "decay.invalid_iso_timestamp",
            timestamp=str(iso_timestamp)[:64],
            action="treating_as_maximally_stale",
        )
        return float("inf")
    delta = now - ts
    return max(delta.total_seconds() / _SECONDS_PER_DAY, 0.0)


def _decay_reference_time(entry: MemoryEntry) -> str:
    """Return the timestamp from which decay is measured.

    Uses ``last_reinforced`` if set, otherwise ``updated_at``.
    """
    if entry.last_reinforced:
        return entry.last_reinforced
    return entry.updated_at


# ---------------------------------------------------------------------------
# Difficulty defaults per tier name (GitHub #28, task 040.5)
# ---------------------------------------------------------------------------

_TIER_DIFFICULTY_DEFAULT: dict[str, float] = {
    "identity": 1.0,
    "long-term": 3.0,
    "short-term": 6.0,
    "ephemeral": 9.0,
    # Legacy tier names
    "architectural": 1.0,
    "pattern": 3.0,
    "procedural": 4.0,
    "context": 6.0,
    "session": 9.0,
}

_STABILITY_MIN = 0.1
_STABILITY_MAX = 3650.0


def update_stability(
    entry: MemoryEntry,
    config: DecayConfig,
    was_useful: bool,
    *,
    now: datetime | None = None,
) -> tuple[float, float]:
    """Compute updated (stability, difficulty) using FSRS-style rules.

    Args:
        entry: The memory entry being accessed.
        config: Decay configuration (used for tier half-life lookup).
        was_useful: Whether this access was a useful recall.
        now: Optional current time (for testing).

    Returns:
        (new_stability_days, new_difficulty)
    """
    tier_str = entry.tier.value if isinstance(entry.tier, MemoryTier) else str(entry.tier)

    # Initialize stability from tier half-life if unset
    s_old = entry.stability
    if s_old == 0.0:
        s_old = float(_get_half_life(entry.tier, config))

    # Initialize difficulty from tier if unset
    d_old = entry.difficulty
    if d_old == 0.0:
        d_old = _TIER_DIFFICULTY_DEFAULT.get(tier_str, 5.0)

    # Compute current retrievability R = 0.5^(days / stability)
    ref_time = _decay_reference_time(entry)
    days = _days_since(ref_time, now)
    r = math.pow(0.5, days / max(s_old, 0.1))

    if was_useful:
        # FSRS-inspired stability increase on successful recall
        # S_new = S_old * (1 + 0.4 * exp(-D/3) * S_old^(-0.2) * (exp(0.5*(1-R)) - 1))
        factor = 1.0 + 0.4 * math.exp(-d_old / 3.0) * math.pow(s_old, -0.2) * (
            math.exp(0.5 * (1.0 - r)) - 1.0
        )
        s_new = s_old * max(factor, 1.0)  # stability should only grow on useful access
    else:
        # Simple shrink on non-useful access
        s_new = s_old * 0.8

    # Clamp to valid range
    s_new = max(_STABILITY_MIN, min(_STABILITY_MAX, s_new))

    return (s_new, d_old)


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
    If ``entry.stability > 0``, uses stability as effective half-life (GitHub #28).
    """
    half_life = _get_half_life(entry.tier, config)
    ceiling = _get_ceiling(entry.source, config)
    floor = _get_confidence_floor(entry.tier, config)

    ref_time = _decay_reference_time(entry)
    days = _days_since(ref_time, now)

    # Determine decay model for this tier (EPIC-010)
    tier_str = entry.tier.value if isinstance(entry.tier, MemoryTier) else str(entry.tier)

    # GitHub #28: if entry has a non-zero stability, use it as effective half-life
    effective_hl = entry.stability if entry.stability > 0.0 else float(half_life)

    # TAP-735: temporal_sensitivity multiplies the effective half-life before all
    # other adjustments.  Applied here so that importance-tag boosts still stack on
    # top of the velocity hint (consistent ordering: velocity x importance x model).
    effective_hl *= _TEMPORAL_SENSITIVITY_MULTIPLIERS.get(entry.temporal_sensitivity, 1.0)

    # EPIC-010: Importance tags — boost effective half-life
    importance_tags = config.layer_importance_tags.get(tier_str, {})
    if importance_tags and entry.tags:
        max_multiplier = 1.0
        for tag in entry.tags:
            if tag in importance_tags:
                max_multiplier = max(max_multiplier, importance_tags[tag])
        effective_hl = effective_hl * max_multiplier

    decay_model = config.layer_decay_models.get(tier_str, config.decay_model)

    if decay_model == "power_law":
        beta = config.layer_decay_exponents.get(tier_str, config.decay_exponent)
        k = config.layer_decay_k.get(tier_str, config.decay_k)
        decay_factor = power_law_decay(days, effective_hl, beta, k)
    else:
        decay_factor = exponential_decay(days, effective_hl)

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
    """
    from tapps_brain.profile import MemoryProfile

    if not isinstance(profile, MemoryProfile):
        return DecayConfig()

    layer_half_lives: dict[str, int] = {}
    layer_floors: dict[str, float] = {}
    layer_models: dict[str, str] = {}
    layer_exponents: dict[str, float] = {}
    layer_decay_k: dict[str, float] = {}
    layer_importance_tags: dict[str, dict[str, float]] = {}

    for layer in profile.layers:
        layer_half_lives[layer.name] = layer.half_life_days
        layer_floors[layer.name] = layer.confidence_floor
        if layer.decay_model != "exponential":
            layer_models[layer.name] = layer.decay_model
        if layer.decay_exponent != 1.0:
            layer_exponents[layer.name] = layer.decay_exponent
        if layer.decay_k is not None:
            layer_decay_k[layer.name] = layer.decay_k
        if layer.importance_tags:
            layer_importance_tags[layer.name] = dict(layer.importance_tags)

    # Source ceilings from profile
    ceilings = dict(profile.source_ceilings)

    # Global confidence floor (min of all layer floors, or profile default)
    global_floor = min(layer_floors.values()) if layer_floors else 0.1

    return DecayConfig(
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
        layer_decay_k=layer_decay_k,
        layer_importance_tags=layer_importance_tags,
    )
