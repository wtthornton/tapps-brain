"""Tests for configurable memory profiles (EPIC-010)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from tapps_brain.lexical import LexicalRetrievalConfig
from tapps_brain.profile import (
    ConflictCheckConfig,
    GCConfig,
    HiveConfig,
    HybridFusionConfig,
    LayerDefinition,
    LimitsConfig,
    MemoryProfile,
    PromotionThreshold,
    RecallProfileConfig,
    ScoringConfig,
    SeedingConfig,
    _merge_profiles,
    _resolve_inheritance,
    composite_scoring_weight_total,
    get_builtin_profile,
    list_builtin_profiles,
    load_profile,
    resolve_profile,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_profile_data() -> dict:
    """Return minimal valid profile data."""
    return {
        "profile": {
            "name": "test-profile",
            "layers": [
                {"name": "top", "half_life_days": 90},
                {"name": "bottom", "half_life_days": 7},
            ],
        }
    }


def _write_yaml(path: Path, data: dict) -> Path:
    """Write *data* as YAML to *path* and return the path."""
    path.write_text(yaml.dump(data, default_flow_style=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 1. Model validation
# ---------------------------------------------------------------------------


class TestLayerDefinition:
    """LayerDefinition model tests."""

    def test_valid_creation(self) -> None:
        layer = LayerDefinition(name="arch", half_life_days=180)
        assert layer.name == "arch"
        assert layer.half_life_days == 180
        assert layer.decay_model == "exponential"
        assert layer.decay_exponent == 1.0
        assert layer.confidence_floor == 0.1
        assert layer.importance_tags == {}
        assert layer.promotion_to is None
        assert layer.demotion_to is None
        assert layer.description == ""

    def test_half_life_days_minimum(self) -> None:
        """half_life_days must be >= 1."""
        with pytest.raises(ValidationError):
            LayerDefinition(name="bad", half_life_days=0)

    def test_half_life_days_negative(self) -> None:
        with pytest.raises(ValidationError):
            LayerDefinition(name="bad", half_life_days=-5)

    def test_valid_half_life_days_one(self) -> None:
        layer = LayerDefinition(name="ephemeral", half_life_days=1)
        assert layer.half_life_days == 1

    def test_decay_model_literal(self) -> None:
        layer = LayerDefinition(name="pl", half_life_days=30, decay_model="power_law")
        assert layer.decay_model == "power_law"

    def test_with_promotion_threshold(self) -> None:
        threshold = PromotionThreshold(min_access_count=3, min_age_days=5, min_confidence=0.6)
        layer = LayerDefinition(
            name="mid",
            half_life_days=30,
            promotion_to="top",
            promotion_threshold=threshold,
        )
        assert layer.promotion_threshold is not None
        assert layer.promotion_threshold.min_access_count == 3


class TestPromotionThreshold:
    """PromotionThreshold model tests."""

    def test_defaults(self) -> None:
        pt = PromotionThreshold()
        assert pt.min_access_count == 5
        assert pt.min_age_days == 7
        assert pt.min_confidence == 0.5

    def test_custom_values(self) -> None:
        pt = PromotionThreshold(min_access_count=10, min_age_days=30, min_confidence=0.9)
        assert pt.min_access_count == 10


class TestHybridFusionConfig:
    """Hybrid RRF profile knobs (STORY-042.4)."""

    def test_defaults_match_retriever_historical(self) -> None:
        h = HybridFusionConfig()
        assert h.adaptive_fusion is True
        assert h.top_k_lexical == 20
        assert h.top_k_dense == 20
        assert h.rrf_k == 60

    def test_validate_top_k_fields(self) -> None:
        h = HybridFusionConfig.model_validate(
            {"top_k_lexical": 40, "top_k_dense": 35, "rrf_k": 48, "adaptive_fusion": False}
        )
        assert h.top_k_lexical == 40
        assert h.top_k_dense == 35
        assert h.rrf_k == 48
        assert h.adaptive_fusion is False

    def test_rejects_unknown_keys(self) -> None:
        with pytest.raises(ValidationError):
            HybridFusionConfig.model_validate({"top_k_lexical": 10, "typo": 1})

    def test_builtin_repo_brain_has_hybrid_fusion_defaults(self) -> None:
        p = get_builtin_profile("repo-brain")
        assert p.hybrid_fusion.top_k_lexical == 20
        assert p.hybrid_fusion.rrf_k == 60


class TestScoringConfig:
    """ScoringConfig model tests."""

    def test_default_weights_valid(self) -> None:
        sc = ScoringConfig()
        total = sc.relevance + sc.confidence + sc.recency + sc.frequency
        assert abs(total - 1.0) < 0.05

    def test_valid_custom_weights(self) -> None:
        sc = ScoringConfig(relevance=0.50, confidence=0.20, recency=0.15, frequency=0.15)
        assert sc.relevance == 0.50

    def test_weights_sum_too_low(self) -> None:
        """Weights summing well below 1.0 must be rejected."""
        with pytest.raises(ValueError, match=r"sum to ~1\.0"):
            ScoringConfig(relevance=0.10, confidence=0.10, recency=0.10, frequency=0.10)

    def test_weights_sum_too_high(self) -> None:
        """Weights summing well above 1.0 must be rejected."""
        with pytest.raises(ValueError, match=r"sum to ~1\.0"):
            ScoringConfig(relevance=0.50, confidence=0.50, recency=0.50, frequency=0.50)

    def test_weights_at_lower_boundary(self) -> None:
        """Total of 0.95 is acceptable."""
        sc = ScoringConfig(relevance=0.35, confidence=0.30, recency=0.15, frequency=0.15)
        assert sc.relevance == 0.35

    def test_weights_at_upper_boundary(self) -> None:
        """Total of 1.05 is acceptable."""
        sc = ScoringConfig(relevance=0.45, confidence=0.30, recency=0.15, frequency=0.15)
        assert sc.relevance == 0.45

    def test_default_frequency_cap(self) -> None:
        sc = ScoringConfig()
        assert sc.frequency_cap == 20

    def test_relevance_normalization_field_removed(self) -> None:
        """relevance_normalization and bm25_norm_k removed; min-max is always used."""
        sc = ScoringConfig()
        assert not hasattr(sc, "relevance_normalization")
        assert not hasattr(sc, "bm25_norm_k")

    def test_default_source_trust_values(self) -> None:
        """source_trust defaults match _DEFAULT_SOURCE_TRUST."""
        sc = ScoringConfig()
        assert sc.source_trust["human"] == 1.0
        assert sc.source_trust["system"] == 0.9
        assert sc.source_trust["agent"] == pytest.approx(0.7)
        assert sc.source_trust["inferred"] == pytest.approx(0.5)

    def test_source_trust_is_mutable_copy(self) -> None:
        """Mutating one ScoringConfig's source_trust must not affect another."""
        sc1 = ScoringConfig()
        sc2 = ScoringConfig()
        sc1.source_trust["human"] = 0.5
        assert sc2.source_trust["human"] == 1.0

    def test_six_weight_blend_valid(self) -> None:
        """When graph/provenance are used, all six weights must sum in band."""
        sc = ScoringConfig(
            relevance=0.35,
            confidence=0.25,
            recency=0.15,
            frequency=0.15,
            graph_centrality=0.05,
            provenance_trust=0.05,
        )
        t = composite_scoring_weight_total(
            sc.relevance,
            sc.confidence,
            sc.recency,
            sc.frequency,
            graph_centrality=sc.graph_centrality,
            provenance_trust=sc.provenance_trust,
        )
        assert abs(t - 1.0) < 0.01

    def test_six_weight_blend_rejected_when_sum_out_of_band(self) -> None:
        with pytest.raises(ValueError, match=r"sum to ~1\.0"):
            ScoringConfig(
                relevance=0.40,
                confidence=0.30,
                recency=0.15,
                frequency=0.15,
                graph_centrality=0.10,
                provenance_trust=0.0,
            )


