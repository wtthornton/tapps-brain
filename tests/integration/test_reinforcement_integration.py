"""Integration tests for MemoryStore.reinforce() with real SQLite.

Uses real MemoryStore (no mocks), real SQLite/FTS5, and real
reinforcement logic. All databases use tmp_path for isolation.

Story: STORY-002.2 from EPIC-002
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tapps_brain.store import MemoryStore

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path: Path) -> MemoryStore:
    """Create a real MemoryStore backed by SQLite in a temp directory."""
    s = MemoryStore(tmp_path)
    yield s
    s.close()


def _save(
    store: MemoryStore,
    key: str,
    value: str,
    *,
    tier: str = "pattern",
    tags: list[str] | None = None,
    confidence: float = -1.0,
    source: str = "agent",
) -> None:
    """Helper to save a memory entry with sensible defaults."""
    result = store.save(
        key=key,
        value=value,
        tier=tier,
        tags=tags or [],
        confidence=confidence,
        source=source,
    )
    assert not isinstance(result, dict), f"save failed: {result}"


# ---------------------------------------------------------------------------
# Reinforcement integration tests
# ---------------------------------------------------------------------------


class TestReinforceBasic:
    """Verify reinforce() updates entries correctly via real MemoryStore."""

    def test_reinforce_sets_last_reinforced_and_increments_count(self, store: MemoryStore) -> None:
        """Reinforce an entry with no boost: last_reinforced set, count=1, confidence unchanged."""
        _save(store, "test-entry", "Some useful pattern", confidence=0.5)
        original = store.get("test-entry")
        assert original is not None
        assert original.reinforce_count == 0
        assert original.last_reinforced is None

        updated = store.reinforce("test-entry")

        assert updated.last_reinforced is not None
        assert updated.reinforce_count == 1
        assert updated.confidence == original.confidence

    def test_reinforce_with_confidence_boost(self, store: MemoryStore) -> None:
        """Reinforce with confidence_boost=0.1 increases confidence."""
        _save(store, "boost-entry", "Pattern worth boosting", confidence=0.5)

        updated = store.reinforce("boost-entry", confidence_boost=0.1)

        assert updated.confidence == pytest.approx(0.6, abs=1e-9)
        assert updated.reinforce_count == 1
        assert updated.last_reinforced is not None

    def test_reinforce_confidence_capped_at_agent_ceiling(self, store: MemoryStore) -> None:
        """Confidence boost cannot exceed agent ceiling (0.85)."""
        _save(store, "high-conf", "Already high confidence", confidence=0.82)

        updated = store.reinforce("high-conf", confidence_boost=0.1)

        # 0.82 + 0.1 = 0.92, but agent ceiling is 0.85
        assert updated.confidence == pytest.approx(0.85, abs=1e-9)

    def test_reinforce_nonexistent_key_raises_key_error(self, store: MemoryStore) -> None:
        """Reinforcing a non-existent key raises KeyError."""
        with pytest.raises(KeyError):
            store.reinforce("does-not-exist")

    def test_reinforce_twice_increments_count_to_two(self, store: MemoryStore) -> None:
        """Two reinforcements yield reinforce_count=2."""
        _save(store, "double-reinforce", "Entry reinforced twice", confidence=0.4)

        store.reinforce("double-reinforce")
        updated = store.reinforce("double-reinforce")

        assert updated.reinforce_count == 2
        assert updated.last_reinforced is not None


class TestReinforcePersistence:
    """Verify reinforcement data survives store close/reopen."""

    def test_reinforced_values_persist_across_reopen(self, tmp_path: Path) -> None:
        """Reinforce, close, reopen from same path: values are persisted."""
        s1 = MemoryStore(tmp_path)
        _save(s1, "persist-entry", "Persistence check", confidence=0.5)
        s1.reinforce("persist-entry", confidence_boost=0.1)
        s1.close()

        s2 = MemoryStore(tmp_path)
        try:
            reloaded = s2.get("persist-entry")
            assert reloaded is not None
            assert reloaded.reinforce_count == 1
            assert reloaded.last_reinforced is not None
            assert reloaded.confidence == pytest.approx(0.6, abs=1e-9)
        finally:
            s2.close()
