"""Tests for memory decay engine (Epic 24.1)."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from tapps_brain.decay import (
    DecayConfig,
    _days_since,
    calculate_decayed_confidence,
    decay_config_from_profile,
    exponential_decay,
    get_effective_confidence,
    is_stale,
    power_law_decay,
    update_stability,
)
from tapps_brain.models import MemoryEntry, MemorySource, MemoryTier
from tests.factories import make_entry


def _make_entry(
    *,
    tier: MemoryTier = MemoryTier.pattern,
    source: MemorySource = MemorySource.agent,
    confidence: float = 0.8,
    updated_at: str | None = None,
    last_reinforced: str | None = None,
    stability: float | None = None,
    difficulty: float | None = None,
) -> MemoryEntry:
    """Helper to create a MemoryEntry with controlled timestamps."""
    return make_entry(
        tier=tier,
        source=source,
        confidence=confidence,
        updated_at=updated_at,
        last_reinforced=last_reinforced,
        stability=stability,
        difficulty=difficulty,
    )


@pytest.fixture
def config() -> DecayConfig:
    return DecayConfig()


class TestDecayConfig:
    def test_default_half_lives(self) -> None:
        cfg = DecayConfig()
        assert cfg.architectural_half_life_days == 180
        assert cfg.pattern_half_life_days == 60
        assert cfg.procedural_half_life_days == 30  # story-019.1: procedural tier added
        assert cfg.context_half_life_days == 14

    def test_default_ceilings(self) -> None:
        cfg = DecayConfig()
        assert cfg.human_confidence_ceiling == 0.95
        assert cfg.agent_confidence_ceiling == 0.85
        assert cfg.inferred_confidence_ceiling == 0.70
        assert cfg.confidence_floor == 0.1

    def test_default_decay_k_is_fsrs_canonical(self) -> None:
        """STORY-SC02 (TAP-558): default decay_k = 81/19 (FSRS-canonical)."""
        cfg = DecayConfig()
        assert cfg.decay_k == pytest.approx(81.0 / 19.0)

    def test_decay_exponent_upper_bound_is_10(self) -> None:
        """STORY-SC02 (TAP-558): decay_exponent accepts values up to 10.0
        (needed for the half-life-anchor β ≈ 3.29 and future fits)."""
        DecayConfig(decay_exponent=10.0)  # upper bound accepted
        with pytest.raises(ValidationError):
            DecayConfig(decay_exponent=10.1)

    def test_decay_exponent_lower_bound(self) -> None:
        DecayConfig(decay_exponent=0.1)
        with pytest.raises(ValidationError):
            DecayConfig(decay_exponent=0.09)

    def test_decay_k_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            DecayConfig(decay_k=0.0)
        with pytest.raises(ValidationError):
            DecayConfig(decay_k=-1.0)


class TestPowerLawDecayFunction:
    """STORY-SC02 (TAP-558): standalone tests for ``power_law_decay``."""

    _K = 81.0 / 19.0

    def test_r_at_zero_is_one(self) -> None:
        assert power_law_decay(0.0, half_life=30.0, decay_exponent=3.29) == 1.0

    def test_negative_days_clamped_to_one(self) -> None:
        assert power_law_decay(-5.0, half_life=30.0, decay_exponent=3.29) == 1.0

    def test_monotonic_decreasing(self) -> None:
        """R(t) must strictly decrease in t for any positive β, H, k."""
        hl = 30.0
        beta = 3.29
        prev = 1.0
        for t in (1, 5, 10, 30, 60, 90, 180, 365):
            r = power_law_decay(float(t), hl, beta)
            assert 0.0 < r < prev, f"R({t}) = {r} should be < prev {prev}"
            prev = r

    def test_half_life_anchor_beta_gives_one_half_at_half_life(self) -> None:
        """With β = ln 2 / ln(1 + 1/k), R(t=H) == 0.5 exactly. This is the
        calibration used by the `repo-brain` profile migration (STORY-SC02)
        to preserve median retention across the exponential → power-law swap.
        """
        import math as _math

        k = self._K
        beta = _math.log(2.0) / _math.log(1.0 + 1.0 / k)
        for hl in (14.0, 30.0, 60.0, 180.0, 365.0):
            r = power_law_decay(hl, hl, beta, k=k)
            assert abs(r - 0.5) < 1e-12, f"R(H={hl}) = {r}, expected 0.5"

    def test_fatter_tail_than_exponential(self) -> None:
        """Power-law tail (with anchored β) must be fatter than exponential
        past the half-life — this is the whole point of the migration."""
        import math as _math

        hl = 60.0
        k = self._K
        beta = _math.log(2.0) / _math.log(1.0 + 1.0 / k)

        # Beyond H, power_law > exponential (fatter tail)
        for t in (120.0, 240.0, 480.0):
            pl = power_law_decay(t, hl, beta, k=k)
            ex = math.pow(0.5, t / hl)
            assert pl > ex, f"t={t}: power_law {pl} should exceed exponential {ex}"

    def test_k_param_changes_curve_shape(self) -> None:
        """Smaller k → steeper near-term decay for the same β."""
        beta = 3.29
        hl = 30.0
        r_small_k = power_law_decay(5.0, hl, beta, k=1.0)
        r_large_k = power_law_decay(5.0, hl, beta, k=10.0)
        assert r_small_k < r_large_k


class TestExponentialDecayFunction:
    """STORY-SC02 (TAP-558): standalone tests for ``exponential_decay``."""

    def test_r_at_zero_is_one(self) -> None:
        assert exponential_decay(0.0, half_life=30.0) == 1.0

    def test_negative_days_clamped_to_one(self) -> None:
        assert exponential_decay(-5.0, half_life=30.0) == 1.0

    def test_r_at_half_life_is_one_half(self) -> None:
        for hl in (14.0, 30.0, 60.0, 180.0, 365.0):
            assert exponential_decay(hl, hl) == pytest.approx(0.5, abs=1e-12)

    def test_monotonic_decreasing(self) -> None:
        prev = 1.0
        for t in (1, 5, 10, 30, 60, 90, 180):
            r = exponential_decay(float(t), 30.0)
            assert 0.0 < r < prev
            prev = r


class TestPerLayerDecayKOverride:
    """STORY-SC02 (TAP-558): per-category ``decay_k`` configuration."""

    def test_layer_decay_k_takes_precedence_over_global(self) -> None:
        """When ``layer_decay_k`` has an entry for a tier, it overrides
        the global ``decay_k`` for that tier only."""
        now = datetime.now(tz=UTC)
        updated = (now - timedelta(days=30)).isoformat()

        # Global k=4.263 (FSRS), override architectural to k=1.0 (steeper)
        config = DecayConfig(
            decay_model="power_law",
            decay_exponent=3.29,
            layer_decay_models={"architectural": "power_law", "pattern": "power_law"},
            layer_decay_exponents={"architectural": 3.29, "pattern": 3.29},
            layer_decay_k={"architectural": 1.0},  # steeper curve for architectural only
        )

        arch = _make_entry(tier=MemoryTier.architectural, confidence=0.8, updated_at=updated)
        pat = _make_entry(tier=MemoryTier.pattern, confidence=0.8, updated_at=updated)

        arch_decayed = calculate_decayed_confidence(arch, config, now=now)
        pat_decayed = calculate_decayed_confidence(pat, config, now=now)

        # Same half-life & β, smaller k → steeper → lower confidence. Architectural
        # (180d HL) normally decays slower than pattern (60d HL), so if our k override
        # pushes architectural below pattern, the override is definitely taking effect.
        assert arch_decayed < pat_decayed, (
            f"arch (k=1.0) {arch_decayed} should decay faster than pat (k=4.263) {pat_decayed}"
        )

    def test_per_layer_decay_k_absent_falls_back_to_global(self) -> None:
        """Without a layer entry, the global ``decay_k`` applies."""
        now = datetime.now(tz=UTC)
        updated = (now - timedelta(days=30)).isoformat()

        config_explicit = DecayConfig(
            decay_model="power_law",
            decay_exponent=3.29,
            decay_k=2.0,
            layer_decay_models={"pattern": "power_law"},
            layer_decay_exponents={"pattern": 3.29},
            layer_decay_k={"pattern": 2.0},
        )
        config_fallback = DecayConfig(
            decay_model="power_law",
            decay_exponent=3.29,
            decay_k=2.0,
            layer_decay_models={"pattern": "power_law"},
            layer_decay_exponents={"pattern": 3.29},
            # no layer_decay_k entry for pattern
        )

        entry = _make_entry(tier=MemoryTier.pattern, confidence=0.8, updated_at=updated)
        r1 = calculate_decayed_confidence(entry, config_explicit, now=now)
        r2 = calculate_decayed_confidence(entry, config_fallback, now=now)
        assert r1 == pytest.approx(r2, abs=1e-12)


class TestCalculateDecayedConfidence:
    def test_fresh_memory_returns_original_confidence(self, config: DecayConfig) -> None:
        """A memory created just now should return ~original confidence."""
        entry = _make_entry(confidence=0.8)
        now = datetime.now(tz=UTC)
        result = calculate_decayed_confidence(entry, config, now=now)
        assert abs(result - 0.8) < 0.01

    def test_pattern_at_half_life_returns_half(self, config: DecayConfig) -> None:
        """A pattern memory at exactly its half-life (60 days) returns ~50% confidence."""
        now = datetime.now(tz=UTC)
        updated = (now - timedelta(days=60)).isoformat()
        entry = _make_entry(tier=MemoryTier.pattern, confidence=0.8, updated_at=updated)
        result = calculate_decayed_confidence(entry, config, now=now)
        assert abs(result - 0.4) < 0.01

    def test_pattern_at_double_half_life_returns_quarter(self, config: DecayConfig) -> None:
        """A pattern memory at 2x half-life (120 days) returns ~25% confidence."""
        now = datetime.now(tz=UTC)
        updated = (now - timedelta(days=120)).isoformat()
        entry = _make_entry(tier=MemoryTier.pattern, confidence=0.8, updated_at=updated)
        result = calculate_decayed_confidence(entry, config, now=now)
        assert abs(result - 0.2) < 0.01

    def test_architectural_decays_slower(self, config: DecayConfig) -> None:
        """Architectural (180d half-life) decays slower than pattern (60d)."""
        now = datetime.now(tz=UTC)
        updated = (now - timedelta(days=60)).isoformat()

        arch = _make_entry(tier=MemoryTier.architectural, confidence=0.8, updated_at=updated)
        pat = _make_entry(tier=MemoryTier.pattern, confidence=0.8, updated_at=updated)

        arch_conf = calculate_decayed_confidence(arch, config, now=now)
        pat_conf = calculate_decayed_confidence(pat, config, now=now)
        assert arch_conf > pat_conf

    def test_context_decays_fastest(self, config: DecayConfig) -> None:
        """Context (14d half-life) decays faster than pattern (60d)."""
        now = datetime.now(tz=UTC)
        updated = (now - timedelta(days=14)).isoformat()

        ctx = _make_entry(tier=MemoryTier.context, confidence=0.8, updated_at=updated)
        pat = _make_entry(tier=MemoryTier.pattern, confidence=0.8, updated_at=updated)

        ctx_conf = calculate_decayed_confidence(ctx, config, now=now)
        pat_conf = calculate_decayed_confidence(pat, config, now=now)
        assert ctx_conf < pat_conf

    def test_procedural_decays_between_pattern_and_context(self, config: DecayConfig) -> None:
        """Procedural (30d half-life) decays slower than context (14d).

        Faster than pattern (60d). Epic 65.11.
        """
        now = datetime.now(tz=UTC)
        updated = (now - timedelta(days=30)).isoformat()

        proc = _make_entry(tier=MemoryTier.procedural, confidence=0.8, updated_at=updated)
        ctx = _make_entry(tier=MemoryTier.context, confidence=0.8, updated_at=updated)
        pat = _make_entry(tier=MemoryTier.pattern, confidence=0.8, updated_at=updated)

        proc_conf = calculate_decayed_confidence(proc, config, now=now)
        ctx_conf = calculate_decayed_confidence(ctx, config, now=now)
        pat_conf = calculate_decayed_confidence(pat, config, now=now)
        assert ctx_conf < proc_conf < pat_conf

    def test_confidence_floor_prevents_zero(self, config: DecayConfig) -> None:
        """Confidence never drops below the floor (0.1)."""
        now = datetime.now(tz=UTC)
        updated = (now - timedelta(days=3650)).isoformat()  # ~10 years
        entry = _make_entry(confidence=0.8, updated_at=updated)
        result = calculate_decayed_confidence(entry, config, now=now)
        assert result == config.confidence_floor

    def test_human_ceiling_enforced(self, config: DecayConfig) -> None:
        """Human source ceiling (0.95) is enforced even for fresh memories."""
        entry = _make_entry(source=MemorySource.human, confidence=1.0)
        now = datetime.now(tz=UTC)
        result = calculate_decayed_confidence(entry, config, now=now)
        assert result <= config.human_confidence_ceiling

    def test_agent_ceiling_enforced(self, config: DecayConfig) -> None:
        """Agent source ceiling (0.85) is enforced."""
        entry = _make_entry(source=MemorySource.agent, confidence=0.9)
        now = datetime.now(tz=UTC)
        result = calculate_decayed_confidence(entry, config, now=now)
        assert result <= config.agent_confidence_ceiling

    def test_inferred_ceiling_enforced(self, config: DecayConfig) -> None:
        """Inferred source ceiling (0.70) is enforced."""
        entry = _make_entry(source=MemorySource.inferred, confidence=0.8)
        now = datetime.now(tz=UTC)
        result = calculate_decayed_confidence(entry, config, now=now)
        assert result <= config.inferred_confidence_ceiling

    def test_reinforced_memory_uses_reinforced_time(self, config: DecayConfig) -> None:
        """When last_reinforced is set, decay measures from that timestamp."""
        now = datetime.now(tz=UTC)
        old_update = (now - timedelta(days=120)).isoformat()
        recent_reinforce = (now - timedelta(days=1)).isoformat()

        entry = _make_entry(
            confidence=0.8,
            updated_at=old_update,
            last_reinforced=recent_reinforce,
        )
        result = calculate_decayed_confidence(entry, config, now=now)
        # Should be close to 0.8 since reinforced 1 day ago
        assert result > 0.75


class TestIsStale:
    def test_fresh_memory_not_stale(self, config: DecayConfig) -> None:
        entry = _make_entry(confidence=0.8)
        now = datetime.now(tz=UTC)
        assert not is_stale(entry, config, now=now)

    def test_old_memory_is_stale(self, config: DecayConfig) -> None:
        now = datetime.now(tz=UTC)
        updated = (now - timedelta(days=200)).isoformat()
        entry = _make_entry(confidence=0.5, updated_at=updated)
        assert is_stale(entry, config, now=now)

    def test_custom_threshold(self, config: DecayConfig) -> None:
        now = datetime.now(tz=UTC)
        updated = (now - timedelta(days=30)).isoformat()
        entry = _make_entry(confidence=0.8, updated_at=updated)
        # With high threshold, even moderate decay triggers stale
        assert is_stale(entry, config, threshold=0.8, now=now)


class TestGetEffectiveConfidence:
    def test_returns_tuple(self, config: DecayConfig) -> None:
        entry = _make_entry(confidence=0.8)
        now = datetime.now(tz=UTC)
        result = get_effective_confidence(entry, config, now=now)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], float)
        assert isinstance(result[1], bool)

    def test_fresh_memory_not_stale(self, config: DecayConfig) -> None:
        entry = _make_entry(confidence=0.8)
        now = datetime.now(tz=UTC)
        decayed, stale = get_effective_confidence(entry, config, now=now)
        assert decayed > 0.7
        assert not stale


class TestUpdateStability:
    """EPIC-042.8: deterministic FSRS-lite stability updates."""

    def test_useful_access_increases_stability(self, config: DecayConfig) -> None:
        now = datetime.now(tz=UTC)
        old = (now - timedelta(days=10)).isoformat()
        entry = _make_entry(
            tier=MemoryTier.pattern,
            confidence=0.8,
            updated_at=old,
            stability=0.0,
            difficulty=0.0,
        )
        s_new, d_new = update_stability(entry, config, True, now=now)
        # Initialized from pattern half-life (60d), then grown on useful recall
        assert s_new > 60.0
        assert d_new == pytest.approx(3.0)  # default difficulty for pattern tier

    def test_non_useful_access_shrinks_stability(self, config: DecayConfig) -> None:
        now = datetime.now(tz=UTC)
        entry = _make_entry(
            tier=MemoryTier.pattern,
            confidence=0.8,
            stability=100.0,
            difficulty=5.0,
        )
        s_new, d_new = update_stability(entry, config, False, now=now)
        assert s_new == pytest.approx(80.0)
        assert d_new == pytest.approx(5.0)


class TestDaysSince:
    def test_zero_for_now(self) -> None:
        now = datetime.now(tz=UTC)
        result = _days_since(now.isoformat(), now)
        assert result < 0.001

    def test_one_day(self) -> None:
        now = datetime.now(tz=UTC)
        yesterday = (now - timedelta(days=1)).isoformat()
        result = _days_since(yesterday, now)
        assert abs(result - 1.0) < 0.01

    def test_invalid_timestamp_returns_zero(self) -> None:
        result = _days_since("not-a-timestamp")
        assert result == 0.0

    def test_naive_timestamp_treated_as_utc(self) -> None:
        now = datetime.now(tz=UTC)
        naive = now.replace(tzinfo=None) - timedelta(days=5)
        result = _days_since(naive.isoformat(), now)
        assert abs(result - 5.0) < 0.01


class TestDecayConfigFromProfile:
    """Tests for decay_config_from_profile type safety and correctness (BUG-001-B)."""

    def _make_profile(self) -> object:
        """Build a MemoryProfile with standard and custom layers."""
        from tapps_brain.profile import LayerDefinition, MemoryProfile

        return MemoryProfile(
            name="test-profile",
            layers=[
                LayerDefinition(name="architectural", half_life_days=200, confidence_floor=0.15),
                LayerDefinition(name="pattern", half_life_days=70, confidence_floor=0.12),
                LayerDefinition(name="procedural", half_life_days=45, confidence_floor=0.10),
                LayerDefinition(name="context", half_life_days=10, confidence_floor=0.05),
                LayerDefinition(name="custom_tier", half_life_days=90, confidence_floor=0.08),
            ],
            source_ceilings={"human": 0.98, "agent": 0.80},
        )

    def test_returns_decay_config_instance(self) -> None:
        profile = self._make_profile()
        config = decay_config_from_profile(profile)
        assert isinstance(config, DecayConfig)

    def test_legacy_fields_are_int(self) -> None:
        """BUG-001-B: legacy half_life_days fields must be int, not Any."""
        profile = self._make_profile()
        config = decay_config_from_profile(profile)
        assert isinstance(config.architectural_half_life_days, int)
        assert isinstance(config.pattern_half_life_days, int)
        assert isinstance(config.procedural_half_life_days, int)
        assert isinstance(config.context_half_life_days, int)

    def test_layer_half_lives_populated(self) -> None:
        """Profile layer half-lives are populated in layer_half_lives dict."""
        profile = self._make_profile()
        config = decay_config_from_profile(profile)
        assert config.layer_half_lives["architectural"] == 200
        assert config.layer_half_lives["pattern"] == 70
        assert config.layer_half_lives["procedural"] == 45
        assert config.layer_half_lives["context"] == 10

    def test_default_legacy_fields_when_layers_absent(self) -> None:
        """When profile has no standard layers, defaults are used."""
        from tapps_brain.profile import LayerDefinition, MemoryProfile

        profile = MemoryProfile(
            name="custom-only",
            layers=[LayerDefinition(name="custom_tier", half_life_days=90, confidence_floor=0.08)],
        )
        config = decay_config_from_profile(profile)
        assert config.architectural_half_life_days == 180
        assert config.pattern_half_life_days == 60
        assert config.procedural_half_life_days == 30
        assert config.context_half_life_days == 14

    def test_non_profile_returns_default_config(self) -> None:
        """Non-MemoryProfile objects return default DecayConfig."""
        config = decay_config_from_profile("not a profile")
        assert config == DecayConfig()

    def test_source_ceilings_propagated(self) -> None:
        profile = self._make_profile()
        config = decay_config_from_profile(profile)
        assert config.human_confidence_ceiling == pytest.approx(0.98)
        assert config.agent_confidence_ceiling == pytest.approx(0.80)

    def test_confidence_floor_is_float(self) -> None:
        profile = self._make_profile()
        config = decay_config_from_profile(profile)
        assert isinstance(config.confidence_floor, float)


class TestUnknownTierRaisesValueError:
    """Tests for unknown tier ValueError in _get_half_life."""

    def test_unknown_tier_raises_value_error(self) -> None:
        """Unknown tier string raises ValueError instead of falling back."""
        from tapps_brain.decay import _get_half_life

        config = DecayConfig()
        with pytest.raises(ValueError, match="Unknown tier"):
            _get_half_life("totally_unknown_tier", config)

    def test_known_enum_tier_does_not_log_warning(self) -> None:
        """Known MemoryTier enum values do not trigger the fallback warning."""
        from unittest.mock import MagicMock, patch

        from tapps_brain.decay import _get_half_life

        config = DecayConfig()
        mock_logger = MagicMock()
        with patch("tapps_brain.decay.logger", mock_logger):
            _get_half_life(MemoryTier.architectural, config)

        mock_logger.warning.assert_not_called()

    def test_known_string_tier_does_not_log_warning(self) -> None:
        """Known tier name as string (e.g. 'pattern') does not trigger warning."""
        from unittest.mock import MagicMock, patch

        from tapps_brain.decay import _get_half_life

        config = DecayConfig()
        mock_logger = MagicMock()
        with patch("tapps_brain.decay.logger", mock_logger):
            result = _get_half_life("pattern", config)

        assert result == config.pattern_half_life_days
        mock_logger.warning.assert_not_called()

    def test_profile_custom_tier_does_not_log_warning(self) -> None:
        """Custom tier defined in profile layer_half_lives does not trigger warning."""
        from unittest.mock import MagicMock, patch

        from tapps_brain.decay import _get_half_life

        config = DecayConfig(layer_half_lives={"custom_layer": 45})
        mock_logger = MagicMock()
        with patch("tapps_brain.decay.logger", mock_logger):
            result = _get_half_life("custom_layer", config)

        assert result == 45
        mock_logger.warning.assert_not_called()


class TestUnknownSourceRaisesValueError:
    """Tests for unknown source ValueError in _get_ceiling."""

    def test_unknown_source_raises_value_error(self) -> None:
        """Unknown source string raises ValueError instead of falling back."""
        from tapps_brain.decay import _get_ceiling

        config = DecayConfig()
        with pytest.raises(ValueError, match="Unknown source"):
            _get_ceiling("totally_unknown_source", config)

    def test_known_enum_source_does_not_log_warning(self) -> None:
        """Known MemorySource enum values do not trigger the fallback warning."""
        from unittest.mock import MagicMock, patch

        from tapps_brain.decay import _get_ceiling

        config = DecayConfig()
        mock_logger = MagicMock()
        with patch("tapps_brain.decay.logger", mock_logger):
            _get_ceiling(MemorySource.human, config)

        mock_logger.warning.assert_not_called()

    def test_known_string_source_does_not_log_warning(self) -> None:
        """Known source name as string (e.g. 'human') does not trigger warning."""
        from unittest.mock import MagicMock, patch

        from tapps_brain.decay import _get_ceiling

        config = DecayConfig()
        mock_logger = MagicMock()
        with patch("tapps_brain.decay.logger", mock_logger):
            result = _get_ceiling("human", config)

        assert result == config.human_confidence_ceiling
        mock_logger.warning.assert_not_called()


class TestLayerHalfLivesValidation:
    """Tests for layer_half_lives dict value validation (review 019-A)."""

    def test_valid_layer_half_lives_accepted(self) -> None:
        """Dict with valid ge=1 values is accepted."""
        config = DecayConfig(layer_half_lives={"custom": 7, "other": 1})
        assert config.layer_half_lives["custom"] == 7
        assert config.layer_half_lives["other"] == 1

    def test_zero_layer_half_life_raises(self) -> None:
        """A value of 0 in layer_half_lives raises ValueError to prevent ZeroDivisionError."""
        with pytest.raises(ValidationError):
            DecayConfig(layer_half_lives={"bad_layer": 0})

    def test_negative_layer_half_life_raises(self) -> None:
        """A negative value in layer_half_lives raises ValueError."""
        with pytest.raises(ValidationError):
            DecayConfig(layer_half_lives={"bad_layer": -5})


class TestPersonalAssistantProfileDecay:
    """Regression tests for issue #11: personal-assistant profile ephemeral tier decay.

    The personal-assistant profile defines layers: identity, long-term, short-term,
    ephemeral. None of the first three are MemoryTier enum values, so they rely on
    ``layer_half_lives`` from the profile's DecayConfig. This test ensures no
    ``unknown_tier_fallback`` warning fires and the correct half-lives are used.
    """

    def _make_decay_config_from_personal_assistant(self) -> DecayConfig:
        """Load personal-assistant profile and build its DecayConfig."""
        from tapps_brain.decay import decay_config_from_profile
        from tapps_brain.profile import get_builtin_profile

        profile = get_builtin_profile("personal-assistant")
        return decay_config_from_profile(profile)

    def test_ephemeral_tier_uses_1_day_half_life(self) -> None:
        """personal-assistant ephemeral layer has half_life_days=1; decay uses that value."""
        from tapps_brain.decay import _get_half_life

        config = self._make_decay_config_from_personal_assistant()
        half_life = _get_half_life("ephemeral", config)
        assert half_life == 1

    def test_identity_tier_uses_365_day_half_life(self) -> None:
        """personal-assistant identity layer has half_life_days=365."""
        from tapps_brain.decay import _get_half_life

        config = self._make_decay_config_from_personal_assistant()
        half_life = _get_half_life("identity", config)
        assert half_life == 365

    def test_short_term_tier_uses_7_day_half_life(self) -> None:
        """personal-assistant short-term layer has half_life_days=7."""
        from tapps_brain.decay import _get_half_life

        config = self._make_decay_config_from_personal_assistant()
        half_life = _get_half_life("short-term", config)
        assert half_life == 7

    def test_no_unknown_tier_fallback_warning_for_profile_tiers(self) -> None:
        """Profile-defined tiers (identity, long-term, short-term, ephemeral) must
        not trigger unknown_tier_fallback warning — fix for issue #11."""
        from unittest.mock import MagicMock, patch

        from tapps_brain.decay import _get_half_life

        config = self._make_decay_config_from_personal_assistant()
        mock_logger = MagicMock()
        with patch("tapps_brain.decay.logger", mock_logger):
            for tier_name in ("identity", "long-term", "short-term", "ephemeral"):
                _get_half_life(tier_name, config)

        mock_logger.warning.assert_not_called()

    def test_recall_orchestrator_receives_decay_config(self) -> None:
        """MemoryStore.recall() must pass decay_config to RecallOrchestrator so that
        profile-defined tiers are resolved correctly (fix for issue #11)."""
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from tapps_brain.profile import get_builtin_profile
        from tapps_brain.store import MemoryStore

        profile = get_builtin_profile("personal-assistant")
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MemoryStore(
                project_root=Path(tmpdir),
                profile=profile,
            )
            try:
                # Trigger lazy init of _recall_orchestrator
                with patch("tapps_brain.decay.logger") as mock_log:
                    store.recall("test query")
                    # No unknown_tier_fallback warning should be emitted
                    for call in mock_log.warning.call_args_list:
                        assert call[0][0] != "unknown_tier_fallback", (
                            f"unexpected unknown_tier_fallback warning: {call}"
                        )
                # The orchestrator should have a non-None decay config
                assert hasattr(store, "_recall_orchestrator")
                orc = store._recall_orchestrator  # type: ignore[attr-defined]
                assert orc._decay_config is not None
            finally:
                store.close()


