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
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tapps_brain.feedback import FeedbackConfig
from tapps_brain.lexical import LexicalRetrievalConfig

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
        description=(
            "Enable FSRS-style adaptive stability updates on "
            "``MemoryStore.record_access`` and ``MemoryStore.reinforce``."
        ),
    )
    promotion_strategy: str = Field(
        default="threshold",
        description="Promotion strategy: 'threshold' (default) or 'stability'.",
    )
    promotion_stability_threshold: float = Field(
        default=10.0,
        description="Stability score threshold for stability-based promotion.",
    )
    demotion_min_stability: float = Field(
        default=0.0,
        description="Minimum stability to stay in this tier. 0 = no demotion by stability.",
    )


_DEFAULT_SOURCE_TRUST: dict[str, float] = {
    "human": 1.0,
    "system": 0.9,
    "agent": 0.7,
    "inferred": 0.5,
}

# EPIC-042.5 / STORY-042.5: primary blend weights (and optional graph/provenance)
# when those are non-zero) must fall in this band so composite scores stay interpretable.
SCORING_WEIGHT_SUM_MIN: float = 0.95
SCORING_WEIGHT_SUM_MAX: float = 1.05


def composite_scoring_weight_total(
    relevance: float,
    confidence: float,
    recency: float,
    frequency: float,
    *,
    graph_centrality: float = 0.0,
    provenance_trust: float = 0.0,
) -> float:
    """Sum of composite ranking weights (same terms as ``ScoringConfig`` validation)."""
    return relevance + confidence + recency + frequency + graph_centrality + provenance_trust


class ScoringConfig(BaseModel):
    """Composite scoring weight configuration (EPIC-042.5).

    Tune under ``profile.scoring`` in YAML. The four primary signals
    (``relevance``, ``confidence``, ``recency``, ``frequency``) must sum to
    ~1.0 together with any non-zero ``graph_centrality`` or ``provenance_trust``.
    ``source_trust`` entries are **multipliers** applied after the linear blend,
    not part of that sum.

    Defaults match historical product behavior (40/30/15/15).
    """

    relevance: float = Field(default=0.40, ge=0.0, le=1.0)
    confidence: float = Field(default=0.30, ge=0.0, le=1.0)
    recency: float = Field(default=0.15, ge=0.0, le=1.0)
    frequency: float = Field(default=0.15, ge=0.0, le=1.0)
    graph_centrality: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Weight for graph centrality signal (placeholder; populated when "
            "relationship graph #33 is implemented). Defaults to 0.0 (disabled)."
        ),
    )
    provenance_trust: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Weight for provenance trust signal (source_trust * channel_trust). "
            "Defaults to 0.0 (disabled)."
        ),
    )
    frequency_cap: int = Field(default=20, ge=1)
    source_trust: dict[str, float] = Field(
        default_factory=lambda: dict(_DEFAULT_SOURCE_TRUST),
        description=(
            "Per-source trust multipliers applied to composite scores. "
            "Values > 1.0 boost, < 1.0 penalise. Default: human=1.0, "
            "system=0.9, agent=0.7, inferred=0.5."
        ),
    )

    @model_validator(mode="after")
    def _weights_sum_check(self) -> ScoringConfig:
        using_new_signals = self.graph_centrality > 0.0 or self.provenance_trust > 0.0
        if using_new_signals:
            # All 6 weights must sum to ~1.0
            total = composite_scoring_weight_total(
                self.relevance,
                self.confidence,
                self.recency,
                self.frequency,
                graph_centrality=self.graph_centrality,
                provenance_trust=self.provenance_trust,
            )
            if not (SCORING_WEIGHT_SUM_MIN <= total <= SCORING_WEIGHT_SUM_MAX):
                msg = (
                    f"Scoring weights must sum to ~1.0 (got {total:.3f}). "
                    f"relevance={self.relevance}, confidence={self.confidence}, "
                    f"recency={self.recency}, frequency={self.frequency}, "
                    f"graph_centrality={self.graph_centrality}, "
                    f"provenance_trust={self.provenance_trust}"
                )
                raise ValueError(msg)
        else:
            # Original 4 weights must sum to ~1.0
            total = composite_scoring_weight_total(
                self.relevance, self.confidence, self.recency, self.frequency
            )
            if not (SCORING_WEIGHT_SUM_MIN <= total <= SCORING_WEIGHT_SUM_MAX):
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


