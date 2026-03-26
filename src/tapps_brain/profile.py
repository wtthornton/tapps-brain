"""Configurable memory profiles — pluggable layers and scoring (EPIC-010).

Defines the ``MemoryProfile`` data model and loading/resolution functions.
Profiles are YAML files that configure layer definitions (tiers), decay
parameters, scoring weights, GC thresholds, and recall defaults.

The default ``repo-brain`` profile reproduces current hardcoded behavior
exactly, ensuring zero behavior change for existing users.
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from tapps_brain.feedback import FeedbackConfig

# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class PromotionThreshold(BaseModel):
    """Criteria for promoting a memory to a higher layer."""

    min_access_count: int = Field(default=5, ge=1)
    min_age_days: int = Field(default=7, ge=1)
    min_confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class LayerDefinition(BaseModel):
    """A single memory layer (tier) definition within a profile."""

    name: str
    description: str = ""
    half_life_days: int = Field(ge=1)
    decay_model: Literal["exponential", "power_law"] = "exponential"
    decay_exponent: float = Field(default=1.0, ge=0.1, le=5.0)
    confidence_floor: float = Field(default=0.1, ge=0.0, le=1.0)
    importance_tags: dict[str, float] = Field(default_factory=dict)
    promotion_to: str | None = None
    promotion_threshold: PromotionThreshold | None = None
    demotion_to: str | None = None
    adaptive_stability: bool = Field(
        default=False,
        description="Enable FSRS-style adaptive stability on reinforcement.",
    )


_DEFAULT_SOURCE_TRUST: dict[str, float] = {
    "human": 1.0,
    "system": 0.9,
    "agent": 0.7,
    "inferred": 0.5,
}


class ScoringConfig(BaseModel):
    """Composite scoring weight configuration."""

    relevance: float = Field(default=0.40, ge=0.0, le=1.0)
    confidence: float = Field(default=0.30, ge=0.0, le=1.0)
    recency: float = Field(default=0.15, ge=0.0, le=1.0)
    frequency: float = Field(default=0.15, ge=0.0, le=1.0)
    bm25_norm_k: float = Field(default=5.0, ge=0.1)
    frequency_cap: int = Field(default=20, ge=1)
    source_trust: dict[str, float] = Field(
        default_factory=lambda: dict(_DEFAULT_SOURCE_TRUST),
        description=(
            "Per-source trust multipliers applied to composite scores. "
            "Values > 1.0 boost, < 1.0 penalise. Default: human=1.0, "
            "system=0.9, agent=0.7, inferred=0.5."
        ),
    )

    _WEIGHT_SUM_LO: float = 0.95
    _WEIGHT_SUM_HI: float = 1.05

    @model_validator(mode="after")
    def _weights_sum_check(self) -> ScoringConfig:
        total = self.relevance + self.confidence + self.recency + self.frequency
        if not (self._WEIGHT_SUM_LO <= total <= self._WEIGHT_SUM_HI):
            msg = (
                f"Scoring weights must sum to ~1.0 (got {total:.3f}). "
                f"relevance={self.relevance}, confidence={self.confidence}, "
                f"recency={self.recency}, frequency={self.frequency}"
            )
            raise ValueError(msg)
        return self


class GCConfig(BaseModel):
    """Garbage collection configuration."""

    floor_retention_days: int = Field(default=30, ge=1)
    session_expiry_days: int = Field(default=7, ge=1)
    contradicted_threshold: float = Field(default=0.2, ge=0.0, le=1.0)
    stale_threshold: float = Field(default=0.3, ge=0.0, le=1.0)


class RecallProfileConfig(BaseModel):
    """Recall / injection defaults."""

    default_token_budget: int = Field(default=3000, ge=100)
    default_engagement: Literal["low", "medium", "high"] = "high"
    min_score: float = Field(default=0.3, ge=0.0, le=1.0)
    min_confidence: float = Field(default=0.1, ge=0.0, le=1.0)


class LimitsConfig(BaseModel):
    """Store limits configuration."""

    max_entries: int = Field(default=5000, ge=1)
    max_key_length: int = Field(default=128, ge=1)
    max_value_length: int = Field(default=4096, ge=1)
    max_tags: int = Field(default=10, ge=1)


class DiagnosticsProfileConfig(BaseModel):
    """Diagnostics / quality scorecard settings (EPIC-030)."""

    retention_days: int = Field(default=90, ge=1, le=3650)
    custom_dimension_paths: list[str] = Field(
        default_factory=list,
        description="Dotted paths to HealthDimension factories or classes.",
    )
    dimension_weights: dict[str, float] = Field(
        default_factory=dict,
        description="Optional per-dimension weight overrides.",
    )


class HiveConfig(BaseModel):
    """Hive propagation configuration (EPIC-011).

    Controls how memories flow between the local store and the Hive.
    When absent or all defaults, Hive is effectively disabled.
    """

    auto_propagate_tiers: list[str] = Field(
        default_factory=list,
        description="Tiers that auto-propagate to the Hive (e.g. ['architectural']).",
    )
    private_tiers: list[str] = Field(
        default_factory=list,
        description="Tiers that never propagate (e.g. ['context']).",
    )
    conflict_policy: str = Field(
        default="supersede",
        description=(
            "Conflict resolution: 'supersede' | 'source_authority'"
            " | 'confidence_max' | 'last_write_wins'."
        ),
    )
    recall_weight: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="Weight multiplier for Hive results in recall (0.0-1.0).",
    )


# ---------------------------------------------------------------------------
# Main profile model
# ---------------------------------------------------------------------------

_DEFAULT_SOURCE_CONFIDENCE: dict[str, float] = {
    "human": 0.95,
    "agent": 0.60,
    "inferred": 0.40,
    "system": 0.90,
}

_DEFAULT_SOURCE_CEILINGS: dict[str, float] = {
    "human": 0.95,
    "agent": 0.85,
    "inferred": 0.70,
    "system": 0.95,
}


class MemoryProfile(BaseModel):
    """A complete memory profile configuration.

    Loaded from YAML at ``MemoryStore`` init time. The default
    ``repo-brain`` profile reproduces current hardcoded behavior exactly.
    """

    name: str
    version: str = "1.0"
    extends: str | None = None
    description: str = ""
    layers: list[LayerDefinition]
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    source_confidence: dict[str, float] = Field(
        default_factory=lambda: dict(_DEFAULT_SOURCE_CONFIDENCE)
    )
    source_ceilings: dict[str, float] = Field(
        default_factory=lambda: dict(_DEFAULT_SOURCE_CEILINGS)
    )
    gc: GCConfig = Field(default_factory=GCConfig)
    recall: RecallProfileConfig = Field(default_factory=RecallProfileConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    hive: HiveConfig = Field(default_factory=HiveConfig)
    feedback: FeedbackConfig = Field(
        default_factory=FeedbackConfig,
        description=(
            "Feedback collection configuration.  Allows registering custom event "
            "types and enabling strict event-type validation."
        ),
    )
    diagnostics: DiagnosticsProfileConfig = Field(
        default_factory=DiagnosticsProfileConfig,
        description="Diagnostics history retention and custom dimension paths.",
    )

    @model_validator(mode="after")
    def _validate_layers(self) -> MemoryProfile:
        names = [layer.name for layer in self.layers]
        if len(names) != len(set(names)):
            seen: set[str] = set()
            dupes: list[str] = []
            for n in names:
                if n in seen:
                    dupes.append(n)
                else:
                    seen.add(n)
            msg = f"Layer names must be unique. Duplicates: {dupes}"
            raise ValueError(msg)

        # Validate promotion/demotion targets reference existing layers
        name_set = set(names)
        for layer in self.layers:
            if layer.promotion_to and layer.promotion_to not in name_set:
                msg = (
                    f"Layer '{layer.name}' has promotion_to='{layer.promotion_to}' "
                    f"which is not a defined layer."
                )
                raise ValueError(msg)
            if layer.demotion_to and layer.demotion_to not in name_set:
                msg = (
                    f"Layer '{layer.name}' has demotion_to='{layer.demotion_to}' "
                    f"which is not a defined layer."
                )
                raise ValueError(msg)
        return self

    def get_layer(self, name: str) -> LayerDefinition | None:
        """Return the layer definition for *name*, or ``None``."""
        for layer in self.layers:
            if layer.name == name:
                return layer
        return None

    def get_layer_or_default(self, name: str) -> LayerDefinition:
        """Return the layer for *name*, falling back to the shortest half-life layer."""
        layer = self.get_layer(name)
        if layer is not None:
            return layer
        # Fallback: shortest half-life layer
        return min(self.layers, key=lambda la: la.half_life_days)

    @property
    def layer_names(self) -> list[str]:
        """Return the ordered list of layer names."""
        return [layer.name for layer in self.layers]


# ---------------------------------------------------------------------------
# Profile loading
# ---------------------------------------------------------------------------

_MAX_INHERITANCE_DEPTH = 3


def load_profile(path: Path) -> MemoryProfile:
    """Load and validate a profile from a YAML file.

    The YAML is expected to have a top-level ``profile`` key.

    Raises:
        FileNotFoundError: If *path* does not exist.
        ValueError: If the YAML is invalid or fails validation.
    """
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        msg = f"Profile YAML must be a mapping, got {type(data).__name__}"
        raise ValueError(msg)

    profile_data = data.get("profile", data)
    return MemoryProfile.model_validate(profile_data)


def _builtin_profiles_dir() -> Path:
    """Return the path to the built-in profiles directory."""
    ref = importlib.resources.files("tapps_brain") / "profiles"
    # files() returns a Traversable; for our purposes it's always a Path
    return Path(str(ref))


def get_builtin_profile(name: str) -> MemoryProfile:
    """Load a built-in profile by name.

    Raises:
        FileNotFoundError: If no built-in profile with that name exists.
        ValueError: If *name* contains path-traversal characters.
    """
    # Reject path-traversal attempts before constructing the path.
    if "/" in name or "\\" in name or name.startswith("."):
        msg = f"Invalid profile name '{name}': must not contain path separators or start with '.'"
        raise ValueError(msg)
    profiles_dir = _builtin_profiles_dir()
    path = profiles_dir / f"{name}.yaml"
    if not path.exists():
        available = list_builtin_profiles()
        msg = f"No built-in profile '{name}'. Available: {available}"
        raise FileNotFoundError(msg)
    return load_profile(path)


def list_builtin_profiles() -> list[str]:
    """Return the names of all available built-in profiles."""
    profiles_dir = _builtin_profiles_dir()
    if not profiles_dir.exists():
        return []
    return sorted(p.stem for p in profiles_dir.glob("*.yaml"))


def _merge_profiles(child: MemoryProfile, parent: MemoryProfile) -> MemoryProfile:
    """Merge a child profile onto a parent profile.

    Child layers with matching names replace parent layers; new child
    layers are appended. Scalar configs (scoring, gc, etc.) from the
    child override the parent.
    """
    # Start with parent layers, override with child layers by name
    parent_layers = {la.name: la for la in parent.layers}
    for child_layer in child.layers:
        parent_layers[child_layer.name] = child_layer
    merged_layers = list(parent_layers.values())

    # Child scalars override parent (Pydantic defaults are used if child didn't set them)
    return MemoryProfile(
        name=child.name,
        version=child.version,
        extends=None,  # Resolved — no further inheritance
        description=child.description or parent.description,
        layers=merged_layers,
        scoring=child.scoring,
        source_confidence={**parent.source_confidence, **child.source_confidence},
        source_ceilings={**parent.source_ceilings, **child.source_ceilings},
        gc=child.gc,
        recall=child.recall,
        limits=child.limits,
        hive=child.hive,
        feedback=child.feedback,
        diagnostics=child.diagnostics,
    )


def _resolve_inheritance(profile: MemoryProfile, depth: int = 0) -> MemoryProfile:
    """Resolve profile inheritance up to ``_MAX_INHERITANCE_DEPTH``."""
    if profile.extends is None:
        return profile
    if depth >= _MAX_INHERITANCE_DEPTH:
        msg = (
            f"Profile inheritance depth exceeds maximum ({_MAX_INHERITANCE_DEPTH}). "
            f"Chain: {profile.name} extends {profile.extends}"
        )
        raise ValueError(msg)

    parent = get_builtin_profile(profile.extends)
    parent = _resolve_inheritance(parent, depth + 1)
    return _merge_profiles(profile, parent)


def resolve_profile(
    project_dir: Path,
    profile_name: str | None = None,
) -> MemoryProfile:
    """Resolve the active profile using the resolution order.

    Resolution order:
    1. ``{project_dir}/.tapps-brain/profile.yaml`` (project-specific)
    2. ``~/.tapps-brain/profile.yaml`` (user-global)
    3. Built-in profile by *profile_name* (default: ``repo-brain``)

    Inheritance via ``extends`` is resolved after loading.
    """
    # 1. Project-specific profile
    project_profile = project_dir / ".tapps-brain" / "profile.yaml"
    if project_profile.exists():
        profile = load_profile(project_profile)
        return _resolve_inheritance(profile)

    # 2. User-global profile
    user_profile = Path.home() / ".tapps-brain" / "profile.yaml"
    if user_profile.exists():
        profile = load_profile(user_profile)
        return _resolve_inheritance(profile)

    # 3. Built-in profile
    name = profile_name or "repo-brain"
    profile = get_builtin_profile(name)
    return _resolve_inheritance(profile)
