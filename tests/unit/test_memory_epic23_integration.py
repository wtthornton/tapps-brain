"""Integration and edge case tests for Epic 23 memory foundation.

Tests the full round-trip from store -> persistence -> reload,
edge cases, and concurrency.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from tapps_brain.models import (
    MAX_KEY_LENGTH,
    MAX_VALUE_LENGTH,
    MemoryEntry,
    MemoryTier,
)
from tapps_brain.persistence import MemoryPersistence
from tapps_brain.store import MemoryStore


class TestFullRoundTrip:
    """Integration: store -> persistence -> reload."""

    def test_save_close_reload(self, tmp_path: Path) -> None:
        """Save via store, close, reopen, verify data survives."""
        store1 = MemoryStore(tmp_path)
        result = store1.save(
            key="arch-decision",
            value="Use SQLite for persistence",
            tier="architectural",
            source="human",
            source_agent="claude-code",
            scope="project",
            tags=["architecture", "database"],
            confidence=0.95,
        )
        assert isinstance(result, MemoryEntry)
        store1.close()

        store2 = MemoryStore(tmp_path)
        loaded = store2.get("arch-decision")
        assert loaded is not None
        assert loaded.value == "Use SQLite for persistence"
        assert loaded.tier == MemoryTier.architectural
        assert loaded.source_agent == "claude-code"
        assert loaded.tags == ["architecture", "database"]
        assert loaded.confidence == 0.95
        store2.close()

    def test_delete_persists(self, tmp_path: Path) -> None:
        store1 = MemoryStore(tmp_path)
        store1.save(key="temp", value="will be deleted")
        store1.delete("temp")
        store1.close()

        store2 = MemoryStore(tmp_path)
        assert store2.get("temp") is None
        assert store2.count() == 0
        store2.close()

    def test_search_after_reload(self, tmp_path: Path) -> None:
        store1 = MemoryStore(tmp_path)
        store1.save(key="pattern-1", value="Always use type annotations in Python")
        store1.close()

        store2 = MemoryStore(tmp_path)
        results = store2.search("annotations")
        assert len(results) >= 1
        assert results[0].key == "pattern-1"
        store2.close()

    def test_multiple_entries_survive_reload(self, tmp_path: Path) -> None:
        store1 = MemoryStore(tmp_path)
        for i in range(10):
            store1.save(key=f"entry-{i}", value=f"value-{i}")
        store1.close()

        store2 = MemoryStore(tmp_path)
        assert store2.count() == 10
        for i in range(10):
            loaded = store2.get(f"entry-{i}")
            assert loaded is not None
            assert loaded.value == f"value-{i}"
        store2.close()


class TestEdgeCases:
    """Edge case tests for the memory subsystem."""

    def test_empty_store_operations(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        assert store.count() == 0
        assert store.get("nonexistent") is None
        assert store.list_all() == []
        assert store.search("anything") == []
        assert store.delete("nonexistent") is False
        snap = store.snapshot()
        assert snap.total_count == 0

    def test_max_key_length_roundtrip(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        long_key = "a" * MAX_KEY_LENGTH
        result = store.save(key=long_key, value="value")
        assert isinstance(result, MemoryEntry)
        loaded = store.get(long_key)
        assert loaded is not None

    def test_max_value_length_roundtrip(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        long_value = "x" * MAX_VALUE_LENGTH
        result = store.save(key="long-val", value=long_value)
        assert isinstance(result, MemoryEntry)
        loaded = store.get("long-val")
        assert loaded is not None
        assert len(loaded.value) == MAX_VALUE_LENGTH

    def test_max_tags_roundtrip(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        tags = [f"tag{i}" for i in range(10)]
        result = store.save(key="many-tags", value="v", tags=tags)
        assert isinstance(result, MemoryEntry)
        assert len(result.tags) == 10

    def test_invalid_tier_rejected(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        with pytest.raises(Exception):
            store.save(key="bad-tier", value="v", tier="nonexistent")

    def test_invalid_scope_rejected(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        with pytest.raises(Exception):
            store.save(key="bad-scope", value="v", scope="nonexistent")

    def test_branch_scope_requires_branch(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        with pytest.raises(Exception):
            store.save(key="no-branch", value="v", scope="branch")


class TestConcurrency:
    """Concurrency tests for the memory subsystem."""

    def test_concurrent_saves_to_same_key(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        errors: list[Exception] = []

        def save_entry(i: int) -> None:
            try:
                store.save(key="shared-key", value=f"value-{i}")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=save_entry, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert store.count() == 1
        loaded = store.get("shared-key")
        assert loaded is not None

    def test_concurrent_reads_during_writes(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        store.save(key="base", value="initial")
        errors: list[Exception] = []

        def reader() -> None:
            try:
                for _ in range(10):
                    store.get("base")
            except Exception as exc:
                errors.append(exc)

        def writer() -> None:
            try:
                for i in range(10):
                    store.save(key=f"write-{i}", value=f"v-{i}")
            except Exception as exc:
                errors.append(exc)

        t_read = threading.Thread(target=reader)
        t_write = threading.Thread(target=writer)
        t_read.start()
        t_write.start()
        t_read.join()
        t_write.join()

        assert not errors


class TestPersistenceEdgeCases:
    """Edge cases for the SQLite persistence layer."""

    def test_schema_version_persists(self, tmp_path: Path) -> None:
        p1 = MemoryPersistence(tmp_path)
        v1 = p1.get_schema_version()
        assert v1 >= 1  # Migrations run on init
        p1.close()

        p2 = MemoryPersistence(tmp_path)
        assert p2.get_schema_version() == v1
        p2.close()

    def test_fts_special_chars_no_crash(self, tmp_path: Path) -> None:
        p = MemoryPersistence(tmp_path)
        p.save(MemoryEntry(key="special", value="Use C++ and C# languages"))
        results = p.search("C++")
        assert isinstance(results, list)
        p.close()

    def test_empty_fts_search(self, tmp_path: Path) -> None:
        p = MemoryPersistence(tmp_path)
        assert p.search("") == []
        assert p.search("   ") == []
        p.close()
