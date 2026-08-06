"""TAP-5544: mission-scoped shared state.

The load-bearing property is isolation: two missions living under one
``project_id`` must not be able to read each other's state. Everything else
here is shape.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tapps_brain.models import MemoryEntry, MemoryScope
from tapps_brain.services import memory_service


class _FakeStore:
    """Key-value stand-in that records what ``save`` was told."""

    def __init__(self) -> None:
        self.entries: dict[str, MemoryEntry] = {}
        self.save_calls: list[dict[str, Any]] = []

    def save(self, **kwargs: Any) -> MemoryEntry:
        self.save_calls.append(kwargs)
        entry = MemoryEntry(
            key=kwargs["key"],
            value=kwargs["value"],
            scope=MemoryScope(kwargs.get("scope", "project")),
            mission_id=kwargs.get("mission_id"),
            run_id=kwargs.get("run_id"),
            tags=kwargs.get("tags") or [],
        )
        self.entries[entry.key] = entry
        return entry

    def get(self, key: str) -> MemoryEntry | None:
        return self.entries.get(key)


def _set(store: _FakeStore, **kwargs: Any) -> dict[str, Any]:
    params: dict[str, Any] = {
        "mission_id": "m-1",
        "kind": "contract",
        "value": {"goal": "ship it"},
    }
    params.update(kwargs)
    return memory_service.brain_mission_state_set(store, "proj", "agent", **params)


def _get(store: _FakeStore, **kwargs: Any) -> dict[str, Any]:
    params: dict[str, Any] = {"mission_id": "m-1", "kind": "contract"}
    params.update(kwargs)
    return memory_service.brain_mission_state_get(store, "proj", "agent", **params)


class TestRoundTrip:
    def test_set_then_get_returns_the_payload(self) -> None:
        store = _FakeStore()
        _set(store)
        result = _get(store)
        assert result["found"] is True
        assert result["value"] == {"goal": "ship it"}

    def test_structured_payloads_survive(self) -> None:
        store = _FakeStore()
        payload = {"steps": [1, 2, 3], "nested": {"a": True}, "none": None}
        _set(store, value=payload, kind="findings")
        assert _get(store, kind="findings")["value"] == payload

    def test_set_records_mission_scope_and_companion_field(self) -> None:
        """Mission state rides the scope=branch precedent, not a parallel store."""
        store = _FakeStore()
        _set(store)
        call = store.save_calls[0]
        assert call["scope"] == "mission"
        assert call["mission_id"] == "m-1"

    def test_stored_entry_validates_as_mission_scoped(self) -> None:
        store = _FakeStore()
        _set(store)
        entry = next(iter(store.entries.values()))
        assert entry.scope is MemoryScope.mission
        assert entry.mission_id == "m-1"


class TestMissionIsolation:
    """Two missions under ONE project_id must not read each other."""

    def test_second_mission_cannot_read_the_first(self) -> None:
        store = _FakeStore()
        _set(store, mission_id="m-1", value={"secret": "mission one"})
        result = _get(store, mission_id="m-2")
        assert result["found"] is False
        assert result["value"] is None

    def test_each_mission_reads_only_its_own(self) -> None:
        store = _FakeStore()
        _set(store, mission_id="m-1", value={"owner": "one"})
        _set(store, mission_id="m-2", value={"owner": "two"})
        assert _get(store, mission_id="m-1")["value"] == {"owner": "one"}
        assert _get(store, mission_id="m-2")["value"] == {"owner": "two"}

    def test_forged_key_still_cannot_cross_missions(self) -> None:
        """Key namespacing is not the only guard — mission_id is re-checked."""
        store = _FakeStore()
        _set(store, mission_id="m-1", value={"secret": "mission one"})
        # Re-file mission one's entry under the key mission two would read.
        stolen = next(iter(store.entries.values()))
        store.entries["mission.m-2.contract"] = stolen
        result = _get(store, mission_id="m-2")
        assert result["found"] is False, "an entry owned by m-1 was served to m-2"

    def test_run_id_narrows_within_a_mission(self) -> None:
        store = _FakeStore()
        _set(store, mission_id="m-1", value={"scope": "mission-level"})
        _set(store, mission_id="m-1", run_id="r-9", value={"scope": "run-level"})
        assert _get(store, mission_id="m-1")["value"] == {"scope": "mission-level"}
        assert _get(store, mission_id="m-1", run_id="r-9")["value"] == {"scope": "run-level"}

    def test_a_run_scoped_slot_is_not_returned_to_the_mission_level_read(self) -> None:
        store = _FakeStore()
        _set(store, mission_id="m-1", run_id="r-9", value={"scope": "run-level"})
        assert _get(store, mission_id="m-1")["found"] is False


class TestMissingSlot:
    def test_unwritten_slot_is_found_false_not_an_error(self) -> None:
        """A worker picking up a mission needs "nothing parked yet" to be normal."""
        result = _get(_FakeStore())
        assert result["found"] is False
        assert result["value"] is None
        assert "error" not in result


class TestValidation:
    @pytest.mark.parametrize("bad", ["", "   "])
    def test_blank_mission_id_is_rejected(self, bad: str) -> None:
        assert _set(_FakeStore(), mission_id=bad)["error"] == "invalid_request"
        assert _get(_FakeStore(), mission_id=bad)["error"] == "invalid_request"

    def test_unknown_kind_is_rejected(self) -> None:
        assert _set(_FakeStore(), kind="vibes")["error"] == "invalid_request"
        assert _get(_FakeStore(), kind="vibes")["error"] == "invalid_request"

    @pytest.mark.parametrize("kind", ["contract", "findings", "knowledge"])
    def test_documented_kinds_are_accepted(self, kind: str) -> None:
        assert _set(_FakeStore(), kind=kind)["saved"] is True

    def test_non_serialisable_value_is_rejected_not_crashed(self) -> None:
        result = _set(_FakeStore(), value={"fn": object()})
        assert result["error"] == "invalid_request"

    @pytest.mark.parametrize("bad", ["mission:one", "Mission-1", "-leading", "has space", "a/b"])
    def test_key_unsafe_mission_id_is_a_400_not_a_crash(self, bad: str) -> None:
        """Identifiers become part of the memory key, which is a lowercase slug.

        Without this check the bad value surfaces as a pydantic ValidationError
        from deep inside save(), i.e. a 500 for what is the caller's mistake.
        """
        assert _set(_FakeStore(), mission_id=bad)["error"] == "invalid_request"

    def test_key_unsafe_run_id_is_rejected(self) -> None:
        assert _set(_FakeStore(), run_id="Run:9")["error"] == "invalid_request"

    def test_uppercase_is_rejected_rather_than_folded(self) -> None:
        """Silently lowercasing would collide 'M-1' with 'm-1' — an isolation bug."""
        assert _set(_FakeStore(), mission_id="M-1")["error"] == "invalid_request"

    def test_overlong_identifiers_are_rejected(self) -> None:
        assert _set(_FakeStore(), mission_id="m" * 130)["error"] == "invalid_request"


class TestModelValidation:
    def test_mission_scope_requires_a_mission_id(self) -> None:
        """Mirrors the scope=branch rule: the companion field is not optional."""
        with pytest.raises(ValueError, match="mission_id is required"):
            MemoryEntry(key="k", value="v", scope=MemoryScope.mission)

    def test_mission_scope_with_a_mission_id_is_valid(self) -> None:
        entry = MemoryEntry(key="k", value="v", scope=MemoryScope.mission, mission_id="m-1")
        assert entry.mission_id == "m-1"

    def test_other_scopes_do_not_require_a_mission_id(self) -> None:
        assert MemoryEntry(key="k", value="v").mission_id is None


class TestStorageShape:
    def test_value_is_persisted_as_json(self) -> None:
        store = _FakeStore()
        _set(store, value={"a": 1})
        assert json.loads(store.save_calls[0]["value"]) == {"a": 1}

    def test_run_id_participates_in_the_key(self) -> None:
        store = _FakeStore()
        _set(store, mission_id="m-1", kind="findings", run_id="r-2")
        assert store.save_calls[0]["key"] == "mission.m-1.r-2.findings"

    def test_mission_level_key_omits_the_run(self) -> None:
        store = _FakeStore()
        _set(store, mission_id="m-1", kind="findings")
        assert store.save_calls[0]["key"] == "mission.m-1.findings"
