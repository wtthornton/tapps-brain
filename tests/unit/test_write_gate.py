"""VAL-06 (TAP-6696): write gate — oversize value returns a 413-shaped refusal.

A value over the per-entry cap returns a structured ``value_too_large`` error
naming ``/v1/documents`` instead of a generic pydantic 400. See
store.py::MemoryStore._value_too_large_error.

Cross-key byte-identical *dedup/coalesce* (the other half of VAL-06) is
deliberately NOT implemented here — it would reintroduce the exact bug
TAP-5615/5616/5617 fixed for a real production consumer (nlt-ideas-scout):
matching writes by value across keys silently discarded the requested key's
write. That fix is locked in by tests/unit/test_save_write_loss.py and
tests/unit/test_save_many.py ("same value under distinct keys must persist
as distinct rows"), which building VAL-06 as literally specified would break.
See the PR description / evidence block for the full rationale — this is
reported as a blocked half-deliverable, not silently skipped.
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