class TestGCConfig:
    """GCConfig model tests."""

    def test_defaults(self) -> None:
        gc = GCConfig()
        assert gc.floor_retention_days == 30
        assert gc.session_expiry_days == 7
        assert gc.contradicted_threshold == 0.2
        assert gc.stale_threshold == 0.3


class TestRecallProfileConfig:
    """RecallProfileConfig model tests."""

    def test_defaults(self) -> None:
        rc = RecallProfileConfig()
        assert rc.default_token_budget == 3000
        assert rc.default_engagement == "high"
        assert rc.min_score == 0.3
        assert rc.min_confidence == 0.1


class TestLimitsConfig:
    """LimitsConfig model tests."""

    def test_defaults(self) -> None:
        lc = LimitsConfig()
        assert lc.max_entries == 5000
        assert lc.max_entries_per_group is None
        assert lc.max_key_length == 128
        assert lc.max_value_length == 4096
        assert lc.max_tags == 10

    def test_max_entries_per_group_when_set(self) -> None:
        lc = LimitsConfig(max_entries_per_group=200)
        assert lc.max_entries_per_group == 200

    def test_max_entries_per_group_invalid(self) -> None:
        with pytest.raises(ValueError, match="max_entries_per_group"):
            LimitsConfig(max_entries_per_group=0)


