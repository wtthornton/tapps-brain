"""Integration and edge case tests for the memory foundation.

Tests full round-trips from store -> persistence -> reload, store-level
edge cases, concurrency, persistence edge cases, and decay/contradiction
integration with the store.

Note: Pure model validation tests (key format, value length, tags, etc.)
live in ``test_memory_models.py`` — this file focuses on *store-level*
behaviour and persistence round-trips.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator

import pytest

from tapps_brain.decay import DecayConfig, get_effective_confidence
from tapps_brain.models import (
    MAX_KEY_LENGTH,
    MAX_VALUE_LENGTH,
    MemoryEntry,
    MemoryTier,
)
from tapps_brain.persistence import MemoryPersistence
from tapps_brain.store import MemoryStore

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def store(tmp_path: Path) -> Generator[MemoryStore, None, None]:
    """Create a fresh MemoryStore backed by a temp directory."""
    s = MemoryStore(tmp_path)
    yield s
    s.close()


# ---------------------------------------------------------------------------
# Integration: round-trip tests
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_save_and_get(self, store: MemoryStore) -> None:
        """A saved entry can be retrieved by key."""
        store.save(key="my-key", value="hello world")
        entry = store.get("my-key")
        assert entry is not None
        assert entry.key == "my-key"
        assert entry.value == "hello world"

    def test_save_persist_and_reload(self, tmp_path: Path) -> None:
        """Entries survive store close and reopen (persistence round-trip)."""
        store1 = MemoryStore(tmp_path)
        store1.save(key="persistent-key", value="persisted value", tier="architectural")
        store1.close()

        store2 = MemoryStore(tmp_path)
        entry = store2.get("persistent-key")
        store2.close()

        assert entry is not None
        assert entry.value == "persisted value"
        assert entry.tier == MemoryTier.architectural

    def test_save_close_reload_full_fields(self, tmp_path: Path) -> None:
        """Save with all fields, close, reopen — verify every field survives."""
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
        store2.close()

        assert loaded is not None
        assert loaded.value == "Use SQLite for persistence"
        assert loaded.tier == MemoryTier.architectural
        assert loaded.source_agent == "claude-code"
        assert loaded.tags == ["architecture", "database"]
        assert loaded.confidence == 0.95

    def test_update_preserves_created_at(self, store: MemoryStore) -> None:
        """Updating a key preserves original created_at."""
        store.save(key="update-key", value="v1")
        entry1 = store.get("update-key")
        assert entry1 is not None
        created = entry1.created_at

        store.save(key="update-key", value="v2")
        entry2 = store.get("update-key")
        assert entry2 is not None
        assert entry2.value == "v2"
        assert entry2.created_at == created

    def test_delete_removes_from_store_and_persistence(self, tmp_path: Path) -> None:
        """Deleted entries are gone after store reopen."""
        store1 = MemoryStore(tmp_path)
        store1.save(key="delete-me", value="temp")
        assert store1.delete("delete-me") is True
        assert store1.get("delete-me") is None
        store1.close()

        store2 = MemoryStore(tmp_path)
        assert store2.get("delete-me") is None
        assert store2.count() == 0
        store2.close()

    def test_search_via_fts5(self, store: MemoryStore) -> None:
        """FTS5 search returns matching entries."""
        store.save(key="python-orm", value="SQLAlchemy is a great ORM for Python")
        store.save(key="js-runtime", value="Node.js powers many backends")

        results = store.search("SQLAlchemy")
        assert len(results) >= 1
        assert any(r.key == "python-orm" for r in results)

    def test_search_after_reload(self, tmp_path: Path) -> None:
        """FTS5 search works after store close and reopen."""
        store1 = MemoryStore(tmp_path)
        store1.save(key="pattern-1", value="Always use type annotations in Python")
        store1.close()

        store2 = MemoryStore(tmp_path)
        results = store2.search("annotations")
        assert len(results) >= 1
        assert results[0].key == "pattern-1"
        store2.close()

    def test_multiple_entries_survive_reload(self, tmp_path: Path) -> None:
        """Batch of entries survives close and reopen."""
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

    def test_list_with_tier_filter(self, store: MemoryStore) -> None:
        """list_all respects tier filter."""
        store.save(key="arch-1", value="architecture note", tier="architectural")
        store.save(key="pat-1", value="pattern note", tier="pattern")

        arch_entries = store.list_all(tier="architectural")
        assert len(arch_entries) == 1
        assert arch_entries[0].key == "arch-1"

    def test_update_fields_partial(self, store: MemoryStore) -> None:
        """update_fields changes specific fields without clobbering others."""
        store.save(key="partial-key", value="original")
        updated = store.update_fields("partial-key", confidence=0.99)
        assert updated is not None
        assert updated.confidence == 0.99
        assert updated.value == "original"

    def test_snapshot_includes_all(self, store: MemoryStore) -> None:
        """Snapshot captures all entries and tier counts."""
        store.save(key="a1", value="v1", tier="architectural")
        store.save(key="p1", value="v2", tier="pattern")
        store.save(key="c1", value="v3", tier="context")

        snap = store.snapshot()
        assert snap.total_count == 3
        assert snap.tier_counts.get("architectural") == 1
        assert snap.tier_counts.get("pattern") == 1
        assert snap.tier_counts.get("context") == 1


# ---------------------------------------------------------------------------
# Decay and contradiction integration with store
# ---------------------------------------------------------------------------


class TestDecayStoreIntegration:
    def test_fresh_entry_high_confidence(self, store: MemoryStore) -> None:
        """A just-saved entry has high effective confidence."""
        store.save(key="fresh", value="brand new", source="human")
        entry = store.get("fresh")
        assert entry is not None

        config = DecayConfig()
        effective, is_stale = get_effective_confidence(entry, config)
        assert effective > 0.8
        assert not is_stale

    def test_update_fields_reinforcement(self, store: MemoryStore) -> None:
        """update_fields works for reinforcement field updates."""
        store.save(key="reinforce-test", value="test")

        from datetime import UTC, datetime

        now = datetime.now(tz=UTC).isoformat()
        updated = store.update_fields(
            "reinforce-test",
            last_reinforced=now,
            reinforce_count=1,
        )
        assert updated is not None
        assert updated.last_reinforced == now
        assert updated.reinforce_count == 1

    def test_update_fields_contradiction(self, store: MemoryStore) -> None:
        """update_fields works for contradiction flagging."""
        store.save(key="contradict-test", value="test")

        updated = store.update_fields(
            "contradict-test",
            contradicted=True,
            contradiction_reason="Tech stack drift",
            confidence=0.3,
        )
        assert updated is not None
        assert updated.contradicted is True
        assert updated.contradiction_reason == "Tech stack drift"
        assert updated.confidence == 0.3


# ---------------------------------------------------------------------------
# Store-level edge cases
# ---------------------------------------------------------------------------


class TestStoreEdgeCases:
    def test_empty_store_operations(self, tmp_path: Path) -> None:
        """All operations on an empty store behave gracefully."""
        store = MemoryStore(tmp_path)
        try:
            assert store.count() == 0
            assert store.get("nonexistent") is None
            assert store.list_all() == []
            assert store.search("anything") == []
            assert store.delete("nonexistent") is False
            snap = store.snapshot()
            assert snap.total_count == 0
        finally:
            store.close()

    def test_get_nonexistent_key(self, store: MemoryStore) -> None:
        assert store.get("no-such-key") is None

    def test_delete_nonexistent_key(self, store: MemoryStore) -> None:
        assert store.delete("no-such-key") is False

    def test_update_fields_nonexistent(self, store: MemoryStore) -> None:
        assert store.update_fields("no-such-key", confidence=0.5) is None

    def test_max_key_length_roundtrip(self, tmp_path: Path) -> None:
        """Max-length key survives save and reload."""
        store = MemoryStore(tmp_path)
        try:
            long_key = "a" * MAX_KEY_LENGTH
            result = store.save(key=long_key, value="value")
            assert isinstance(result, MemoryEntry)
            loaded = store.get(long_key)
            assert loaded is not None
        finally:
            store.close()

    def test_max_value_length_roundtrip(self, tmp_path: Path) -> None:
        """Max-length value survives save and reload."""
        store = MemoryStore(tmp_path)
        try:
            long_value = "x" * MAX_VALUE_LENGTH
            result = store.save(key="long-val", value=long_value)
            assert isinstance(result, MemoryEntry)
            loaded = store.get("long-val")
            assert loaded is not None
            assert len(loaded.value) == MAX_VALUE_LENGTH
        finally:
            store.close()

    def test_max_tags_roundtrip(self, tmp_path: Path) -> None:
        """Max-count tags survive save and reload."""
        store = MemoryStore(tmp_path)
        try:
            tags = [f"tag{i}" for i in range(10)]
            result = store.save(key="many-tags", value="v", tags=tags)
            assert isinstance(result, MemoryEntry)
            assert len(result.tags) == 10
        finally:
            store.close()

    def test_unknown_tier_coerces_to_pattern(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        try:
            out = store.save(key="bad-tier", value="v", tier="nonexistent")
            assert isinstance(out, MemoryEntry)
            assert str(out.tier) == "pattern"
        finally:
            store.close()

    def test_invalid_scope_rejected(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        try:
            with pytest.raises(ValueError):
                store.save(key="bad-scope", value="v", scope="nonexistent")
        finally:
            store.close()

    def test_branch_scope_requires_branch(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        try:
            with pytest.raises(ValueError):
                store.save(key="no-branch", value="v", scope="branch")
        finally:
            store.close()


# ---------------------------------------------------------------------------
# Persistence edge cases
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


class TestConcurrency:
    def test_concurrent_saves_to_same_key(self, store: MemoryStore) -> None:
        """Multiple threads saving to the same key doesn't corrupt state."""
        errors: list[Exception] = []

        def saver(thread_id: int) -> None:
            try:
                for i in range(20):
                    store.save(key="shared-key", value=f"thread-{thread_id}-iter-{i}")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=saver, args=(t,)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrent save errors: {errors}"
        entry = store.get("shared-key")
        assert entry is not None
        assert entry.value.startswith("thread-")

    def test_concurrent_reads_and_writes(self, store: MemoryStore) -> None:
        """Concurrent reads and writes don't cause errors."""
        store.save(key="rw-test", value="initial")
        errors: list[Exception] = []

        def writer() -> None:
            try:
                for i in range(20):
                    store.save(key="rw-test", value=f"write-{i}")
            except Exception as exc:
                errors.append(exc)

        def reader() -> None:
            try:
                for _ in range(20):
                    store.get("rw-test")
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=reader),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrent read/write errors: {errors}"