class HybridFusionConfig(BaseModel):
    """Hybrid BM25 + dense recall pool sizes and RRF *k* (EPIC-042 / STORY-042.4).

    Configure under ``profile.hybrid_fusion`` in YAML. ``MemoryRetriever`` reads these
    when passed as ``hybrid_config`` (e.g. from ``inject_memories`` via the active
    store profile). Defaults match historical hardcoded behavior (20/20/60).
    """

    model_config = ConfigDict(extra="forbid")

    adaptive_fusion: bool = Field(
        default=True,
        description=(
            "When True, query-aware BM25 vs vector weights (#40). "
            "When False, equal 1:1 RRF weights."
        ),
    )
    top_k_lexical: int = Field(
        default=20,
        ge=1,
        le=500,
        description="Max BM25 candidates fed into RRF.",
    )
    top_k_dense: int = Field(
        default=20,
        ge=1,
        le=500,
        description="Max vector/KNN candidates fed into RRF.",
    )
    rrf_k: int = Field(
        default=60,
        ge=1,
        le=500,
        description="RRF denominator offset k in 1/(k+rank); default 60 (see fusion.py).",
    )


class ConflictCheckConfig(BaseModel):
    """Save-time semantic conflict detection (EPIC-044.3 / GitHub #44).

    Tune under ``profile.conflict_check`` in YAML. ``aggressiveness`` selects a
    default Jaccard-style similarity cutoff for ``detect_save_conflicts``; set
    ``similarity_threshold`` explicitly to override the tier. For offline review
    of pairs at that cutoff, use CLI ``maintenance save-conflict-candidates``
    (see ``docs/guides/save-conflict-nli-offline.md``).
    """

    model_config = ConfigDict(extra="forbid")

    aggressiveness: Literal["low", "medium", "high"] = Field(
        default="medium",
        description=(
            "low: fewer flags (threshold 0.75). medium: historical default (0.6). "
            "high: more flags (0.45)."
        ),
    )
    similarity_threshold: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="When set, overrides aggressiveness-derived threshold.",
    )

    def effective_similarity_threshold(self) -> float:
        """Similarity cutoff passed to ``detect_save_conflicts``."""
        if self.similarity_threshold is not None:
            return float(self.similarity_threshold)
        tier_defaults: dict[str, float] = {"low": 0.75, "medium": 0.6, "high": 0.45}
        return tier_defaults[self.aggressiveness]


class SafetyConfig(BaseModel):
    """Write-time RAG / injection pattern ruleset (EPIC-044 STORY-044.1).

    Tune under ``profile.safety`` in YAML. The bundled pattern list is selected
    by ``ruleset_version``; only versions shipped with tapps-brain are supported.
    """

    model_config = ConfigDict(extra="forbid")

    ruleset_version: str | None = Field(
        default=None,
        description=(
            "Semver of the pattern ruleset (e.g. '1.0.0'). None = library default. "
            "Unknown values log a warning and fall back to the default."
        ),
    )


class LimitsConfig(BaseModel):
    """Store limits configuration."""

    max_entries: int = Field(default=5000, ge=1)
    max_entries_per_group: int | None = Field(
        default=None,
        description=(
            "When set, each memory_group bucket (including ungrouped rows) may hold at most "
            "this many keys; lowest-confidence eviction runs inside that bucket. None disables "
            "per-group caps (EPIC-044 STORY-044.7)."
        ),
    )
    max_key_length: int = Field(default=128, ge=1)
    max_value_length: int = Field(default=4096, ge=1)
    max_tags: int = Field(default=10, ge=1)

    @field_validator("max_entries_per_group")
    @classmethod
    def _validate_max_entries_per_group(cls, v: int | None) -> int | None:
        if v is not None and v < 1:
            msg = "max_entries_per_group must be >= 1 when set"
            raise ValueError(msg)
        return v


