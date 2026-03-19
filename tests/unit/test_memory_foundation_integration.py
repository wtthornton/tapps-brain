"""Integration and edge case tests for Epic 23 memory foundation.

Tests full round-trips from store -> persistence -> reload, model edge
cases, concurrency, and decay/contradiction integration with the store.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from tapps_brain.decay import DecayConfig, get_effective_confidence
from tapps_brain.models import (
    MAX_KEY_LENGTH,
    MAX_TAGS,
    MAX_VALUE_LENGTH,
    MemoryEntry,
    MemoryScope,
    MemorySource,
    MemoryTier,
)
from tapps_brain.store import MemoryStore


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
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
        store2.close()

    def test_search_via_fts5(self, store: MemoryStore) -> None:
        """FTS5 search returns matching entries."""
        store.save(key="python-orm", value="SQLAlchemy is a great ORM for Python")
        store.save(key="js-runtime", value="Node.js powers many backends")

        results = store.search("SQLAlchemy")
        assert len(results) >= 1
        assert any(r.key == "python-orm" for r in results)

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
# Edge cases: model validation
# ---------------------------------------------------------------------------


class TestModelEdgeCases:
    def test_empty_store_list(self, store: MemoryStore) -> None:
        assert store.list_all() == []

    def test_empty_store_count(self, store: MemoryStore) -> None:
        assert store.count() == 0

    def test_get_nonexistent_key(self, store: MemoryStore) -> None:
        assert store.get("no-such-key") is None

    def test_delete_nonexistent_key(self, store: MemoryStore) -> None:
        assert store.delete("no-such-key") is False

    def test_update_fields_nonexistent(self, store: MemoryStore) -> None:
        assert store.update_fields("no-such-key", confidence=0.5) is None

    def test_max_key_length(self) -> None:
        key = "a" * MAX_KEY_LENGTH
        entry = MemoryEntry(key=key, value="test")
        assert entry.key == key

    def test_key_too_long_rejected(self) -> None:
        key = "a" * (MAX_KEY_LENGTH + 1)
        with pytest.raises(Exception):  # noqa: B017
            MemoryEntry(key=key, value="test")

    def test_max_value_length(self) -> None:
        value = "x" * MAX_VALUE_LENGTH
        entry = MemoryEntry(key="big-value", value=value)
        assert len(entry.value) == MAX_VALUE_LENGTH

    def test_value_too_long_rejected(self) -> None:
        value = "x" * (MAX_VALUE_LENGTH + 1)
        with pytest.raises(Exception):  # noqa: B017
            MemoryEntry(key="too-big", value=value)

    def test_empty_value_rejected(self) -> None:
        with pytest.raises(Exception):  # noqa: B017
            MemoryEntry(key="empty-val", value="   ")

    def test_max_tags(self) -> None:
        tags = [f"tag-{i}" for i in range(MAX_TAGS)]
        entry = MemoryEntry(key="many-tags", value="test", tags=tags)
        assert len(entry.tags) == MAX_TAGS

    def test_too_many_tags_rejected(self) -> None:
        tags = [f"tag-{i}" for i in range(MAX_TAGS + 1)]
        with pytest.raises(Exception):  # noqa: B017
            MemoryEntry(key="too-many-tags", value="test", tags=tags)

    def test_invalid_key_format(self) -> None:
        with pytest.raises(Exception):  # noqa: B017
            MemoryEntry(key="UPPERCASE", value="test")

    def test_branch_required_for_branch_scope(self) -> None:
        with pytest.raises(Exception):  # noqa: B017
            MemoryEntry(key="branch-test", value="test", scope=MemoryScope.branch)

    def test_branch_scope_with_branch(self) -> None:
        entry = MemoryEntry(
            key="branch-test", value="test", scope=MemoryScope.branch, branch="main"
        )
        assert entry.branch == "main"

    def test_source_confidence_defaults(self) -> None:
        human = MemoryEntry(key="h1", value="test", source=MemorySource.human)
        agent = MemoryEntry(key="a1", value="test", source=MemorySource.agent)
        inferred = MemoryEntry(key="i1", value="test", source=MemorySource.inferred)
        system = MemoryEntry(key="s1", value="test", source=MemorySource.system)

        assert human.confidence == 0.95
        assert agent.confidence == 0.6
        assert inferred.confidence == 0.4
        assert system.confidence == 0.9


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
