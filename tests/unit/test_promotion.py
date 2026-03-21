"""Tests for the promotion and demotion engine (EPIC-010)."""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest

from tapps_brain.decay import DecayConfig
from tapps_brain.models import MemoryTier
from tapps_brain.profile import (
    LayerDefinition,
    MemoryProfile,
    PromotionThreshold,
    ScoringConfig,
)
from tapps_brain.promotion import PromotionEngine
from tests.factories import make_entry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Fixed reference points for deterministic tests
_JAN_1 = datetime(2025, 1, 1, tzinfo=UTC)
_JAN_15 = datetime(2025, 1, 15, tzinfo=UTC)
_JAN_1_ISO = _JAN_1.isoformat()
_JAN_15_ISO = _JAN_15.isoformat()


def _make_profile(layers: list[LayerDefinition]) -> MemoryProfile:
    """Build a ``MemoryProfile`` with the given layers and valid scoring weights."""
    return MemoryProfile(
        name="test-profile",
        layers=layers,
        scoring=ScoringConfig(
            relevance=0.40,
            confidence=0.30,
            recency=0.15,
            frequency=0.15,
        ),
    )


# ---------------------------------------------------------------------------
# Promotion tests
# ---------------------------------------------------------------------------


class TestCheckPromotion:
    """Tests for PromotionEngine.check_promotion."""

    def test_promotion_triggers_at_threshold(self) -> None:
        """Entry exceeding all promotion criteria gets promoted."""
        layers = [
            LayerDefinition(
                name="context",
                half_life_days=14,
                promotion_to="procedural",
                promotion_threshold=PromotionThreshold(
                    min_access_count=5,
                    min_age_days=7,
                    min_confidence=0.5,
                ),
            ),
            LayerDefinition(name="procedural", half_life_days=30),
        ]
        profile = _make_profile(layers)
        # Confidence 0.8 with updated_at close to 'now' so decayed confidence
        # stays above min_confidence=0.5. Decay reference is updated_at (Jan 12),
        # only 3 days before now (Jan 15): 0.8 * 0.5^(3/14) ~ 0.69 > 0.5.
        _jan_12_iso = datetime(2025, 1, 12, tzinfo=UTC).isoformat()
        entry = make_entry(
            key="test-promo",
            tier=MemoryTier.context,
            confidence=0.8,
            access_count=6,
            created_at=_JAN_1_ISO,
            updated_at=_jan_12_iso,
            last_accessed=_jan_12_iso,
        )
        engine = PromotionEngine()
        result = engine.check_promotion(entry, profile, now=_JAN_15)
        assert result == "procedural"

    def test_no_promotion_below_access_count(self) -> None:
        """Entry below min_access_count is not promoted."""
        layers = [
            LayerDefinition(
                name="context",
                half_life_days=14,
                promotion_to="procedural",
                promotion_threshold=PromotionThreshold(
                    min_access_count=5,
                    min_age_days=7,
                    min_confidence=0.5,
                ),
            ),
            LayerDefinition(name="procedural", half_life_days=30),
        ]
        profile = _make_profile(layers)
        entry = make_entry(
            key="test-below",
            tier=MemoryTier.context,
            confidence=0.8,
            access_count=3,
            created_at=_JAN_1_ISO,
            updated_at=_JAN_1_ISO,
            last_accessed=_JAN_1_ISO,
        )
        engine = PromotionEngine()
        result = engine.check_promotion(entry, profile, now=_JAN_15)
        assert result is None

    def test_no_promotion_without_threshold(self) -> None:
        """Layer with no promotion_threshold returns None."""
        layers = [
            LayerDefinition(
                name="context",
                half_life_days=14,
                promotion_to="procedural",
                # promotion_threshold intentionally omitted
            ),
            LayerDefinition(name="procedural", half_life_days=30),
        ]
        profile = _make_profile(layers)
        entry = make_entry(
            key="test-no-thresh",
            tier=MemoryTier.context,
            confidence=0.8,
            access_count=10,
            created_at=_JAN_1_ISO,
            updated_at=_JAN_1_ISO,
            last_accessed=_JAN_1_ISO,
        )
        engine = PromotionEngine()
        result = engine.check_promotion(entry, profile, now=_JAN_15)
        assert result is None

    def test_no_promotion_at_top_tier(self) -> None:
        """Layer with promotion_to=None returns None."""
        layers = [
            LayerDefinition(
                name="architectural",
                half_life_days=180,
                # promotion_to is None by default
                promotion_threshold=PromotionThreshold(
                    min_access_count=1,
                    min_age_days=1,
                    min_confidence=0.1,
                ),
            ),
        ]
        profile = _make_profile(layers)
        entry = make_entry(
            key="test-top",
            tier=MemoryTier.architectural,
            confidence=0.9,
            access_count=20,
            created_at=_JAN_1_ISO,
            updated_at=_JAN_1_ISO,
            last_accessed=_JAN_1_ISO,
        )
        engine = PromotionEngine()
        result = engine.check_promotion(entry, profile, now=_JAN_15)
        assert result is None

    def test_no_promotion_for_non_profile(self) -> None:
        """Passing a non-MemoryProfile object returns None."""
        entry = make_entry(
            key="test-non-profile",
            tier=MemoryTier.context,
            confidence=0.8,
            access_count=10,
            created_at=_JAN_1_ISO,
            updated_at=_JAN_1_ISO,
            last_accessed=_JAN_1_ISO,
        )
        engine = PromotionEngine()
        result = engine.check_promotion(entry, {"not": "a profile"}, now=_JAN_15)
        assert result is None


