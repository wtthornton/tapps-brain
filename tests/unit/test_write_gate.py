"""VAL-06 (TAP-6696): write gate — dedup coalesce + oversize value refusal.

A value over the per-entry cap returns a structured ``value_too_large`` error
naming ``/v1/documents`` instead of a generic pydantic 400. See
store.py::MemoryStore._value_too_large_error.

A byte-identical re-save under the *same* ``(project_id, agent_id, key)``
returns ``status="coalesced"`` (round 2): no new row is written, so the
caller must not see it echoed back as an indistinguishable second ``"saved"``.
See store.py::MemoryStore._handle_dedup.

Cross-key byte-identical *dedup/coalesce* is deliberately NOT implemented —
it would reintroduce the exact bug TAP-5615/5616/5617 fixed for a real
production consumer (nlt-ideas-scout): matching writes by value across keys
silently discarded the requested key's write. That fix is locked in by
tests/unit/test_save_write_loss.py and tests/unit/test_save_many.py ("same
value under distinct keys must persist as distinct rows"), which building
cross-key coalesce as originally specified would break. See the PR
description / evidence block for the full rationale.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tapps_brain.services import memory_service
from tapps_brain.store import MemoryStore

if TYPE_CHECKING:
    from collections.abc import Generator


@pytest.fixture()
def store(tmp_path: Path) -> Generator[MemoryStore, None, None]:
    """MemoryStore backed by InMemoryPrivateBackend (injected by conftest)."""
    s = MemoryStore(tmp_path)
    yield s
    s.close()


class TestOversizeValueWriteGate:
    def test_value_over_cap_returns_413_shaped_error(self, store: MemoryStore) -> None:
        cap = store.effective_max_value_length()
        oversized = "x" * (cap + 1)
        result = memory_service.memory_save(store, "proj", "agent", key="too-big", value=oversized)
        assert result.get("error") == "value_too_large"
        assert "/v1/documents" in result["detail"]
        assert result["max_value_length"] == cap
        assert store.get("too-big") is None

    def test_value_at_cap_boundary_still_saves(self, store: MemoryStore) -> None:
        cap = store.effective_max_value_length()
        exactly_at_cap = "y" * cap
        result = memory_service.memory_save(
            store, "proj", "agent", key="at-cap", value=exactly_at_cap
        )
        assert result["status"] == "saved"

    def test_batch_row_over_cap_returns_value_too_large(self, store: MemoryStore) -> None:
        cap = store.effective_max_value_length()
        result = memory_service.memory_save_many(
            store,
            "proj",
            "agent",
            entries=[
                {"key": "ok-row", "value": "short"},
                {"key": "big-row", "value": "z" * (cap + 1)},
            ],
        )
        rows = result["results"]
        big_row_result = next(r for r in rows if r.get("error") == "value_too_large")
        assert "/v1/documents" in big_row_result["detail"]
        assert store.get("ok-row") is not None
        assert store.get("big-row") is None


class TestSameKeyDedupCoalesce:
    """Round 2: a byte-identical re-save under the same key coalesces."""

    def test_same_key_identical_value_coalesces(self, store: MemoryStore) -> None:
        first = memory_service.memory_save(
            store, "proj", "agent", key="stable", value="unchanged content"
        )
        assert first["status"] == "saved"

        second = memory_service.memory_save(
            store, "proj", "agent", key="stable", value="unchanged content"
        )
        assert second["status"] == "coalesced"
        assert second["key"] == "stable"
        assert second["coalesced_into"] == "stable"
        assert second["persisted"] is False

        entry = store.get("stable")
        assert entry is not None
        assert entry.reinforce_count == 1

    def test_same_key_different_value_still_saves(self, store: MemoryStore) -> None:
        memory_service.memory_save(store, "proj", "agent", key="stable", value="version one")

        second = memory_service.memory_save(
            store, "proj", "agent", key="stable", value="version two, materially different text"
        )
        assert second["status"] == "saved"
        entry = store.get("stable")
        assert entry is not None
        assert entry.value == "version two, materially different text"

    def test_distinct_keys_same_value_all_persist(self, store: MemoryStore) -> None:
        """Regression guard: round-2 same-key coalesce must not resurrect the
        cross-key matching bug TAP-5615 fixed."""
        keys = [f"dedup-echo-{i}" for i in range(5)]
        envelopes = [
            memory_service.memory_save(store, "proj", "agent", key=key, value="echo-probe")
            for key in keys
        ]
        assert all(e["status"] == "saved" for e in envelopes)
        assert [e["key"] for e in envelopes] == keys
        for key in keys:
            entry = store.get(key)
            assert entry is not None
            assert entry.value == "echo-probe"
