"""Tests verifying repo-brain.yaml promotion thresholds (BUG-002-F).

The `repo-brain` profile has `min_access_count` thresholds that were reduced
in a prior change (pattern: 8→5, procedural: 5→3, expressed as procedural→pattern
and context→procedural promotion thresholds).  These tests pin the current values
and ensure entries are promoted only when ALL criteria are met — preventing
premature promotion.

Current thresholds in repo-brain.yaml:
  - context   → procedural:   min_access_count=3,  min_age_days=7,  min_confidence=0.5
  - procedural → pattern:      min_access_count=5,  min_age_days=14, min_confidence=0.6
  - pattern   → architectural: min_access_count=10, min_age_days=30, min_confidence=0.7
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tapps_brain.decay import decay_config_from_profile
from tapps_brain.profile import get_builtin_profile
from tapps_brain.promotion import PromotionEngine
from tests.factories import make_entry

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_NOW = datetime(2025, 6, 1, tzinfo=UTC)


@pytest.fixture(scope="module")
def repo_brain():
    """Load the built-in repo-brain profile once for all tests in this module."""
    return get_builtin_profile("repo-brain")


@pytest.fixture(scope="module")
def engine(repo_brain):
    """PromotionEngine configured with repo-brain decay settings."""
    config = decay_config_from_profile(repo_brain)
    return PromotionEngine(config)


# ---------------------------------------------------------------------------
# 1. Profile threshold values — regression guards
# ---------------------------------------------------------------------------


class TestRepoBrainThresholdValues:
    """Pin the exact min_access_count values from repo-brain.yaml.

    If someone changes the YAML these tests will fail loudly, prompting
    a deliberate review of downstream effects.
    """

    def test_context_promotion_threshold(self, repo_brain) -> None:
        layer = repo_brain.get_layer("context")
        assert layer is not None
        assert layer.promotion_to == "procedural"
        assert layer.promotion_threshold is not None
        assert layer.promotion_threshold.min_access_count == 3
        assert layer.promotion_threshold.min_age_days == 7
        assert layer.promotion_threshold.min_confidence == pytest.approx(0.5)

    def test_procedural_promotion_threshold(self, repo_brain) -> None:
        layer = repo_brain.get_layer("procedural")
        assert layer is not None
        assert layer.promotion_to == "pattern"
        assert layer.promotion_threshold is not None
        assert layer.promotion_threshold.min_access_count == 5
        assert layer.promotion_threshold.min_age_days == 14
        assert layer.promotion_threshold.min_confidence == pytest.approx(0.6)

    def test_pattern_promotion_threshold(self, repo_brain) -> None:
        layer = repo_brain.get_layer("pattern")
        assert layer is not None
        assert layer.promotion_to == "architectural"
        assert layer.promotion_threshold is not None
        assert layer.promotion_threshold.min_access_count == 10
        assert layer.promotion_threshold.min_age_days == 30
        assert layer.promotion_threshold.min_confidence == pytest.approx(0.7)

    def test_architectural_has_no_promotion(self, repo_brain) -> None:
        layer = repo_brain.get_layer("architectural")
        assert layer is not None
        assert layer.promotion_to is None


# ---------------------------------------------------------------------------
# 2. Context → Procedural boundary tests (min_access_count = 3)
# ---------------------------------------------------------------------------


class TestContextToProceduralBoundary:
    """Entries in the context tier need access_count >= 3 (plus age/confidence)."""

    def _context_entry(self, *, access_count: int, days_old: int = 10) -> object:
        created = (_NOW - timedelta(days=days_old)).isoformat()
        return make_entry(
            key="ctx-entry",
            value="Always run ruff before committing",
            tier="context",
            confidence=0.9,
            source="human",
            created_at=created,
            updated_at=created,
            last_accessed=created,
            access_count=access_count,
        )

    def test_below_threshold_not_promoted(self, engine, repo_brain) -> None:
        """access_count=2 (< 3) must NOT promote."""
        entry = self._context_entry(access_count=2)
        result = engine.check_promotion(entry, repo_brain, now=_NOW)
        assert result is None, f"Expected no promotion at access_count=2, got '{result}'"

    def test_at_threshold_promoted(self, engine, repo_brain) -> None:
        """access_count=3 (== 3) SHOULD promote to procedural."""
        entry = self._context_entry(access_count=3)
        result = engine.check_promotion(entry, repo_brain, now=_NOW)
        assert result == "procedural", (
            f"Expected promotion to 'procedural' at access_count=3, got '{result}'"
        )

    def test_above_threshold_promoted(self, engine, repo_brain) -> None:
        """access_count=10 (> 3) SHOULD also promote."""
        entry = self._context_entry(access_count=10)
        result = engine.check_promotion(entry, repo_brain, now=_NOW)
        assert result == "procedural"

    def test_age_gate_prevents_premature_promotion(self, engine, repo_brain) -> None:
        """access_count=3 but only 3 days old (< min_age_days=7) → no promotion."""
        entry = self._context_entry(access_count=3, days_old=3)
        result = engine.check_promotion(entry, repo_brain, now=_NOW)
        assert result is None, f"Entry too young (3d < 7d min) should not promote, got '{result}'"

    def test_confidence_gate_prevents_premature_promotion(self, engine, repo_brain) -> None:
        """access_count=3 and old enough, but confidence=0.3 (< min_confidence=0.5)."""
        created = (_NOW - timedelta(days=10)).isoformat()
        entry = make_entry(
            key="ctx-low-conf",
            value="Low confidence context entry",
            tier="context",
            confidence=0.3,
            source="human",
            created_at=created,
            updated_at=created,
            last_accessed=created,
            access_count=3,
        )
        result = engine.check_promotion(entry, repo_brain, now=_NOW)
        assert result is None, f"Low confidence (0.3 < 0.5 min) should not promote, got '{result}'"


# ---------------------------------------------------------------------------
# 3. Procedural → Pattern boundary tests (min_access_count = 5)
# ---------------------------------------------------------------------------


class TestProceduralToPatternBoundary:
    """Entries in the procedural tier need access_count >= 5."""

    def _procedural_entry(self, *, access_count: int, days_old: int = 20) -> object:
        created = (_NOW - timedelta(days=days_old)).isoformat()
        # Use a recent updated_at so confidence hasn't decayed below min_confidence=0.6
        # (procedural half_life=30d; updating 1 day ago keeps effective conf ≈ 0.83)
        recent = (_NOW - timedelta(days=1)).isoformat()
        return make_entry(
            key="proc-entry",
            value="Run `uv sync --extra dev` to set up the environment",
            tier="procedural",
            confidence=0.85,
            source="human",
            created_at=created,
            updated_at=recent,
            last_accessed=recent,
            access_count=access_count,
        )

    def test_below_threshold_not_promoted(self, engine, repo_brain) -> None:
        """access_count=4 (< 5) must NOT promote."""
        entry = self._procedural_entry(access_count=4)
        result = engine.check_promotion(entry, repo_brain, now=_NOW)
        assert result is None, f"Expected no promotion at access_count=4, got '{result}'"

    def test_at_threshold_promoted(self, engine, repo_brain) -> None:
        """access_count=5 (== 5) SHOULD promote to pattern."""
        entry = self._procedural_entry(access_count=5)
        result = engine.check_promotion(entry, repo_brain, now=_NOW)
        assert result == "pattern", (
            f"Expected promotion to 'pattern' at access_count=5, got '{result}'"
        )

    def test_age_gate_prevents_premature_promotion(self, engine, repo_brain) -> None:
        """access_count=5 but only 10 days old (< min_age_days=14) → no promotion."""
        entry = self._procedural_entry(access_count=5, days_old=10)
        result = engine.check_promotion(entry, repo_brain, now=_NOW)
        assert result is None, f"Entry too young (10d < 14d min) should not promote, got '{result}'"


# ---------------------------------------------------------------------------
# 4. Pattern → Architectural boundary tests (min_access_count = 10)
# ---------------------------------------------------------------------------


class TestPatternToArchitecturalBoundary:
    """Entries in the pattern tier need access_count >= 10."""

    def _pattern_entry(self, *, access_count: int, days_old: int = 35) -> object:
        created = (_NOW - timedelta(days=days_old)).isoformat()
        # Use a recent updated_at so confidence hasn't decayed below min_confidence=0.7
        # (pattern half_life=60d; updating 1 day ago keeps effective conf ≈ 0.84)
        recent = (_NOW - timedelta(days=1)).isoformat()
        return make_entry(
            key="pat-entry",
            value="All API endpoints follow RESTful resource naming conventions",
            tier="pattern",
            confidence=0.85,
            source="human",
            created_at=created,
            updated_at=recent,
            last_accessed=recent,
            access_count=access_count,
        )

    def test_below_threshold_not_promoted(self, engine, repo_brain) -> None:
        """access_count=9 (< 10) must NOT promote."""
        entry = self._pattern_entry(access_count=9)
        result = engine.check_promotion(entry, repo_brain, now=_NOW)
        assert result is None, f"Expected no promotion at access_count=9, got '{result}'"

    def test_at_threshold_promoted(self, engine, repo_brain) -> None:
        """access_count=10 (== 10) SHOULD promote to architectural."""
        entry = self._pattern_entry(access_count=10)
        result = engine.check_promotion(entry, repo_brain, now=_NOW)
        assert result == "architectural", (
            f"Expected promotion to 'architectural' at access_count=10, got '{result}'"
        )

    def test_age_gate_prevents_premature_promotion(self, engine, repo_brain) -> None:
        """access_count=10 but only 25 days old (< min_age_days=30) → no promotion."""
        entry = self._pattern_entry(access_count=10, days_old=25)
        result = engine.check_promotion(entry, repo_brain, now=_NOW)
        assert result is None, f"Entry too young (25d < 30d min) should not promote, got '{result}'"

    def test_confidence_gate_prevents_premature_promotion(self, engine, repo_brain) -> None:
        """access_count=10, old enough, but confidence=0.6 (< min_confidence=0.7)."""
        created = (_NOW - timedelta(days=35)).isoformat()
        entry = make_entry(
            key="pat-low-conf",
            value="Low confidence pattern entry",
            tier="pattern",
            confidence=0.6,
            source="human",
            created_at=created,
            updated_at=created,
            last_accessed=created,
            access_count=10,
        )
        result = engine.check_promotion(entry, repo_brain, now=_NOW)
        assert result is None, f"Low confidence (0.6 < 0.7 min) should not promote, got '{result}'"
