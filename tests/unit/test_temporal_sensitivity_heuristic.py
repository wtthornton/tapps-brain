"""VAL-11 (TAP-6696): default-fill temporal_sensitivity when the caller omits it.

- A value mentioning a port, URL, or pinned version -> "high".
- A value citing an ADR -> "low".
- A plain sentence -> None (the existing null/medium default).
- An explicit caller value is never overridden.

See temporal_sensitivity.py::infer_temporal_sensitivity and its wiring in
store.py::MemoryStore.save.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tapps_brain.services import memory_service
from tapps_brain.store import MemoryStore
from tapps_brain.temporal_sensitivity import infer_temporal_sensitivity

if TYPE_CHECKING:
    from collections.abc import Generator


class TestInferTemporalSensitivityPure:
    @pytest.mark.parametrize(
        "value",
        [
            "the service listens on localhost:8080 by default",
            "docs live at https://example.com/reference",
            "pin the dependency to v1.2.3 before release",
            "bumped to 2.4 in the last release",
        ],
    )
    def test_port_url_version_is_high(self, value: str) -> None:
        assert infer_temporal_sensitivity(value) == "high"

    @pytest.mark.parametrize(
        "value",
        [
            "see ADR-007 for the storage decision",
            "documented in adr-12 already",
        ],
    )
    def test_adr_citation_is_low(self, value: str) -> None:
        assert infer_temporal_sensitivity(value) == "low"

    def test_plain_sentence_is_none(self) -> None:
        assert infer_temporal_sensitivity("prefer composition over inheritance") is None

    def test_high_pattern_wins_when_both_present(self) -> None:
        """A pinned/volatile detail dominates even alongside a stable ADR citation."""
        assert infer_temporal_sensitivity("see ADR-007, now deployed on localhost:8080") == "high"


@pytest.fixture()
def store(tmp_path: Path) -> Generator[MemoryStore, None, None]:
    s = MemoryStore(tmp_path)
    yield s
    s.close()


class TestSaveDefaultFillsTemporalSensitivity:
    def test_high_pattern_default_fills_on_save(self, store: MemoryStore) -> None:
        result = memory_service.memory_save(
            store,
            "proj",
            "agent",
            key="svc-port",
            value="the internal API listens on localhost:9200",
        )
        assert result["status"] == "saved"
        entry = store.get("svc-port")
        assert entry is not None
        assert entry.temporal_sensitivity == "high"

    def test_adr_citation_default_fills_low(self, store: MemoryStore) -> None:
        memory_service.memory_save(
            store, "proj", "agent", key="storage-decision", value="see ADR-007 for the rationale"
        )
        entry = store.get("storage-decision")
        assert entry is not None
        assert entry.temporal_sensitivity == "low"

    def test_plain_value_leaves_temporal_sensitivity_unset(self, store: MemoryStore) -> None:
        memory_service.memory_save(
            store, "proj", "agent", key="convention", value="prefer composition over inheritance"
        )
        entry = store.get("convention")
        assert entry is not None
        assert entry.temporal_sensitivity is None

    def test_explicit_value_is_never_overridden(self, store: MemoryStore) -> None:
        """Value matches the high-sensitivity pattern, but the caller said 'low'."""
        result = memory_service.memory_save(
            store,
            "proj",
            "agent",
            key="explicit-override",
            value="the internal API listens on localhost:9200",
            temporal_sensitivity="low",
        )
        assert result["status"] == "saved"
        entry = store.get("explicit-override")
        assert entry is not None
        assert entry.temporal_sensitivity == "low"
