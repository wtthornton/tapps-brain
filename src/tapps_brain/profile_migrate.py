"""Safe tier remapping when profiles or naming change (GitHub #20).

Parses ``from:to`` maps, validates targets against ``MemoryTier`` and the
active profile's layer names, and applies updates with JSONL audit records.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from tapps_brain.models import MemoryTier

if TYPE_CHECKING:
    from tapps_brain.profile import MemoryProfile


class TierMigrationChange(BaseModel):
    """A single entry that would be or was updated."""

    key: str
    from_tier: str
    to_tier: str


class TierMigrationResult(BaseModel):
    """Summary of a tier migration run."""

    dry_run: bool = False
    would_update: int = Field(default=0, ge=0)
    updated: int = Field(default=0, ge=0)
    skipped_no_match: int = Field(default=0, ge=0)
    skipped_identity: int = Field(default=0, ge=0)
    changes: list[TierMigrationChange] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


def parse_tier_map_pairs(pairs: list[str]) -> dict[str, str]:
    """Parse ``['old:new', ...]`` into a dict (later pairs override earlier)."""
    out: dict[str, str] = {}
    for raw in pairs:
        part = raw.strip()
        if not part:
            continue
        if ":" not in part:
            msg = f"Invalid --map {raw!r}: expected from:to"
            raise ValueError(msg)
        left, right = part.split(":", 1)
        from_tier, to_tier = left.strip(), right.strip()
        if not from_tier or not to_tier:
            msg = f"Invalid --map {raw!r}: empty from or to tier"
            raise ValueError(msg)
        out[from_tier] = to_tier
    return out


def parse_tier_map_json(raw: str) -> dict[str, str]:
    """Parse JSON object mapping source tier string to target tier string."""
    data = json.loads(raw)
    if not isinstance(data, dict):
        msg = "tier_map_json must be a JSON object"
        raise ValueError(msg)
    out: dict[str, str] = {}
    for k, v in data.items():
        if not isinstance(k, str) or not isinstance(v, str):
            msg = "tier_map_json keys and values must be strings"
            raise ValueError(msg)
        ks, vs = k.strip(), v.strip()
        if not ks or not vs:
            msg = f"Invalid empty tier in tier_map_json: {k!r} -> {v!r}"
            raise ValueError(msg)
        out[ks] = vs
    return out


def coerce_tier_value(name: str, profile: MemoryProfile | None) -> MemoryTier | str:
    """Resolve a tier or profile layer name to a value accepted by ``MemoryEntry``."""
    try:
        return MemoryTier(name)
    except ValueError:
        if profile is not None and name in profile.layer_names:
            return name
    msg = f"Unknown tier or profile layer: {name!r}"
    raise ValueError(msg)


def validate_tier_map(
    tier_map: dict[str, str],
    profile: MemoryProfile | None,
) -> list[str]:
    """Return validation errors; empty list means the map is usable.

    Only *target* tiers are validated against ``MemoryTier`` and profile layers.
    Source keys are arbitrary strings matched against stored entry tiers.
    """
    errors: list[str] = []
    if not tier_map:
        errors.append("tier map is empty")
        return errors
    for src, dst in tier_map.items():
        if not str(src).strip() or not str(dst).strip():
            errors.append(f"empty tier in mapping {src!r} -> {dst!r}")
            continue
        try:
            coerce_tier_value(dst, profile)
        except ValueError as exc:
            errors.append(f"target for {src!r} ({dst!r}): {exc}")
    return errors
