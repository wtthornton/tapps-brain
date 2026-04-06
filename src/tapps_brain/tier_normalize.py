"""Normalize memory tier strings from agents, relays, and profiles (GitHub #48).

Sub-agents and OpenClaw-style tools often emit tier names that do not exactly
match ``MemoryTier`` or a project's profile layer names. We map common aliases
and perform case-insensitive matching before falling back to ``pattern``.
"""

from __future__ import annotations

from tapps_brain.models import MemoryTier

# Common aliases from docs, OpenClaw memory-core, and personal-assistant profiles.
_TIER_ALIASES: dict[str, str] = {
    "long-term": MemoryTier.architectural.value,
    "long_term": MemoryTier.architectural.value,
    "short-term": MemoryTier.pattern.value,
    "short_term": MemoryTier.pattern.value,
    "identity": MemoryTier.architectural.value,
    "memo": MemoryTier.pattern.value,
    "note": MemoryTier.pattern.value,
    "notes": MemoryTier.pattern.value,
    "working": MemoryTier.context.value,
    "scratch": MemoryTier.context.value,
    "how-to": MemoryTier.procedural.value,
    "how_to": MemoryTier.procedural.value,
    "routine": MemoryTier.procedural.value,
    "workflow": MemoryTier.procedural.value,
}


def normalize_save_tier(raw: str | None, profile: object | None) -> str:
    """Return a tier string accepted by ``MemoryStore.save`` (enum or profile layer).

    Profile layer names are matched before global aliases so e.g. ``long-term`` on
    ``personal-assistant`` stays that layer instead of mapping to ``architectural``.

    Unknown values map to ``pattern`` so saves never fail solely on tier spelling.
    """
    if raw is None or str(raw).strip() == "":
        return MemoryTier.pattern.value

    t_lower = str(raw).strip().lower()

    if profile is not None:
        names = getattr(profile, "layer_names", None) or []
        for name in names:
            if isinstance(name, str) and name.lower() == t_lower:
                return name

    t = _TIER_ALIASES.get(t_lower, t_lower)

    try:
        return MemoryTier(t).value
    except ValueError:
        pass

    for mt in MemoryTier:
        if mt.value.lower() == t:
            return mt.value

    return MemoryTier.pattern.value
