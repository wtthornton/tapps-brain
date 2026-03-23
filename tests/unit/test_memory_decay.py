"""Tests for memory decay engine (Epic 24.1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from tapps_brain.decay import (
    DecayConfig,
    _days_since,
    calculate_decayed_confidence,
    decay_config_from_profile,
    get_effective_confidence,
    is_stale,
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
) -> MemoryEntry:
    """Helper to create a MemoryEntry with controlled timestamps."""
    return make_entry(
        tier=tier,
        source=source,
        confidence=confidence,
        updated_at=updated_at,
        last_reinforced=last_reinforced,
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

    def test_legacy_fields_match_profile_layers(self) -> None:
        """Values from profile layers are propagated to legacy fields."""
        profile = self._make_profile()
        config = decay_config_from_profile(profile)
        assert config.architectural_half_life_days == 200
        assert config.pattern_half_life_days == 70
        assert config.procedural_half_life_days == 45
        assert config.context_half_life_days == 10

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


class TestUnknownTierFallbackWarning:
    """Tests for unknown tier fallback warning in _get_half_life (BUG-001-D)."""

    def test_unknown_tier_returns_context_half_life(self) -> None:
        """Unknown tier string falls back to context_half_life_days."""
        from tapps_brain.decay import _get_half_life

        config = DecayConfig()
        result = _get_half_life("totally_unknown_tier", config)
        assert result == config.context_half_life_days

    def test_unknown_tier_logs_warning(self) -> None:
        """Unknown tier string triggers a structlog warning with tier and fallback_days."""
        from unittest.mock import MagicMock, patch

        from tapps_brain.decay import _get_half_life

        config = DecayConfig()
        mock_logger = MagicMock()
        with patch("tapps_brain.decay.logger", mock_logger):
            result = _get_half_life("totally_unknown_tier", config)

        assert result == config.context_half_life_days
        mock_logger.warning.assert_called_once_with(
            "unknown_tier_fallback",
            tier="totally_unknown_tier",
            fallback_days=config.context_half_life_days,
        )

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


class TestUnknownSourceFallbackWarning:
    """Tests for unknown source fallback warning in _get_ceiling (review 019-A)."""

    def test_unknown_source_returns_agent_ceiling(self) -> None:
        """Unknown source string falls back to agent_confidence_ceiling."""
        from tapps_brain.decay import _get_ceiling

        config = DecayConfig()
        result = _get_ceiling("totally_unknown_source", config)
        assert result == config.agent_confidence_ceiling

    def test_unknown_source_logs_warning(self) -> None:
        """Unknown source string triggers a structlog warning with source and fallback_ceiling."""
        from unittest.mock import MagicMock, patch

        from tapps_brain.decay import _get_ceiling

        config = DecayConfig()
        mock_logger = MagicMock()
        with patch("tapps_brain.decay.logger", mock_logger):
            result = _get_ceiling("totally_unknown_source", config)

        assert result == config.agent_confidence_ceiling
        mock_logger.warning.assert_called_once_with(
            "unknown_source_fallback",
            source="totally_unknown_source",
            fallback_ceiling=config.agent_confidence_ceiling,
        )

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
