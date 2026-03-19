"""Unit tests for memory persistence layer (Epic 23, Story 2)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tapps_brain.models import (
    MemoryEntry,
    MemoryScope,
    MemorySource,
    MemoryTier,
)
from tapps_brain.persistence import MemoryPersistence

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture()
def persistence(tmp_path: Path) -> MemoryPersistence:
    """Create a MemoryPersistence instance backed by a temp directory."""
    return MemoryPersistence(tmp_path)


@pytest.fixture()
def sample_entry() -> MemoryEntry:
    """Create a sample MemoryEntry for testing."""
    return MemoryEntry(
        key="test-key",
        value="Test value for persistence",
        tier=MemoryTier.pattern,
        source=MemorySource.agent,
        tags=["python", "testing"],
    )


class TestMemoryPersistence:
    """Tests for MemoryPersistence."""

    def test_save_and_get_roundtrip(
        self, persistence: MemoryPersistence, sample_entry: MemoryEntry
    ) -> None:
        persistence.save(sample_entry)
        loaded = persistence.get("test-key")
        assert loaded is not None
        assert loaded.key == "test-key"
        assert loaded.value == "Test value for persistence"
        assert loaded.tier == MemoryTier.pattern
        assert loaded.tags == ["python", "testing"]

    def test_get_nonexistent_returns_none(self, persistence: MemoryPersistence) -> None:
        assert persistence.get("nonexistent") is None

    def test_save_replaces_existing(self, persistence: MemoryPersistence) -> None:
        entry1 = MemoryEntry(key="k1", value="original")
        persistence.save(entry1)

        entry2 = MemoryEntry(key="k1", value="updated")
        persistence.save(entry2)

        loaded = persistence.get("k1")
        assert loaded is not None
        assert loaded.value == "updated"
        assert persistence.count() == 1

    def test_delete(self, persistence: MemoryPersistence, sample_entry: MemoryEntry) -> None:
        persistence.save(sample_entry)
        assert persistence.delete("test-key") is True
        assert persistence.get("test-key") is None
        assert persistence.count() == 0

    def test_delete_nonexistent_returns_false(self, persistence: MemoryPersistence) -> None:
        assert persistence.delete("nonexistent") is False

    def test_list_all_no_filters(self, persistence: MemoryPersistence) -> None:
        persistence.save(MemoryEntry(key="k1", value="v1"))
        persistence.save(MemoryEntry(key="k2", value="v2"))
        entries = persistence.list_all()
        assert len(entries) == 2

    def test_list_all_filter_by_tier(self, persistence: MemoryPersistence) -> None:
        persistence.save(MemoryEntry(key="a1", value="v", tier=MemoryTier.architectural))
        persistence.save(MemoryEntry(key="p1", value="v", tier=MemoryTier.pattern))
        entries = persistence.list_all(tier="architectural")
        assert len(entries) == 1
        assert entries[0].key == "a1"

    def test_list_all_filter_by_scope(self, persistence: MemoryPersistence) -> None:
        persistence.save(MemoryEntry(key="proj1", value="v", scope=MemoryScope.project))
        persistence.save(
            MemoryEntry(
                key="br1",
                value="v",
                scope=MemoryScope.branch,
                branch="main",
            )
        )
        entries = persistence.list_all(scope="project")
        assert len(entries) == 1
        assert entries[0].key == "proj1"

    def test_list_all_filter_by_tags(self, persistence: MemoryPersistence) -> None:
        persistence.save(MemoryEntry(key="k1", value="v", tags=["python", "testing"]))
        persistence.save(MemoryEntry(key="k2", value="v", tags=["rust"]))
        entries = persistence.list_all(tags=["python"])
        assert len(entries) == 1
        assert entries[0].key == "k1"

    def test_search_fts5(self, persistence: MemoryPersistence) -> None:
        persistence.save(MemoryEntry(key="architecture-decision", value="Use SQLite for storage"))
        persistence.save(MemoryEntry(key="coding-pattern", value="Always use type hints"))
        results = persistence.search("SQLite")
        assert len(results) >= 1
        assert any(r.key == "architecture-decision" for r in results)

    def test_search_empty_query_returns_empty(self, persistence: MemoryPersistence) -> None:
        persistence.save(MemoryEntry(key="k1", value="v1"))
        assert persistence.search("") == []
        assert persistence.search("   ") == []

    def test_load_all(self, persistence: MemoryPersistence) -> None:
        persistence.save(MemoryEntry(key="k1", value="v1"))
        persistence.save(MemoryEntry(key="k2", value="v2"))
        all_entries = persistence.load_all()
        assert len(all_entries) == 2

    def test_count(self, persistence: MemoryPersistence) -> None:
        assert persistence.count() == 0
        persistence.save(MemoryEntry(key="k1", value="v1"))
        assert persistence.count() == 1
        persistence.save(MemoryEntry(key="k2", value="v2"))
        assert persistence.count() == 2

    def test_schema_version(self, persistence: MemoryPersistence) -> None:
        assert persistence.get_schema_version() == 4  # Epic 65.12: relations table

    def test_wal_mode_enabled(self, tmp_path: Path) -> None:
        p = MemoryPersistence(tmp_path)
        row = p._conn.execute("PRAGMA journal_mode").fetchone()
        assert row[0] == "wal"
        p.close()

    def test_audit_log_created(
        self, persistence: MemoryPersistence, sample_entry: MemoryEntry
    ) -> None:
        persistence.save(sample_entry)
        audit_path = persistence._audit_path
        assert audit_path.exists()
        lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) >= 1
        import json

        record = json.loads(lines[0])
        assert record["action"] == "save"
        assert record["key"] == "test-key"

    def test_audit_log_records_delete(
        self, persistence: MemoryPersistence, sample_entry: MemoryEntry
    ) -> None:
        persistence.save(sample_entry)
        persistence.delete("test-key")
        import json

        lines = persistence._audit_path.read_text(encoding="utf-8").strip().splitlines()
        actions = [json.loads(line)["action"] for line in lines]
        assert "save" in actions
        assert "delete" in actions

    def test_persists_across_instances(self, tmp_path: Path) -> None:
        p1 = MemoryPersistence(tmp_path)
        p1.save(MemoryEntry(key="survive", value="across restart"))
        p1.close()

        p2 = MemoryPersistence(tmp_path)
        loaded = p2.get("survive")
        assert loaded is not None
        assert loaded.value == "across restart"
        p2.close()

    def test_confidence_and_source_preserved(self, persistence: MemoryPersistence) -> None:
        entry = MemoryEntry(
            key="conf-test",
            value="v",
            source=MemorySource.human,
            confidence=0.85,
        )
        persistence.save(entry)
        loaded = persistence.get("conf-test")
        assert loaded is not None
        assert loaded.confidence == 0.85
        assert loaded.source == MemorySource.human

    def test_branch_scope_preserved(self, persistence: MemoryPersistence) -> None:
        entry = MemoryEntry(
            key="branch-test",
            value="v",
            scope=MemoryScope.branch,
            branch="feature-x",
        )
        persistence.save(entry)
        loaded = persistence.get("branch-test")
        assert loaded is not None
        assert loaded.scope == MemoryScope.branch
        assert loaded.branch == "feature-x"
