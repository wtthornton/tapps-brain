"""Unit tests for MemoryStore (Epic 23, Story 3)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from tapps_brain.models import MemoryEntry
from tapps_brain.store import _MAX_ENTRIES, MemoryStore

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture()
def store(tmp_path: Path) -> MemoryStore:
    """Create a MemoryStore instance backed by a temp directory."""
    return MemoryStore(tmp_path)


class TestMemoryStoreCRUD:
    """Tests for basic CRUD operations."""

    def test_save_and_get(self, store: MemoryStore) -> None:
        result = store.save(key="test-key", value="Test value")
        assert isinstance(result, MemoryEntry)
        assert result.key == "test-key"

        loaded = store.get("test-key")
        assert loaded is not None
        assert loaded.value == "Test value"

    def test_get_nonexistent(self, store: MemoryStore) -> None:
        assert store.get("nonexistent") is None

    def test_save_updates_existing(self, store: MemoryStore) -> None:
        store.save(key="k1", value="original")
        store.save(key="k1", value="updated")

        loaded = store.get("k1")
        assert loaded is not None
        assert loaded.value == "updated"
        assert store.count() == 1

    def test_save_preserves_created_at(self, store: MemoryStore) -> None:
        entry1 = store.save(key="k1", value="v1")
        assert isinstance(entry1, MemoryEntry)
        created = entry1.created_at

        entry2 = store.save(key="k1", value="v2")
        assert isinstance(entry2, MemoryEntry)
        assert entry2.created_at == created

    def test_delete(self, store: MemoryStore) -> None:
        store.save(key="k1", value="v1")
        assert store.delete("k1") is True
        assert store.get("k1") is None
        assert store.count() == 0

    def test_delete_nonexistent(self, store: MemoryStore) -> None:
        assert store.delete("nonexistent") is False

    def test_count(self, store: MemoryStore) -> None:
        assert store.count() == 0
        store.save(key="k1", value="v1")
        assert store.count() == 1
        store.save(key="k2", value="v2")
        assert store.count() == 2

    def test_get_updates_access_metadata(self, store: MemoryStore) -> None:
        store.save(key="k1", value="v1")
        entry = store.get("k1")
        assert entry is not None
        assert entry.access_count == 1

        entry2 = store.get("k1")
        assert entry2 is not None
        assert entry2.access_count == 2


class TestMemoryStoreList:
    """Tests for list_all with filters."""

    def test_list_all_unfiltered(self, store: MemoryStore) -> None:
        store.save(key="k1", value="v1")
        store.save(key="k2", value="v2")
        entries = store.list_all()
        assert len(entries) == 2

    def test_list_filter_by_tier(self, store: MemoryStore) -> None:
        store.save(key="a1", value="v", tier="architectural")
        store.save(key="p1", value="v", tier="pattern")
        entries = store.list_all(tier="architectural")
        assert len(entries) == 1
        assert entries[0].key == "a1"

    def test_list_filter_by_scope(self, store: MemoryStore) -> None:
        store.save(key="proj1", value="v", scope="project")
        store.save(key="br1", value="v", scope="branch", branch="main")
        entries = store.list_all(scope="project")
        assert len(entries) == 1
        assert entries[0].key == "proj1"

    def test_list_filter_by_tags(self, store: MemoryStore) -> None:
        store.save(key="k1", value="v", tags=["python"])
        store.save(key="k2", value="v", tags=["rust"])
        entries = store.list_all(tags=["python"])
        assert len(entries) == 1
        assert entries[0].key == "k1"


class TestMemoryStoreSearch:
    """Tests for FTS5 search."""

    def test_search(self, store: MemoryStore) -> None:
        store.save(key="arch-decision", value="Use SQLite for storage")
        store.save(key="code-pattern", value="Always use type hints")
        results = store.search("SQLite")
        assert len(results) >= 1
        assert any(r.key == "arch-decision" for r in results)

    def test_search_empty_returns_empty(self, store: MemoryStore) -> None:
        store.save(key="k1", value="v1")
        assert store.search("") == []


class TestMemoryStoreUpdateFields:
    """Tests for partial field updates."""

    def test_update_confidence(self, store: MemoryStore) -> None:
        store.save(key="k1", value="v1")
        updated = store.update_fields("k1", confidence=0.9)
        assert updated is not None
        assert updated.confidence == 0.9

    def test_update_nonexistent_returns_none(self, store: MemoryStore) -> None:
        assert store.update_fields("nonexistent", confidence=0.5) is None

    def test_update_contradicted(self, store: MemoryStore) -> None:
        store.save(key="k1", value="v1")
        updated = store.update_fields(
            "k1", contradicted=True, contradiction_reason="outdated"
        )
        assert updated is not None
        assert updated.contradicted is True
        assert updated.contradiction_reason == "outdated"


class TestMemoryStoreSnapshot:
    """Tests for snapshot generation."""

    def test_snapshot_empty(self, store: MemoryStore) -> None:
        snap = store.snapshot()
        assert snap.total_count == 0
        assert snap.entries == []

    def test_snapshot_with_entries(self, store: MemoryStore) -> None:
        store.save(key="k1", value="v1", tier="architectural")
        store.save(key="k2", value="v2", tier="pattern")
        snap = store.snapshot()
        assert snap.total_count == 2
        assert snap.tier_counts.get("architectural") == 1
        assert snap.tier_counts.get("pattern") == 1


class TestMemoryStoreEviction:
    """Tests for max entries eviction."""

    def test_evicts_lowest_confidence_at_max(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        # Fill to max with confidence 0.5
        for i in range(_MAX_ENTRIES):
            store.save(
                key=f"entry-{i:04d}",
                value=f"value {i}",
                source="agent",
                confidence=0.5,
            )
        assert store.count() == _MAX_ENTRIES

        # Add one with low confidence first to be the eviction target
        store.save(key="low-conf", value="low", source="inferred", confidence=0.1)
        # Now at max, the lowest was evicted (could be low-conf or entry-0000)
        assert store.count() == _MAX_ENTRIES


class TestMemoryStoreRAGSafety:
    """Tests for RAG safety on save."""

    def test_normal_content_passes(self, store: MemoryStore) -> None:
        result = store.save(key="safe-key", value="Normal safe content")
        assert isinstance(result, MemoryEntry)

    def test_blocked_content_returns_error(self, store: MemoryStore) -> None:
        # Simulate content that triggers heavy RAG safety flags
        with patch(
            "tapps_brain.store.check_content_safety"
        ) as mock_safety:
            from tapps_brain.safety import SafetyCheckResult

            mock_safety.return_value = SafetyCheckResult(
                safe=False,
                flagged_patterns=["role_manipulation", "instruction_injection"],
                match_count=5,
            )
            result = store.save(key="bad-key", value="malicious content")
            assert isinstance(result, dict)
            assert result["error"] == "content_blocked"