class TestMemoryProfileValidation:
    """MemoryProfile model-level validation tests."""

    def test_valid_minimal_profile(self) -> None:
        mp = MemoryProfile(
            name="test",
            layers=[
                LayerDefinition(name="a", half_life_days=90),
                LayerDefinition(name="b", half_life_days=7),
            ],
        )
        assert mp.name == "test"
        assert len(mp.layers) == 2

    def test_duplicate_layer_names_rejected(self) -> None:
        with pytest.raises(ValueError, match="unique"):
            MemoryProfile(
                name="dup",
                layers=[
                    LayerDefinition(name="same", half_life_days=30),
                    LayerDefinition(name="same", half_life_days=60),
                ],
            )

    def test_promotion_to_invalid_reference(self) -> None:
        with pytest.raises(ValueError, match="promotion_to"):
            MemoryProfile(
                name="bad-promo",
                layers=[
                    LayerDefinition(name="a", half_life_days=30, promotion_to="nonexistent"),
                    LayerDefinition(name="b", half_life_days=7),
                ],
            )

    def test_demotion_to_invalid_reference(self) -> None:
        with pytest.raises(ValueError, match="demotion_to"):
            MemoryProfile(
                name="bad-demo",
                layers=[
                    LayerDefinition(name="a", half_life_days=30, demotion_to="nonexistent"),
                    LayerDefinition(name="b", half_life_days=7),
                ],
            )

    def test_valid_promotion_and_demotion_references(self) -> None:
        mp = MemoryProfile(
            name="linked",
            layers=[
                LayerDefinition(name="top", half_life_days=90, demotion_to="bottom"),
                LayerDefinition(name="bottom", half_life_days=7, promotion_to="top"),
            ],
        )
        assert mp.layers[0].demotion_to == "bottom"
        assert mp.layers[1].promotion_to == "top"

    def test_default_version(self) -> None:
        mp = MemoryProfile(
            name="v",
            layers=[LayerDefinition(name="x", half_life_days=10)],
        )
        assert mp.version == "1.0"

    def test_default_scoring(self) -> None:
        mp = MemoryProfile(
            name="s",
            layers=[LayerDefinition(name="x", half_life_days=10)],
        )
        assert mp.scoring.relevance == 0.40
        assert mp.scoring.confidence == 0.30
        assert mp.scoring.recency == 0.15
        assert mp.scoring.frequency == 0.15

    def test_default_limits(self) -> None:
        mp = MemoryProfile(
            name="l",
            layers=[LayerDefinition(name="x", half_life_days=10)],
        )
        assert mp.limits.max_entries == 5000


# ---------------------------------------------------------------------------
# 2. Profile loading
# ---------------------------------------------------------------------------