class SeedingConfig(BaseModel):
    """Auto-seed metadata (EPIC-044 STORY-044.6)."""

    model_config = ConfigDict(extra="forbid")

    seed_version: str | None = Field(
        default=None,
        description=(
            "Opaque label for the current profile-driven seed recipe. "
            "Included in ``seed_from_profile`` / ``reseed_from_profile`` summaries "
            "so operators can diff runs when bumping this value."
        ),
    )


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

    Controls tier routing, conflict resolution, and Hive recall weight when a
    ``HiveStore`` is attached (see CLI/MCP defaults vs
    ``MemoryStore(..., hive_store=...)`` in the Hive guide). This block does not
    attach or detach Hive; it only applies when ``hive_store`` is non-``None``.
    """

    auto_propagate_tiers: list[str] = Field(
        default_factory=lambda: ["architectural", "pattern"],
        description="Tiers that auto-propagate to the Hive (e.g. ['architectural']).",
    )
    private_tiers: list[str] = Field(
        default_factory=lambda: ["context"],
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
    groups: list[str] = Field(
        default_factory=list,
        description="Declarative group memberships for the agent (EPIC-056).",
    )
    expert_domains: list[str] = Field(
        default_factory=list,
        description="Expert domains for auto-publishing (EPIC-056).",
    )
    recall_weights: dict[str, float] = Field(
        default_factory=lambda: {"local": 0.5, "group": 0.3, "hive": 0.2},
        description="Recall weight distribution across local, group, and hive scopes (EPIC-056).",
    )
    auto_publish_tiers: list[str] = Field(
        default_factory=lambda: ["architectural", "pattern"],
        description="Tiers eligible for expert auto-publishing (EPIC-056).",
    )


# ---------------------------------------------------------------------------
# Main profile model
# ---------------------------------------------------------------------------

class ConsolidationProfileConfig(BaseModel):
    """Profile-driven auto-consolidation defaults (Issue #71)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=True, description="Enable auto-consolidation on save.")
    threshold: float = Field(
        default=0.7, ge=0.0, le=1.0, description="Similarity threshold for merging."
    )
    min_entries: int = Field(default=3, ge=1, description="Minimum entries before consolidation.")


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
    seeding: SeedingConfig = Field(
        default_factory=SeedingConfig,
        description="Optional auto-seed versioning (EPIC-044 STORY-044.6).",
    )
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
    lexical: LexicalRetrievalConfig = Field(
        default_factory=LexicalRetrievalConfig,
        description="BM25 tokenization and FTS query term splitting (EPIC-042).",
    )
    hybrid_fusion: HybridFusionConfig = Field(
        default_factory=HybridFusionConfig,
        description="Hybrid search RRF pool sizes and k (STORY-042.4); see HybridFusionConfig.",
    )
    conflict_check: ConflictCheckConfig = Field(
        default_factory=ConflictCheckConfig,
        description="Save-time conflict similarity threshold / aggressiveness (EPIC-044.3).",
    )
    safety: SafetyConfig = Field(
        default_factory=SafetyConfig,
        description="RAG safety ruleset semver pin (EPIC-044.1); see tapps_brain.safety.",
    )
    consolidation: ConsolidationProfileConfig = Field(
        default_factory=ConsolidationProfileConfig,
        description="Auto-consolidation defaults (Issue #71).",
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
        lexical=child.lexical,
        hybrid_fusion=child.hybrid_fusion,
        conflict_check=child.conflict_check,
        safety=child.safety,
        seeding=child.seeding,
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
