"""Tests for configurable memory profiles (EPIC-010)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tapps_brain.profile import (
    GCConfig,
    LayerDefinition,
    LimitsConfig,
    MemoryProfile,
    PromotionThreshold,
    RecallProfileConfig,
    ScoringConfig,
    _merge_profiles,
    _resolve_inheritance,
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
        with pytest.raises(Exception):  # noqa: B017
            LayerDefinition(name="bad", half_life_days=0)

    def test_half_life_days_negative(self) -> None:
        with pytest.raises(Exception):  # noqa: B017
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

    def test_default_bm25_norm_k(self) -> None:
        sc = ScoringConfig()
        assert sc.bm25_norm_k == 5.0

    def test_default_frequency_cap(self) -> None:
        sc = ScoringConfig()
        assert sc.frequency_cap == 20


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
        assert rc.default_token_budget == 2000
        assert rc.default_engagement == "high"
        assert rc.min_score == 0.3
        assert rc.min_confidence == 0.1


class TestLimitsConfig:
    """LimitsConfig model tests."""

    def test_defaults(self) -> None:
        lc = LimitsConfig()
        assert lc.max_entries == 500
        assert lc.max_key_length == 128
        assert lc.max_value_length == 4096
        assert lc.max_tags == 10


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
        assert mp.limits.max_entries == 500


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

    def test_load_valid_yaml_without_profile_key(self, tmp_path: Path) -> None:
        """When top-level key is not 'profile', treat the dict itself as profile data."""
        data = {
            "name": "direct-profile",
            "layers": [
                {"name": "only", "half_life_days": 42},
            ],
        }
        path = _write_yaml(tmp_path / "direct.yaml", data)
        profile = load_profile(path)
        assert profile.name == "direct-profile"

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
        assert profile.recall.default_token_budget == 2000
        assert profile.recall.default_engagement == "high"

    def test_repo_brain_limits_defaults(self) -> None:
        profile = get_builtin_profile("repo-brain")
        assert profile.limits.max_entries == 500


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