# ---------------------------------------------------------------------------
# Demotion tests
# ---------------------------------------------------------------------------


class TestCheckDemotion:
    """Tests for PromotionEngine.check_demotion."""

    def test_demotion_on_stale_entry(self) -> None:
        """Entry with low effective confidence and no recent access is demoted."""
        layers = [
            LayerDefinition(
                name="pattern",
                half_life_days=60,
                confidence_floor=0.1,
                demotion_to="context",
            ),
            LayerDefinition(name="context", half_life_days=14),
        ]
        profile = _make_profile(layers)
        # Confidence low enough that decayed value will be near the floor.
        # confidence_floor * 1.5 = 0.15; we need decayed confidence <= 0.15.
        # Use confidence=0.15, updated 200 days ago so it decays well below floor.
        entry = make_entry(
            key="test-demote",
            tier=MemoryTier.pattern,
            confidence=0.15,
            access_count=1,
            created_at=_JAN_1_ISO,
            updated_at=_JAN_1_ISO,
            last_accessed=_JAN_1_ISO,
        )
        # now = Jan 1 + 200 days; days_since_access=200 > half_life_days=60
        now = datetime(2025, 7, 20, tzinfo=UTC)
        engine = PromotionEngine()
        result = engine.check_demotion(entry, profile, now=now)
        assert result == "context"

    def test_no_demotion_when_recently_accessed(self) -> None:
        """Entry accessed within half-life period is not demoted."""
        layers = [
            LayerDefinition(
                name="pattern",
                half_life_days=60,
                confidence_floor=0.1,
                demotion_to="context",
            ),
            LayerDefinition(name="context", half_life_days=14),
        ]
        profile = _make_profile(layers)
        # Low confidence but accessed very recently (now == last_accessed).
        entry = make_entry(
            key="test-recent",
            tier=MemoryTier.pattern,
            confidence=0.12,
            access_count=1,
            created_at=_JAN_1_ISO,
            updated_at=_JAN_1_ISO,
            last_accessed=_JAN_15_ISO,
        )
        engine = PromotionEngine()
        result = engine.check_demotion(entry, profile, now=_JAN_15)
        assert result is None

    def test_no_demotion_when_confidence_high(self) -> None:
        """Entry with high effective confidence is not demoted."""
        layers = [
            LayerDefinition(
                name="pattern",
                half_life_days=60,
                confidence_floor=0.1,
                demotion_to="context",
            ),
            LayerDefinition(name="context", half_life_days=14),
        ]
        profile = _make_profile(layers)
        entry = make_entry(
            key="test-high-conf",
            tier=MemoryTier.pattern,
            confidence=0.9,
            access_count=5,
            created_at=_JAN_1_ISO,
            updated_at=_JAN_1_ISO,
            last_accessed=_JAN_1_ISO,
        )
        engine = PromotionEngine()
        # Only 14 days later: confidence ~0.9 * 0.5^(14/60) ~ 0.77 >> 0.15
        result = engine.check_demotion(entry, profile, now=_JAN_15)
        assert result is None

    def test_no_demotion_at_bottom_tier(self) -> None:
        """Layer with demotion_to=None returns None."""
        layers = [
            LayerDefinition(
                name="context",
                half_life_days=14,
                # demotion_to is None by default
            ),
        ]
        profile = _make_profile(layers)
        entry = make_entry(
            key="test-bottom",
            tier=MemoryTier.context,
            confidence=0.12,
            access_count=0,
            created_at=_JAN_1_ISO,
            updated_at=_JAN_1_ISO,
            last_accessed=_JAN_1_ISO,
        )
        engine = PromotionEngine()
        now = datetime(2025, 7, 20, tzinfo=UTC)
        result = engine.check_demotion(entry, profile, now=now)
        assert result is None

    def test_no_demotion_for_non_profile(self) -> None:
        """Passing a non-MemoryProfile object returns None."""
        entry = make_entry(
            key="test-non-profile-d",
            tier=MemoryTier.pattern,
            confidence=0.12,
            access_count=0,
            created_at=_JAN_1_ISO,
            updated_at=_JAN_1_ISO,
            last_accessed=_JAN_1_ISO,
        )
        engine = PromotionEngine()
        result = engine.check_demotion(entry, "not-a-profile", now=_JAN_15)
        assert result is None


