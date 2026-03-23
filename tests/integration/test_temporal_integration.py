"""Integration tests for bi-temporal fact versioning (EPIC-004, STORY-004.7).

All tests use real MemoryStore + SQLite (no mocks).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pytest

from tapps_brain.retrieval import MemoryRetriever
from tapps_brain.store import ConsolidationConfig, MemoryStore

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture()
def store(tmp_path: Path) -> MemoryStore:
    s = MemoryStore(tmp_path)
    yield s
    s.close()


class TestSupersedeLifecycle:
    """Create -> supersede -> supersede, verify search and history."""

    def test_full_version_chain(self, store: MemoryStore) -> None:
        """Create v1, supersede with v2, supersede with v3.

        - Search returns only v3.
        - Search with as_of between v1 and v2 returns only v1.
        - history() returns [v1, v2, v3].
        """
        store.save(key="pricing-v1", value="Our pricing is 297 dollars monthly plan")
        time.sleep(0.01)

        v1 = store.get("pricing-v1")
        assert v1 is not None
        v1_time = v1.created_at

        v2 = store.supersede(
            "pricing-v1", "Our pricing is 347 dollars monthly plan", key="pricing-v2"
        )
        time.sleep(0.01)

        store.supersede(v2.key, "Our pricing is 397 dollars monthly plan", key="pricing-v3")

        # Default search returns only v3
        results = store.search("pricing dollars monthly plan")
        keys = [r.key for r in results]
        assert "pricing-v1" not in keys
        assert "pricing-v2" not in keys
        assert "pricing-v3" in keys

        # as_of v1 time returns v1
        results_v1 = store.search("pricing dollars monthly plan", as_of=v1_time)
        keys_v1 = [r.key for r in results_v1]
        assert "pricing-v1" in keys_v1

        # history returns full chain
        chain = store.history("pricing-v1")
        assert len(chain) == 3
        assert [e.key for e in chain] == ["pricing-v1", "pricing-v2", "pricing-v3"]


class TestFutureValidity:
    """Entries with valid_at in the future."""

    def test_future_entry_not_in_search(self, store: MemoryStore) -> None:
        """An entry with valid_at in the future should not appear in search now."""
        future = "2099-01-01T00:00:00+00:00"
        store.save(key="future-fact", value="Future release details coming soon")

        # Set valid_at to the future via update_fields
        store.update_fields("future-fact", valid_at=future)

        results = store.search("Future release details")
        keys = [r.key for r in results]
        assert "future-fact" not in keys

        # But searching as_of that future time should find it
        results_future = store.search("Future release details", as_of=future)
        keys_future = [r.key for r in results_future]
        assert "future-fact" in keys_future


class TestExpiredEntry:
    """Entries with invalid_at in the past."""

    def test_expired_excluded_from_search(self, store: MemoryStore) -> None:
        """An entry with invalid_at in the past should not appear in default search."""
        past = "2020-01-01T00:00:00+00:00"
        store.save(key="expired-fact", value="Release freeze is active now")
        store.update_fields("expired-fact", invalid_at=past)

        results = store.search("Release freeze active")
        keys = [r.key for r in results]
        assert "expired-fact" not in keys

    def test_expired_included_with_include_superseded(self, store: MemoryStore) -> None:
        """Expired entries can be retrieved with include_superseded=True."""
        past = "2020-01-01T00:00:00+00:00"
        store.save(key="old-freeze", value="Release freeze was active in January")
        store.update_fields("old-freeze", invalid_at=past)

        retriever = MemoryRetriever()
        results = retriever.search("Release freeze January", store, include_superseded=True)
        keys = [sm.entry.key for sm in results]
        assert "old-freeze" in keys

        # Verify it's marked stale
        match = next(sm for sm in results if sm.entry.key == "old-freeze")
        assert match.stale is True


class TestConsolidationTemporal:
    """Auto-consolidation produces temporal chains."""

    def test_consolidation_sets_temporal_fields(self, tmp_path: Path) -> None:
        """When entries are consolidated, sources get invalid_at and superseded_by."""
        store = MemoryStore(
            tmp_path,
            consolidation_config=ConsolidationConfig(
                enabled=True,
                threshold=0.3,
                min_entries=2,
            ),
        )

        # Save very similar entries to trigger consolidation
        store.save(
            key="react-pattern-1",
            value="We use React with TypeScript for all frontend components",
            tier="pattern",
            tags=["frontend", "react"],
        )
        store.save(
            key="react-pattern-2",
            value="We use React with TypeScript for frontend UI components",
            tier="pattern",
            tags=["frontend", "react"],
        )
        # Third entry should trigger consolidation
        store.save(
            key="react-pattern-3",
            value="We use React with TypeScript for building frontend components",
            tier="pattern",
            tags=["frontend", "react"],
        )

        # Check that source entries were temporally invalidated
        all_entries = store.list_all(include_superseded=True)
        invalidated = [e for e in all_entries if e.invalid_at is not None]

        # Consolidation must trigger given low threshold (0.3) and 3 near-identical entries
        assert len(invalidated) > 0, "Expected consolidation to invalidate at least one entry"
        for entry in invalidated:
            assert entry.superseded_by is not None

        store.close()


class TestPersistenceRoundTrip:
    """Temporal fields survive a cold restart."""

    def test_supersede_persists_across_restart(self, tmp_path: Path) -> None:
        s1 = MemoryStore(tmp_path)
        s1.save(key="db-version", value="PostgreSQL 15 database server")
        s1.supersede("db-version", "PostgreSQL 17 database server", key="db-version-v2")
        s1.close()

        s2 = MemoryStore(tmp_path)

        old = s2.get("db-version")
        assert old is not None
        assert old.invalid_at is not None
        assert old.superseded_by == "db-version-v2"

        reloaded = s2.get("db-version-v2")
        assert reloaded is not None
        assert reloaded.valid_at is not None
        assert reloaded.value == "PostgreSQL 17 database server"

        chain = s2.history("db-version")
        assert len(chain) == 2
        s2.close()


class TestRecallExcludesSuperseded:
    """Recall orchestrator excludes superseded entries."""

    def test_recall_filters_superseded(self, store: MemoryStore) -> None:
        """store.recall() should not return superseded entries."""
        store.save(key="lang-old", value="We use Python 3.11 programming language")
        store.supersede("lang-old", "We use Python 3.12 programming language", key="lang-new")

        result = store.recall("Python programming language")
        if result.memories:
            memory_keys = [m.get("key", "") for m in result.memories]
            assert "lang-old" not in memory_keys