class TestTemporalSensitivity:
    """TAP-735: per-entry decay velocity via temporal_sensitivity field."""

    _DAYS_ELAPSED = 30  # simulate 30 days of ageing

    def _aged_entry(
        self,
        *,
        tier: MemoryTier = MemoryTier.pattern,
        temporal_sensitivity: str | None = None,
    ) -> MemoryEntry:
        """Return a MemoryEntry whose updated_at is 30 days in the past."""
        past = (datetime.now(tz=UTC) - timedelta(days=self._DAYS_ELAPSED)).isoformat()
        entry = make_entry(
            tier=tier,
            confidence=0.8,
            updated_at=past,
        )
        return entry.model_copy(update={"temporal_sensitivity": temporal_sensitivity})

    def test_high_sensitivity_decays_faster_than_none(self) -> None:
        """temporal_sensitivity='high' reduces the effective half-life (×0.25).

        After 30 days an 'architectural' entry with high sensitivity should have a
        lower confidence than one with sensitivity=None.
        """
        config = DecayConfig()
        now = datetime.now(tz=UTC)
        baseline = self._aged_entry(tier=MemoryTier.architectural, temporal_sensitivity=None)
        fast = self._aged_entry(tier=MemoryTier.architectural, temporal_sensitivity="high")

        conf_baseline = calculate_decayed_confidence(baseline, config, now=now)
        conf_fast = calculate_decayed_confidence(fast, config, now=now)

        assert conf_fast < conf_baseline, (
            f"'high' sensitivity should decay faster: {conf_fast:.4f} vs {conf_baseline:.4f}"
        )

    def test_low_sensitivity_decays_slower_than_none(self) -> None:
        """temporal_sensitivity='low' increases the effective half-life (×4.0).

        After 30 days a 'context' entry with low sensitivity should have a
        higher confidence than one with sensitivity=None.
        """
        config = DecayConfig()
        now = datetime.now(tz=UTC)
        baseline = self._aged_entry(tier=MemoryTier.context, temporal_sensitivity=None)
        slow = self._aged_entry(tier=MemoryTier.context, temporal_sensitivity="low")

        conf_baseline = calculate_decayed_confidence(baseline, config, now=now)
        conf_slow = calculate_decayed_confidence(slow, config, now=now)

        assert conf_slow > conf_baseline, (
            f"'low' sensitivity should decay slower: {conf_slow:.4f} vs {conf_baseline:.4f}"
        )

    def test_none_sensitivity_unchanged(self) -> None:
        """temporal_sensitivity=None is a no-op; result equals medium (×1.0)."""
        config = DecayConfig()
        now = datetime.now(tz=UTC)
        entry_none = self._aged_entry(tier=MemoryTier.pattern, temporal_sensitivity=None)
        entry_medium = self._aged_entry(tier=MemoryTier.pattern, temporal_sensitivity="medium")

        conf_none = calculate_decayed_confidence(entry_none, config, now=now)
        conf_medium = calculate_decayed_confidence(entry_medium, config, now=now)

        assert math.isclose(conf_none, conf_medium, rel_tol=1e-9), (
            f"None and 'medium' should give identical results: {conf_none} vs {conf_medium}"
        )

    def test_unknown_temporal_sensitivity_raises_validation_error(self) -> None:
        """MemoryEntry rejects unknown temporal_sensitivity values."""
        with pytest.raises(ValidationError, match="temporal_sensitivity"):
            MemoryEntry(
                key="test-key",
                value="some test value for temporal sensitivity validation",
                temporal_sensitivity="ultra-fast",  # type: ignore[arg-type]
            )
