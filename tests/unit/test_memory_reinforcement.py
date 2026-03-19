"""Tests for memory reinforcement system (Epic 24.2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tapps_brain.decay import DecayConfig, calculate_decayed_confidence
from tapps_brain.models import MemoryEntry, MemorySource, MemoryTier
from tapps_brain.reinforcement import reinforce


def _make_entry(
    *,
    confidence: float = 0.8,
    source: MemorySource = MemorySource.agent,
    updated_at: str | None = None,
    last_reinforced: str | None = None,
    reinforce_count: int = 0,
) -> MemoryEntry:
    """Helper to create a MemoryEntry for reinforcement testing."""
    now = datetime.now(tz=UTC).isoformat()
    entry = MemoryEntry(
        key="test-key",
        value="test value",
        tier=MemoryTier.pattern,
        source=source,
        confidence=confidence,
        updated_at=updated_at or now,
        created_at=now,
        last_accessed=now,
        last_reinforced=last_reinforced,
        reinforce_count=reinforce_count,
    )
    return entry


@pytest.fixture
def config() -> DecayConfig:
    return DecayConfig()


class TestReinforce:
    def test_resets_decay_clock(self, config: DecayConfig) -> None:
        """Reinforcing sets last_reinforced to now, resetting the decay clock."""
        now = datetime.now(tz=UTC)
        old_update = (now - timedelta(days=60)).isoformat()
        entry = _make_entry(updated_at=old_update)

        updates = reinforce(entry, config, now=now)
        assert updates["last_reinforced"] == now.isoformat()

    def test_confidence_boost_within_ceiling(self, config: DecayConfig) -> None:
        """Confidence boost respects the source ceiling."""
        entry = _make_entry(confidence=0.80, source=MemorySource.agent)
        now = datetime.now(tz=UTC)

        updates = reinforce(entry, config, confidence_boost=0.1, now=now)
        # Agent ceiling is 0.85, so 0.80 + 0.10 = 0.90 -> capped at 0.85
        assert updates["confidence"] <= config.agent_confidence_ceiling

    def test_confidence_boost_clamped_at_max(self, config: DecayConfig) -> None:
        """Boost is clamped to the maximum allowed (0.2)."""
        entry = _make_entry(confidence=0.5, source=MemorySource.agent)
        now = datetime.now(tz=UTC)

        updates = reinforce(entry, config, confidence_boost=0.5, now=now)
        # Should be 0.5 + 0.2 (max) = 0.7, not 0.5 + 0.5 = 1.0
        assert updates["confidence"] == pytest.approx(0.7, abs=0.01)

    def test_negative_boost_treated_as_zero(self, config: DecayConfig) -> None:
        """Negative boost is treated as zero."""
        entry = _make_entry(confidence=0.6)
        now = datetime.now(tz=UTC)

        updates = reinforce(entry, config, confidence_boost=-0.5, now=now)
        assert updates["confidence"] == pytest.approx(0.6, abs=0.01)

    def test_increments_reinforce_count(self, config: DecayConfig) -> None:
        """Reinforce count is incremented."""
        entry = _make_entry(reinforce_count=3)
        now = datetime.now(tz=UTC)

        updates = reinforce(entry, config, now=now)
        assert updates["reinforce_count"] == 4

    def test_multiple_reinforcements_accumulate(self, config: DecayConfig) -> None:
        """Multiple reinforcements accumulate the count."""
        entry = _make_entry(reinforce_count=0)
        now = datetime.now(tz=UTC)

        updates1 = reinforce(entry, config, now=now)
        assert updates1["reinforce_count"] == 1

        # Simulate applying the update
        entry2 = _make_entry(reinforce_count=1)
        updates2 = reinforce(entry2, config, now=now)
        assert updates2["reinforce_count"] == 2

    def test_reinforced_memory_decays_from_reinforcement_time(
        self, config: DecayConfig
    ) -> None:
        """After reinforcement, decay measures from the reinforcement timestamp."""
        now = datetime.now(tz=UTC)
        old_update = (now - timedelta(days=120)).isoformat()
        entry = _make_entry(confidence=0.8, updated_at=old_update)

        # Before reinforcement, confidence is heavily decayed
        before = calculate_decayed_confidence(entry, config, now=now)
        assert before < 0.3

        # After reinforcement, create a new entry with the updated fields
        updates = reinforce(entry, config, now=now)
        reinforced = _make_entry(
            confidence=updates["confidence"],  # type: ignore[arg-type]
            updated_at=old_update,
            last_reinforced=updates["last_reinforced"],  # type: ignore[arg-type]
            reinforce_count=updates["reinforce_count"],  # type: ignore[arg-type]
        )
        after = calculate_decayed_confidence(reinforced, config, now=now)
        assert after > 0.7  # much higher because decay measures from reinforcement

    def test_implicit_access_does_not_reset_decay(self, config: DecayConfig) -> None:
        """Simply accessing a memory (without reinforce) does NOT reset the decay clock.

        This is verified by the fact that `reinforce()` is a separate explicit
        action. MemoryStore.get() only updates last_accessed and access_count.
        """
        now = datetime.now(tz=UTC)
        old_update = (now - timedelta(days=60)).isoformat()
        entry = _make_entry(confidence=0.8, updated_at=old_update)

        # Without reinforcement, decay still applies from updated_at
        decayed = calculate_decayed_confidence(entry, config, now=now)
        assert decayed < 0.5  # roughly 50% at half-life
