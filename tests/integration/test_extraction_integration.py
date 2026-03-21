"""Integration tests for extraction ingestion with real MemoryStore + SQLite.

Uses real MemoryStore (no mocks), real SQLite/FTS5, and real rule-based
extraction. All databases use tmp_path for isolation.

Story: STORY-002.3 from EPIC-002
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
    yield s  # type: ignore[misc]
    s.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestIngestContextIntegration:
    """Integration tests for MemoryStore.ingest_context."""

    def test_single_decision_creates_architectural_entry(self, store: MemoryStore) -> None:
        """Ingest text with 'we decided' → entry created with architectural tier."""
        text = "After much discussion, we decided to use PostgreSQL for the primary database."
        keys = store.ingest_context(text, source="agent")

        assert len(keys) == 1
        entry = store.get(keys[0])
        assert entry is not None
        assert str(entry.tier) == "architectural"
        assert "PostgreSQL" in entry.value

    def test_multiple_decision_patterns_create_multiple_entries(self, store: MemoryStore) -> None:
        """Ingest text with multiple decision patterns → multiple entries created."""
        text = (
            "We decided to use PostgreSQL for the primary database. "
            "We agreed on a REST-first API design. "
            "Going forward, all services will use structured logging."
        )
        keys = store.ingest_context(text, source="agent")

        assert len(keys) == 3

        # Verify each entry exists and has the expected tier
        tiers = set()
        for key in keys:
            entry = store.get(key)
            assert entry is not None
            tiers.add(entry.tier.value)

        assert "architectural" in tiers  # "we decided"
        assert "pattern" in tiers  # "we agreed"
        assert "context" in tiers  # "going forward"

    def test_duplicate_ingest_returns_empty(self, store: MemoryStore) -> None:
        """Ingest same text twice → no duplicates, second call returns empty list."""
        text = "We decided to use PostgreSQL for the primary database."
        keys_first = store.ingest_context(text, source="agent")
        assert len(keys_first) == 1

        keys_second = store.ingest_context(text, source="agent")
        assert keys_second == []

    def test_no_decision_patterns_returns_empty(self, store: MemoryStore) -> None:
        """Ingest text with no decision patterns → empty list returned."""
        text = "The weather was nice today. We went for a walk in the park."
        keys = store.ingest_context(text, source="agent")

        assert keys == []

    def test_persistence_across_store_reopen(self, tmp_path: Path) -> None:
        """Ingest, close store, reopen → entries are persisted in SQLite."""
        text = "We decided to use PostgreSQL for the primary database."

        # First store session: ingest and close
        store1 = MemoryStore(tmp_path)
        keys = store1.ingest_context(text, source="agent")
        assert len(keys) == 1
        key = keys[0]
        store1.close()

        # Second store session: reopen and verify
        store2 = MemoryStore(tmp_path)
        entry = store2.get(key)
        assert entry is not None
        assert str(entry.tier) == "architectural"
        assert "PostgreSQL" in entry.value
        store2.close()