class TestLoadProfile:
    """load_profile() tests."""

    def test_load_valid_yaml_with_profile_key(self, tmp_path: Path) -> None:
        data = _minimal_profile_data()
        path = _write_yaml(tmp_path / "profile.yaml", data)
        profile = load_profile(path)
        assert profile.name == "test-profile"
        assert len(profile.layers) == 2

    def test_reject_yaml_without_profile_key(self, tmp_path: Path) -> None:
        """Missing 'profile:' wrapper raises ValueError with a clear message."""
        data = {
            "name": "direct-profile",
            "layers": [
                {"name": "only", "half_life_days": 42},
            ],
        }
        path = _write_yaml(tmp_path / "direct.yaml", data)
        with pytest.raises(ValueError, match="top-level 'profile:' key"):
            load_profile(path)

    def test_reject_yaml_with_typo_in_profile_key(self, tmp_path: Path) -> None:
        """A typo like 'profil:' raises ValueError naming the missing key."""
        data = {
            "profil": {
                "name": "typo-profile",
                "layers": [{"name": "main", "half_life_days": 30}],
            }
        }
        path = _write_yaml(tmp_path / "typo.yaml", data)
        with pytest.raises(ValueError, match="top-level 'profile:' key"):
            load_profile(path)

    def test_reject_non_mapping_yaml(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text("- item1\n- item2\n", encoding="utf-8")
        with pytest.raises(ValueError, match="mapping"):
            load_profile(path)

    def test_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_profile(tmp_path / "missing.yaml")

    def test_load_with_all_sections(self, tmp_path: Path) -> None:
        data = {
            "profile": {
                "name": "full",
                "layers": [{"name": "main", "half_life_days": 30}],
                "scoring": {
                    "relevance": 0.50,
                    "confidence": 0.20,
                    "recency": 0.15,
                    "frequency": 0.15,
                },
                "gc": {"floor_retention_days": 60},
                "recall": {"default_token_budget": 3000},
                "limits": {"max_entries": 1000},
            }
        }
        path = _write_yaml(tmp_path / "full.yaml", data)
        profile = load_profile(path)
        assert profile.scoring.relevance == 0.50
        assert profile.gc.floor_retention_days == 60
        assert profile.recall.default_token_budget == 3000
        assert profile.limits.max_entries == 1000

    def test_load_seeding_section(self, tmp_path: Path) -> None:
        data = {
            "profile": {
                "name": "seed-yaml",
                "layers": [{"name": "main", "half_life_days": 30}],
                "seeding": {"seed_version": "yaml-v1"},
            }
        }
        path = _write_yaml(tmp_path / "seed.yaml", data)
        profile = load_profile(path)
        assert profile.seeding.seed_version == "yaml-v1"


# ---------------------------------------------------------------------------
# 3. Built-in profiles
# ---------------------------------------------------------------------------


class TestBuiltinProfiles:
    """Tests for built-in profile discovery and loading."""

    def test_list_builtin_profiles_returns_profiles(self) -> None:
        profiles = list_builtin_profiles()
        assert isinstance(profiles, list)
        assert len(profiles) >= 1
        assert "repo-brain" in profiles

    def test_each_builtin_profile_loads_and_validates(self) -> None:
        for name in list_builtin_profiles():
            profile = get_builtin_profile(name)
            assert profile.name == name
            assert len(profile.layers) > 0

    def test_each_builtin_scoring_weights_sum_to_one(self) -> None:
        for name in list_builtin_profiles():
            profile = get_builtin_profile(name)
            total = (
                profile.scoring.relevance
                + profile.scoring.confidence
                + profile.scoring.recency
                + profile.scoring.frequency
            )
            assert abs(total - 1.0) < 0.05, f"Profile '{name}' scoring weights sum to {total}"

    def test_repo_brain_layer_half_lives(self) -> None:
        profile = get_builtin_profile("repo-brain")
        expected = {
            "architectural": 180,
            "pattern": 60,
            "procedural": 30,
            "context": 14,
        }
        for layer_name, half_life in expected.items():
            layer = profile.get_layer(layer_name)
            assert layer is not None, f"Missing layer '{layer_name}'"
            assert layer.half_life_days == half_life, (
                f"Layer '{layer_name}' half_life_days={layer.half_life_days}, expected {half_life}"
            )

    def test_repo_brain_scoring_defaults(self) -> None:
        profile = get_builtin_profile("repo-brain")
        assert profile.scoring.relevance == 0.40
        assert profile.scoring.confidence == 0.30
        assert profile.scoring.recency == 0.15
        assert profile.scoring.frequency == 0.15

    def test_repo_brain_has_four_layers(self) -> None:
        profile = get_builtin_profile("repo-brain")
        assert len(profile.layers) == 4

    def test_repo_brain_layer_order(self) -> None:
        profile = get_builtin_profile("repo-brain")
        assert profile.layer_names == ["architectural", "pattern", "procedural", "context"]

    def test_get_builtin_nonexistent_raises(self) -> None:
        with pytest.raises(FileNotFoundError, match="nonexistent"):
            get_builtin_profile("nonexistent")

    def test_repo_brain_gc_defaults(self) -> None:
        profile = get_builtin_profile("repo-brain")
        assert profile.gc.floor_retention_days == 30
        assert profile.gc.session_expiry_days == 7
        assert profile.gc.contradicted_threshold == 0.2
        assert profile.gc.stale_threshold == 0.3

    def test_repo_brain_recall_defaults(self) -> None:
        profile = get_builtin_profile("repo-brain")
        assert profile.recall.default_token_budget == 3000
        assert profile.recall.default_engagement == "high"

    def test_repo_brain_limits_defaults(self) -> None:
        profile = get_builtin_profile("repo-brain")
        assert profile.limits.max_entries == 5000


# ---------------------------------------------------------------------------
# 4. Inheritance
# ---------------------------------------------------------------------------


class TestInheritance:
    """Tests for profile inheritance via extends and _merge_profiles."""

    def test_child_inherits_parent_layers(self) -> None:
        parent = MemoryProfile(
            name="parent",
            layers=[
                LayerDefinition(name="a", half_life_days=90),
                LayerDefinition(name="b", half_life_days=30),
            ],
        )
        child = MemoryProfile(
            name="child",
            layers=[LayerDefinition(name="c", half_life_days=7)],
        )
        merged = _merge_profiles(child, parent)
        assert len(merged.layers) == 3
        names = {la.name for la in merged.layers}
        assert names == {"a", "b", "c"}

    def test_child_overrides_parent_layer_same_name(self) -> None:
        parent = MemoryProfile(
            name="parent",
            layers=[LayerDefinition(name="shared", half_life_days=90)],
        )
        child = MemoryProfile(
            name="child",
            layers=[LayerDefinition(name="shared", half_life_days=7)],
        )
        merged = _merge_profiles(child, parent)
        assert len(merged.layers) == 1
        assert merged.layers[0].half_life_days == 7

    def test_child_adds_new_layers(self) -> None:
        parent = MemoryProfile(
            name="parent",
            layers=[LayerDefinition(name="existing", half_life_days=90)],
        )
        child = MemoryProfile(
            name="child",
            layers=[LayerDefinition(name="brand-new", half_life_days=5)],
        )
        merged = _merge_profiles(child, parent)
        names = [la.name for la in merged.layers]
        assert "existing" in names
        assert "brand-new" in names

    def test_merged_profile_has_child_name(self) -> None:
        parent = MemoryProfile(
            name="parent",
            layers=[LayerDefinition(name="a", half_life_days=90)],
        )
        child = MemoryProfile(
            name="my-child",
            layers=[LayerDefinition(name="a", half_life_days=10)],
        )
        merged = _merge_profiles(child, parent)
        assert merged.name == "my-child"
        assert merged.extends is None  # Inheritance resolved

    def test_merged_profile_inherits_description(self) -> None:
        parent = MemoryProfile(
            name="parent",
            description="Parent description",
            layers=[LayerDefinition(name="a", half_life_days=90)],
        )
        child = MemoryProfile(
            name="child",
            description="",
            layers=[LayerDefinition(name="b", half_life_days=7)],
        )
        merged = _merge_profiles(child, parent)
        assert merged.description == "Parent description"

    def test_child_description_overrides_parent(self) -> None:
        parent = MemoryProfile(
            name="parent",
            description="Parent",
            layers=[LayerDefinition(name="a", half_life_days=90)],
        )
        child = MemoryProfile(
            name="child",
            description="Child override",
            layers=[LayerDefinition(name="b", half_life_days=7)],
        )
        merged = _merge_profiles(child, parent)
        assert merged.description == "Child override"

    def test_merge_profiles_carries_child_conflict_check(self) -> None:
        parent = MemoryProfile(
            name="parent",
            layers=[LayerDefinition(name="a", half_life_days=90)],
            conflict_check=ConflictCheckConfig(aggressiveness="low"),
        )
        child = MemoryProfile(
            name="child",
            layers=[LayerDefinition(name="b", half_life_days=7)],
            conflict_check=ConflictCheckConfig(aggressiveness="high"),
        )
        merged = _merge_profiles(child, parent)
        assert merged.conflict_check.aggressiveness == "high"
        assert merged.conflict_check.effective_similarity_threshold() == 0.45

    def test_merge_profiles_carries_child_seeding(self) -> None:
        parent = MemoryProfile(
            name="parent",
            layers=[LayerDefinition(name="a", half_life_days=90)],
            seeding=SeedingConfig(seed_version="parent-v"),
        )
        child = MemoryProfile(
            name="child",
            layers=[LayerDefinition(name="b", half_life_days=7)],
            seeding=SeedingConfig(seed_version="child-v"),
        )
        merged = _merge_profiles(child, parent)
        assert merged.seeding.seed_version == "child-v"

    def test_resolve_inheritance_with_extends(self) -> None:
        """A profile with extends='repo-brain' inherits repo-brain layers."""
        child = MemoryProfile(
            name="custom",
            extends="repo-brain",
            layers=[LayerDefinition(name="extra", half_life_days=3)],
        )
        resolved = _resolve_inheritance(child)
        names = {la.name for la in resolved.layers}
        # Must include repo-brain layers + child's extra layer
        assert "architectural" in names
        assert "pattern" in names
        assert "procedural" in names
        assert "context" in names
        assert "extra" in names
        assert resolved.extends is None

    def test_max_depth_enforcement(self) -> None:
        """Exceeding max inheritance depth raises ValueError."""
        profile = MemoryProfile(
            name="deep",
            extends="repo-brain",
            layers=[LayerDefinition(name="x", half_life_days=5)],
        )
        # depth=3 should trigger the guard
        with pytest.raises(ValueError, match="depth"):
            _resolve_inheritance(profile, depth=3)

    def test_no_extends_returns_unchanged(self) -> None:
        profile = MemoryProfile(
            name="standalone",
            layers=[LayerDefinition(name="only", half_life_days=42)],
        )
        result = _resolve_inheritance(profile)
        assert result.name == "standalone"
        assert len(result.layers) == 1

    def test_resolve_inheritance_invalid_extends_raises(self) -> None:
        """A profile with extends pointing to a non-existent built-in raises FileNotFoundError."""
        profile = MemoryProfile(
            name="bad-extends",
            extends="no-such-profile",
            layers=[LayerDefinition(name="x", half_life_days=5)],
        )
        with pytest.raises(FileNotFoundError, match="no-such-profile"):
            _resolve_inheritance(profile)

    def test_source_confidence_merged(self) -> None:
        parent = MemoryProfile(
            name="parent",
            source_confidence={"human": 0.95, "agent": 0.60},
            layers=[LayerDefinition(name="a", half_life_days=90)],
        )
        child = MemoryProfile(
            name="child",
            source_confidence={"agent": 0.80, "custom": 0.70},
            layers=[LayerDefinition(name="b", half_life_days=7)],
        )
        merged = _merge_profiles(child, parent)
        assert merged.source_confidence["human"] == 0.95  # from parent
        assert merged.source_confidence["agent"] == 0.80  # child overrides
        assert merged.source_confidence["custom"] == 0.70  # child adds


# ---------------------------------------------------------------------------
# 5. Resolution order
# ---------------------------------------------------------------------------


class TestResolveProfile:
    """resolve_profile() resolution order tests."""

    def test_project_profile_takes_priority(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "project"
        tb_dir = project_dir / ".tapps-brain"
        tb_dir.mkdir(parents=True)
        data = {
            "profile": {
                "name": "project-local",
                "layers": [{"name": "proj", "half_life_days": 45}],
            }
        }
        _write_yaml(tb_dir / "profile.yaml", data)
        profile = resolve_profile(project_dir)
        assert profile.name == "project-local"

    def test_fallback_to_builtin_repo_brain(self, tmp_path: Path) -> None:
        """With no project or user profile, falls back to repo-brain."""
        # tmp_path has no .tapps-brain directory
        profile = resolve_profile(tmp_path)
        assert profile.name == "repo-brain"

    def test_explicit_profile_name(self, tmp_path: Path) -> None:
        """profile_name parameter selects a specific built-in profile."""
        profile = resolve_profile(tmp_path, profile_name="repo-brain")
        assert profile.name == "repo-brain"

    def test_explicit_nonexistent_profile_name(self, tmp_path: Path) -> None:
        """Non-existent profile_name raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            resolve_profile(tmp_path, profile_name="does-not-exist")

    def test_user_global_profile_used(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When no project profile exists but user-global does, use it."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        user_tb_dir = fake_home / ".tapps-brain"
        user_tb_dir.mkdir()
        data = {
            "profile": {
                "name": "user-global",
                "layers": [{"name": "global-layer", "half_life_days": 60}],
            }
        }
        _write_yaml(user_tb_dir / "profile.yaml", data)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        profile = resolve_profile(project_dir)
        assert profile.name == "user-global"

    def test_project_profile_with_inheritance(self, tmp_path: Path) -> None:
        """Project profile with extends='repo-brain' merges correctly."""
        project_dir = tmp_path / "project"
        tb_dir = project_dir / ".tapps-brain"
        tb_dir.mkdir(parents=True)
        data = {
            "profile": {
                "name": "project-extended",
                "extends": "repo-brain",
                "layers": [{"name": "custom", "half_life_days": 3}],
            }
        }
        _write_yaml(tb_dir / "profile.yaml", data)
        profile = resolve_profile(project_dir)
        assert profile.name == "project-extended"
        names = {la.name for la in profile.layers}
        assert "custom" in names
        assert "architectural" in names  # inherited from repo-brain


# ---------------------------------------------------------------------------
# 6. Helper methods
# ---------------------------------------------------------------------------


class TestHelperMethods:
    """MemoryProfile helper method tests."""

    @pytest.fixture()
    def sample_profile(self) -> MemoryProfile:
        return MemoryProfile(
            name="helpers",
            layers=[
                LayerDefinition(name="architectural", half_life_days=180),
                LayerDefinition(name="pattern", half_life_days=60),
                LayerDefinition(name="context", half_life_days=7),
            ],
        )

    def test_get_layer_found(self, sample_profile: MemoryProfile) -> None:
        layer = sample_profile.get_layer("architectural")
        assert layer is not None
        assert layer.name == "architectural"
        assert layer.half_life_days == 180

    def test_get_layer_not_found(self, sample_profile: MemoryProfile) -> None:
        assert sample_profile.get_layer("nonexistent") is None

    def test_get_layer_or_default_found(self, sample_profile: MemoryProfile) -> None:
        layer = sample_profile.get_layer_or_default("pattern")
        assert layer.name == "pattern"

    def test_get_layer_or_default_fallback(self, sample_profile: MemoryProfile) -> None:
        """Fallback returns the layer with the shortest half_life_days."""
        layer = sample_profile.get_layer_or_default("nonexistent")
        assert layer.name == "context"  # half_life_days=7 is the shortest
        assert layer.half_life_days == 7

    def test_layer_names_property(self, sample_profile: MemoryProfile) -> None:
        assert sample_profile.layer_names == ["architectural", "pattern", "context"]

    def test_layer_names_preserves_order(self) -> None:
        mp = MemoryProfile(
            name="ordered",
            layers=[
                LayerDefinition(name="z", half_life_days=10),
                LayerDefinition(name="a", half_life_days=20),
                LayerDefinition(name="m", half_life_days=30),
            ],
        )
        assert mp.layer_names == ["z", "a", "m"]

    def test_get_layer_or_default_with_single_layer(self) -> None:
        mp = MemoryProfile(
            name="single",
            layers=[LayerDefinition(name="only", half_life_days=42)],
        )
        layer = mp.get_layer_or_default("missing")
        assert layer.name == "only"


# ---------------------------------------------------------------------------
# 7. Bug-fix regression tests
# ---------------------------------------------------------------------------


class TestMergeProfilesHive:
    """_merge_profiles must preserve child hive configuration (BUG-FIX)."""

    def test_child_hive_config_preserved_after_merge(self) -> None:
        """The child's hive config must not be silently dropped during inheritance."""
        parent = MemoryProfile(
            name="parent",
            layers=[LayerDefinition(name="a", half_life_days=90)],
        )
        child_hive = HiveConfig(
            auto_propagate_tiers=["architectural"],
            private_tiers=["context"],
            conflict_policy="confidence_max",
            recall_weight=0.6,
        )
        child = MemoryProfile(
            name="child",
            layers=[LayerDefinition(name="b", half_life_days=7)],
            hive=child_hive,
        )
        merged = _merge_profiles(child, parent)
        assert merged.hive.auto_propagate_tiers == ["architectural"]
        assert merged.hive.private_tiers == ["context"]
        assert merged.hive.conflict_policy == "confidence_max"
        assert merged.hive.recall_weight == 0.6

    def test_merge_hive_default_when_child_is_default(self) -> None:
        """When child uses default HiveConfig, the merged result also has defaults."""
        parent = MemoryProfile(
            name="parent",
            layers=[LayerDefinition(name="a", half_life_days=90)],
            hive=HiveConfig(auto_propagate_tiers=["architectural"], recall_weight=0.5),
        )
        child = MemoryProfile(
            name="child",
            layers=[LayerDefinition(name="b", half_life_days=7)],
            # No hive specified — uses default HiveConfig()
        )
        merged = _merge_profiles(child, parent)
        # Child's default HiveConfig wins over parent (full child.hive replaces)
        assert merged.hive.auto_propagate_tiers == ["architectural", "pattern"]
        assert merged.hive.recall_weight == 0.8  # default


class TestMergeProfilesLexical:
    """Child lexical config must replace parent on merge."""

    def test_child_lexical_preserved(self) -> None:
        parent = MemoryProfile(
            name="parent",
            layers=[LayerDefinition(name="a", half_life_days=90)],
        )
        child = MemoryProfile(
            name="child",
            layers=[LayerDefinition(name="b", half_life_days=7)],
            lexical=LexicalRetrievalConfig(apply_stem=False, ascii_fold=True),
        )
        merged = _merge_profiles(child, parent)
        assert merged.lexical.apply_stem is False
        assert merged.lexical.ascii_fold is True


class TestConflictCheckConfig:
    """EPIC-044.3: profile-driven save conflict similarity cutoff."""

    def test_aggressiveness_tiers(self) -> None:
        assert ConflictCheckConfig(aggressiveness="low").effective_similarity_threshold() == 0.75
        assert ConflictCheckConfig(aggressiveness="medium").effective_similarity_threshold() == 0.6
        assert ConflictCheckConfig(aggressiveness="high").effective_similarity_threshold() == 0.45

    def test_similarity_threshold_overrides_aggressiveness(self) -> None:
        c = ConflictCheckConfig(aggressiveness="low", similarity_threshold=0.3)
        assert c.effective_similarity_threshold() == 0.3

    def test_yaml_roundtrip_under_profile(self, tmp_path: Path) -> None:
        data = {
            "profile": {
                "name": "cc-yaml",
                "layers": [{"name": "pattern", "half_life_days": 30}],
                "conflict_check": {"aggressiveness": "high"},
            }
        }
        p = _write_yaml(tmp_path / "p.yaml", data)
        mp = load_profile(p)
        assert mp.conflict_check.aggressiveness == "high"
        assert mp.conflict_check.effective_similarity_threshold() == 0.45


class TestGetBuiltinProfileSecurity:
    """get_builtin_profile must reject path-traversal names (BUG-FIX)."""

    def test_path_traversal_with_dotdot_rejected(self) -> None:
        with pytest.raises(ValueError, match="path separators"):
            get_builtin_profile("../../etc/passwd")

    def test_path_traversal_with_slash_rejected(self) -> None:
        with pytest.raises(ValueError, match="path separators"):
            get_builtin_profile("subdir/malicious")

    def test_path_traversal_with_backslash_rejected(self) -> None:
        with pytest.raises(ValueError, match="path separators"):
            get_builtin_profile("subdir\\malicious")

    def test_name_starting_with_dot_rejected(self) -> None:
        with pytest.raises(ValueError, match="path separators"):
            get_builtin_profile(".hidden")

    def test_valid_hyphenated_name_accepted(self) -> None:
        """Normal hyphenated profile names must still work."""
        profile = get_builtin_profile("repo-brain")
        assert profile.name == "repo-brain"

    def test_valid_underscored_name_raises_file_not_found(self) -> None:
        """A safe but non-existent name raises FileNotFoundError, not ValueError."""
        with pytest.raises(FileNotFoundError):
            get_builtin_profile("no_such_profile")


class TestValidateLayersDuplicateDetection:
    """_validate_layers duplicate detection must work correctly (code quality)."""

    def test_single_duplicate_detected(self) -> None:
        with pytest.raises(ValueError, match="unique"):
            MemoryProfile(
                name="dup",
                layers=[
                    LayerDefinition(name="x", half_life_days=10),
                    LayerDefinition(name="x", half_life_days=20),
                ],
            )

    def test_multiple_duplicates_detected(self) -> None:
        with pytest.raises(ValueError, match="unique"):
            MemoryProfile(
                name="multi-dup",
                layers=[
                    LayerDefinition(name="a", half_life_days=10),
                    LayerDefinition(name="b", half_life_days=20),
                    LayerDefinition(name="a", half_life_days=30),
                    LayerDefinition(name="b", half_life_days=40),
                ],
            )

    def test_all_unique_names_accepted(self) -> None:
        mp = MemoryProfile(
            name="ok",
            layers=[
                LayerDefinition(name="alpha", half_life_days=90),
                LayerDefinition(name="beta", half_life_days=30),
                LayerDefinition(name="gamma", half_life_days=7),
            ],
        )
        assert len(mp.layers) == 3
