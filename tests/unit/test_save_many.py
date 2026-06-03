"""Unit tests for ``MemoryStore.save_many`` — batched persist (TAP-2800).

The old ``memory_save_many`` loop issued N independent DB write-throughs (one
``store.save`` -> ``backend.save`` per entry).  ``save_many`` runs the per-row
pre-persist pipeline (validate / dedup / conflict) in memory, then issues a
SINGLE batched ``backend.save_many`` for the valid rows, then runs the per-row
post-persist fan-out — while preserving the partial-failure semantics of the
single-save path.

All DB interaction is via the in-memory fake backend injected by conftest.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from tapps_brain.models import MemoryEntry
from tapps_brain.store import MemoryStore

if TYPE_CHECKING:
    from pathlib import Path


def _items(n: int) -> list[dict[str, object]]:
    return [{"key": f"k{i}", "value": f"value number {i}"} for i in range(n)]


class TestSaveManyHappyPath:
    def test_persists_all_rows_and_returns_entries(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        results = store.save_many(_items(4))

        assert len(results) == 4
        assert all(isinstance(r, MemoryEntry) for r in results)
        for i in range(4):
            entry = store.get(f"k{i}")
            assert entry is not None
            assert entry.value == f"value number {i}"

    def test_single_batched_persist_not_n_saves(self, tmp_path: Path) -> None:
        """AC-4: a batch of N entries triggers ONE backend.save_many call rather
        than N per-row persists."""
        store = MemoryStore(tmp_path)
        backend = store._persistence  # type: ignore[attr-defined]
        # Backend-agnostic spy (works against both the in-memory fake and the
        # real PostgresPrivateBackend in CI): wrap save_many and count calls.
        with patch.object(backend, "save_many", wraps=backend.save_many) as spy:
            store.save_many(_items(5))
        # Exactly one batched round-trip carried all five rows.
        assert spy.call_count == 1

    def test_empty_batch_returns_empty_list(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        backend = store._persistence  # type: ignore[attr-defined]
        with patch.object(backend, "save_many", wraps=backend.save_many) as spy:
            assert store.save_many([]) == []
        # No rows -> no batched persist.
        assert spy.call_count == 0


class TestSaveManyPartialFailure:
    def test_one_bad_row_does_not_abort_valid_rows(self, tmp_path: Path) -> None:
        """AC-2/AC-3: an invalid row surfaces a per-item error without rolling
        back the valid rows, and the result list stays aligned with the input."""
        store = MemoryStore(tmp_path)
        items: list[dict[str, object]] = [
            {"key": "good-1", "value": "first"},
            {"key": "bad", "value": "second", "agent_scope": "not-a-real-scope"},
            {"key": "good-2", "value": "third"},
        ]
        results = store.save_many(items)

        assert len(results) == 3
        assert isinstance(results[0], MemoryEntry)
        assert isinstance(results[1], dict) and "error" in results[1]
        assert isinstance(results[2], MemoryEntry)
        # The two valid rows are durably persisted despite the bad middle row.
        assert store.get("good-1") is not None
        assert store.get("good-2") is not None

    def test_bad_slug_key_returns_per_row_bad_request(self, tmp_path: Path) -> None:
        """A slug key that fails MemoryEntry validation (TAP-747) surfaces as a
        per-row ``bad_request`` rather than raising and aborting the batch."""
        store = MemoryStore(tmp_path)
        results = store.save_many(
            [
                {"key": "valid-key", "value": "first"},
                {"key": "_bad-key", "value": "second"},  # leading underscore — invalid slug
            ]
        )
        assert isinstance(results[0], MemoryEntry)
        assert isinstance(results[1], dict)
        assert results[1]["error"] == "bad_request"
        assert store.get("valid-key") is not None

    def test_invalid_rows_excluded_from_batch(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        backend = store._persistence  # type: ignore[attr-defined]
        items: list[dict[str, object]] = [
            {"key": "ok", "value": "valid value"},
            {"key": "nope", "value": "v", "agent_scope": "bogus"},
        ]
        with patch.object(backend, "save_many", wraps=backend.save_many) as spy:
            store.save_many(items)
        # Still exactly one batched call — for the single valid row.
        assert spy.call_count == 1


class TestSaveManyShortCircuits:
    def test_dedup_hit_returns_entry_without_rebatching(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        store.save(key="orig", value="duplicated content here")

        # Same value under a new key -> dedup fast-path returns the existing
        # entry rather than persisting a new row.
        results = store.save_many([{"key": "dup", "value": "duplicated content here"}])
        assert len(results) == 1
        assert isinstance(results[0], MemoryEntry)
        # No new key was created by the dedup hit.
        assert store.get("dup") is None


class TestSaveManyParityWithSave:
    def test_save_many_matches_per_row_save(self, tmp_path: Path) -> None:
        """save_many must produce the same persisted state as calling save in a
        loop — the batching is a performance change, not a behaviour change."""
        loop_store = MemoryStore(tmp_path / "loop")
        batch_store = MemoryStore(tmp_path / "batch")

        items = _items(6)
        for item in items:
            loop_store.save(**item)  # type: ignore[arg-type]
        batch_store.save_many(items)

        for item in items:
            key = item["key"]
            loop_entry = loop_store.get(key)  # type: ignore[arg-type]
            batch_entry = batch_store.get(key)  # type: ignore[arg-type]
            assert loop_entry is not None
            assert batch_entry is not None
            assert loop_entry.value == batch_entry.value
            assert str(loop_entry.tier) == str(batch_entry.tier)
            assert loop_entry.agent_scope == batch_entry.agent_scope


class TestSaveManyRollback:
    def test_persist_failure_rolls_back_cache(self, tmp_path: Path) -> None:
        """If the batched persist raises, the in-memory cache must not retain the
        un-persisted rows (write-through consistency)."""
        store = MemoryStore(tmp_path)
        backend = store._persistence  # type: ignore[attr-defined]

        def _boom(_entries: list[MemoryEntry]) -> None:
            raise RuntimeError("simulated DB failure")

        backend.save_many = _boom  # type: ignore[assignment]

        with pytest.raises(RuntimeError, match="simulated DB failure"):
            store.save_many(_items(3))

        # None of the failed-batch rows linger in the cache.
        for i in range(3):
            assert store.get(f"k{i}") is None