# ---------------------------------------------------------------------------
# Desirable difficulty bonus
# ---------------------------------------------------------------------------


class TestDesirableDifficultyBonus:
    """Tests for PromotionEngine.desirable_difficulty_bonus."""

    def test_low_decayed_confidence_gets_bigger_boost(self) -> None:
        """Nearly-forgotten memory (decayed ~0.2) gets larger boost than fresh memory."""
        config = DecayConfig(context_half_life_days=14, confidence_floor=0.0)
        base_boost = 0.1

        # Entry with confidence=0.2, updated at now so decayed confidence == 0.2
        entry_low = make_entry(
            key="test-low-decay",
            tier=MemoryTier.context,
            confidence=0.2,
            updated_at=_JAN_15_ISO,
            last_accessed=_JAN_15_ISO,
        )
        boost_low = PromotionEngine.desirable_difficulty_bonus(
            entry_low, base_boost, config, now=_JAN_15
        )
        # decayed = 0.2 (just updated), bonus = 0.1 * (1 + (1.0 - 0.2)) = 0.1 * 1.8 = 0.18
        assert boost_low == pytest.approx(0.18, abs=0.01)

        # Entry with confidence=0.8, updated at now so decayed confidence == 0.8
        entry_high = make_entry(
            key="test-high-decay",
            tier=MemoryTier.context,
            confidence=0.8,
            updated_at=_JAN_15_ISO,
            last_accessed=_JAN_15_ISO,
        )
        boost_high = PromotionEngine.desirable_difficulty_bonus(
            entry_high, base_boost, config, now=_JAN_15
        )
        # decayed = 0.8, bonus = 0.1 * (1 + 0.2) = 0.12
        assert boost_high == pytest.approx(0.12, abs=0.01)

        # Low-confidence entry gets a bigger boost
        assert boost_low > boost_high


# ---------------------------------------------------------------------------
# Effective half-life
# ---------------------------------------------------------------------------


class TestEffectiveHalfLife:
    """Tests for PromotionEngine.effective_half_life."""

    def test_zero_reinforcements_returns_base(self) -> None:
        """With reinforce_count=0, effective half-life equals base."""
        base = 60
        result = PromotionEngine.effective_half_life(base, 0)
        # log1p(0) == 0, so multiplier == 1.0
        assert result == pytest.approx(60.0)

    def test_ten_reinforcements_growth(self) -> None:
        """With reinforce_count=10, effective half-life is ~base * 1.72."""
        base = 60
        result = PromotionEngine.effective_half_life(base, 10)
        # 1.0 + log1p(10) * 0.3 = 1.0 + ln(11) * 0.3 ~ 1.0 + 2.3979 * 0.3 ~ 1.7194
        expected = base * (1.0 + math.log1p(10) * 0.3)
        assert result == pytest.approx(expected)
        assert result == pytest.approx(base * 1.72, abs=0.5)

    def test_half_life_grows_monotonically(self) -> None:
        """More reinforcements always produce longer half-lives."""
        base = 30
        prev = PromotionEngine.effective_half_life(base, 0)
        for count in [1, 3, 5, 10, 50, 100]:
            current = PromotionEngine.effective_half_life(base, count)
            assert current > prev, f"half-life did not grow at reinforce_count={count}"
            prev = current
