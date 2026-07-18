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
    Layer matching is case- and separator-insensitive (``long_term`` matches a
    ``long-term`` layer) so a save never fails on spelling of a real layer name.

    Without a profile, unknown values map to ``pattern`` so saves never fail on
    tier spelling. When a profile defines custom layer names, the service layer
    validates the result against those names and rejects tiers outside them
    (pinned contract — see ``TestProfileAwareTierValidation``).
    """
    if raw is None or str(raw).strip() == "":
        return MemoryTier.pattern.value

    t_lower = str(raw).strip().lower()

    if profile is not None:
        names = getattr(profile, "layer_names", None) or []
        # Separator-insensitive: "long_term" must match a "long-term" layer,
        # otherwise the alias table maps it to "architectural" and the save
        # is rejected purely on underscore-vs-hyphen spelling.
        t_sep = t_lower.replace("_", "-")
        for name in names:
            if isinstance(name, str) and name.lower().replace("_", "-") == t_sep:
                return name

    t = _TIER_ALIASES.get(t_lower, t_lower)

    try:
        return MemoryTier(t).value
    except ValueError:
        # *t* is already lowercase and every MemoryTier value is lowercase,
        # so a failed enum lookup means the tier is genuinely unknown.
        return MemoryTier.pattern.value
