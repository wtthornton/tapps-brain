"""Unit tests for TAP-731: failed_approaches field on MemoryEntry.

Covers:
- MemoryEntry.failed_approaches field: default empty, max 5 items
- _parse_jsonb_list helper in postgres_private
- PostgresPrivateBackend.save() persists failed_approaches as JSONB
- PostgresPrivateBackend._row_to_entry() restores failed_approaches
- brain_recall surfaces failed_approaches when non-empty
- brain_recall omits failed_approaches when empty
- brain_remember accepts and forwards failed_approaches
- store.save() passes failed_approaches to MemoryEntry
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# MemoryEntry field tests
# ---------------------------------------------------------------------------


class TestFailedApproachesField:
    def test_default_is_empty_list(self) -> None:
        from tapps_brain.models import MemoryEntry

        entry = MemoryEntry(key="k", value="v")
        assert entry.failed_approaches == []

    def test_can_set_approaches(self) -> None:
        from tapps_brain.models import MemoryEntry

        approaches = ["tried A — no effect", "tried B — worse"]
        entry = MemoryEntry(key="k", value="v", failed_approaches=approaches)
        assert entry.failed_approaches == approaches

    def test_max_five_items_enforced(self) -> None:
        from pydantic import ValidationError

        from tapps_brain.models import MemoryEntry

        with pytest.raises(ValidationError):
            MemoryEntry(
                key="k",
                value="v",
                failed_approaches=["a", "b", "c", "d", "e", "f"],  # 6 items — rejected
            )

    def test_exactly_five_items_accepted(self) -> None:
        from tapps_brain.models import MemoryEntry

        entry = MemoryEntry(
            key="k",
            value="v",
            failed_approaches=["a", "b", "c", "d", "e"],
        )
        assert len(entry.failed_approaches) == 5

    def test_serialization_roundtrip(self) -> None:
        from tapps_brain.models import MemoryEntry

        entry = MemoryEntry(
            key="k",
            value="v",
            failed_approaches=["tried X", "tried Y"],
        )
        data = entry.model_dump(mode="json")
        restored = MemoryEntry(**data)
        assert restored.failed_approaches == ["tried X", "tried Y"]


# ---------------------------------------------------------------------------
# _parse_jsonb_list helper tests
# ---------------------------------------------------------------------------


class TestParseJsonbList:
    def _fn(self, raw: Any) -> list[str]:
        from tapps_brain.postgres_private import _parse_jsonb_list

        return _parse_jsonb_list(raw)

    def test_list_passthrough(self) -> None:
        assert self._fn(["a", "b"]) == ["a", "b"]

    def test_json_string_parsed(self) -> None:
        assert self._fn('["x", "y"]') == ["x", "y"]

    def test_none_returns_empty(self) -> None:
        assert self._fn(None) == []

    def test_invalid_json_string_returns_empty(self) -> None:
        assert self._fn("not-json") == []

    def test_non_list_json_returns_empty(self) -> None:
        assert self._fn('{"key": "val"}') == []

    def test_list_items_coerced_to_str(self) -> None:
        assert self._fn([1, 2, 3]) == ["1", "2", "3"]

    def test_empty_list_returns_empty(self) -> None:
        assert self._fn([]) == []


# ---------------------------------------------------------------------------
# PostgresPrivateBackend.save() — failed_approaches persisted as JSONB
# ---------------------------------------------------------------------------


class _FakeCM:
    def __init__(self, obj: Any) -> None:
        self._obj = obj

    def __enter__(self) -> Any:
        return self._obj

    def __exit__(self, *args: Any) -> bool:
        return False


def _make_backend() -> tuple[Any, MagicMock]:
    from tapps_brain.postgres_private import PostgresPrivateBackend

    cur = MagicMock()
    cur.fetchmany.return_value = []
    cur.fetchone.return_value = (0,)

    conn = MagicMock()
    conn.cursor.return_value = _FakeCM(cur)

    cm = MagicMock()
    cm.get_connection.return_value = _FakeCM(conn)
    cm.project_context.return_value = _FakeCM(conn)
    cm.admin_context.return_value = _FakeCM(conn)

    backend = PostgresPrivateBackend(cm, project_id="proj-t731", agent_id="agent-t731")
    return backend, cur


class TestSaveFailedApproaches:
    def test_save_includes_failed_approaches_in_insert(self) -> None:
        from tapps_brain.models import MemoryEntry

        backend, cur = _make_backend()
        entry = MemoryEntry(
            key="test-key",
            value="test value",
            failed_approaches=["tried A", "tried B"],
        )
        backend.save(entry)
        assert cur.execute.called
        params = cur.execute.call_args[0][1]
        # failed_approaches JSONB is followed by status, stale_reason, stale_date, memory_class
        fa_json = params[-5]
        assert json.loads(fa_json) == ["tried A", "tried B"]

    def test_save_empty_failed_approaches(self) -> None:
        from tapps_brain.models import MemoryEntry

        backend, cur = _make_backend()
        entry = MemoryEntry(key="empty-key", value="val", failed_approaches=[])
        backend.save(entry)
        params = cur.execute.call_args[0][1]
        fa_json = params[-5]
        assert json.loads(fa_json) == []


# ---------------------------------------------------------------------------
# PostgresPrivateBackend._row_to_entry() — failed_approaches restored
# ---------------------------------------------------------------------------


class TestRowToEntryFailedApproaches:
    def _make_row(self, failed_approaches: Any = None) -> dict[str, Any]:
        from datetime import UTC, datetime

        now = datetime.now(tz=UTC)
        return {
            "key": "my-key",
            "value": "my value",
            "tier": "pattern",
            "confidence": 0.8,
            "source": "agent",
            "source_agent": "test-agent",
            "scope": "project",
            "agent_scope": "private",
            "memory_group": None,
            "tags": ["t1"],
            "created_at": now,
            "updated_at": now,
            "last_accessed": now,
            "access_count": 1,
            "useful_access_count": 0,
            "total_access_count": 0,
            "branch": None,
            "last_reinforced": None,
            "reinforce_count": 0,
            "contradicted": False,
            "contradiction_reason": None,
            "seeded_from": None,
            "valid_at": None,
            "invalid_at": None,
            "superseded_by": None,
            "valid_from": "",
            "valid_until": "",
            "source_session_id": "",
            "source_channel": "",
            "source_message_id": "",
            "triggered_by": "",
            "stability": 0.0,
            "difficulty": 0.0,
            "positive_feedback_count": 0.0,
            "negative_feedback_count": 0.0,
            "integrity_hash": None,
            "embedding_model_id": None,
            "temporal_sensitivity": None,
            "failed_approaches": failed_approaches,
        }

    def test_row_with_list_restores_approaches(self) -> None:
        from tapps_brain.postgres_private import PostgresPrivateBackend

        row = self._make_row(failed_approaches=["tried X", "tried Y"])
        entry = PostgresPrivateBackend._row_to_entry(row)
        assert entry.failed_approaches == ["tried X", "tried Y"]

    def test_row_with_json_string_restores_approaches(self) -> None:
        from tapps_brain.postgres_private import PostgresPrivateBackend

        row = self._make_row(failed_approaches='["from JSON string"]')
        entry = PostgresPrivateBackend._row_to_entry(row)
        assert entry.failed_approaches == ["from JSON string"]

    def test_row_with_none_restores_empty(self) -> None:
        from tapps_brain.postgres_private import PostgresPrivateBackend

        row = self._make_row(failed_approaches=None)
        entry = PostgresPrivateBackend._row_to_entry(row)
        assert entry.failed_approaches == []

    def test_row_missing_key_restores_empty(self) -> None:
        from tapps_brain.postgres_private import PostgresPrivateBackend

        row = self._make_row(failed_approaches=None)
        row.pop("failed_approaches")
        entry = PostgresPrivateBackend._row_to_entry(row)
        assert entry.failed_approaches == []


# ---------------------------------------------------------------------------
# memory_service.brain_recall — surfaces failed_approaches when non-empty
# ---------------------------------------------------------------------------


class TestBrainRecallSurfacesFailedApproaches:
    def _make_store(self, entries: list[Any]) -> MagicMock:
        store = MagicMock()
        store.search.return_value = entries
        return store

    def _make_entry(self, key: str, failed: list[str]) -> Any:
        from tapps_brain.models import MemoryEntry

        return MemoryEntry(key=key, value="v", failed_approaches=failed)

    def test_non_empty_failed_approaches_included_in_recall(self) -> None:
        from tapps_brain.services.memory_service import brain_recall

        entry = self._make_entry("key-a", ["approach 1", "approach 2"])
        store = self._make_store([entry])
        results = brain_recall(store, "proj", "agent", query="something")
        assert len(results) == 1
        assert results[0]["failed_approaches"] == ["approach 1", "approach 2"]

    def test_empty_failed_approaches_omitted_from_recall(self) -> None:
        from tapps_brain.services.memory_service import brain_recall

        entry = self._make_entry("key-b", [])
        store = self._make_store([entry])
        results = brain_recall(store, "proj", "agent", query="something")
        assert len(results) == 1
        assert "failed_approaches" not in results[0]

    def test_max_results_respected(self) -> None:
        from tapps_brain.services.memory_service import brain_recall

        entries = [self._make_entry(f"key-{i}", [f"approach {i}"]) for i in range(10)]
        store = self._make_store(entries)
        results = brain_recall(store, "proj", "agent", query="q", max_results=3)
        assert len(results) == 3

    def test_dict_entries_passed_through_unchanged(self) -> None:
        from tapps_brain.services.memory_service import brain_recall

        dict_entry = {"key": "x", "value": "v", "tier": "pattern", "confidence": 0.5}
        store = self._make_store([dict_entry])
        results = brain_recall(store, "proj", "agent", query="q")
        assert results[0] == dict_entry


# ---------------------------------------------------------------------------
# memory_service.brain_remember — accepts and forwards failed_approaches
# ---------------------------------------------------------------------------


class TestBrainRememberFailedApproaches:
    def _make_store(self, saved_key: str = "some-key") -> MagicMock:
        from tapps_brain.models import MemoryEntry

        store = MagicMock()
        entry = MemoryEntry(key=saved_key, value="some fact")
        store.save.return_value = entry
        return store

    def test_brain_remember_passes_failed_approaches_to_store(self) -> None:
        from tapps_brain.services.memory_service import brain_remember

        store = self._make_store()
        with patch("tapps_brain.agent_brain._content_key", return_value="k"):
            brain_remember(
                store,
                "proj",
                "agent",
                fact="some fact",
                failed_approaches=["tried X", "tried Y"],
            )
        call_kwargs = store.save.call_args[1]
        assert call_kwargs["failed_approaches"] == ["tried X", "tried Y"]

    def test_brain_remember_default_none_passed_to_store(self) -> None:
        from tapps_brain.services.memory_service import brain_remember

        store = self._make_store()
        with patch("tapps_brain.agent_brain._content_key", return_value="k"):
            brain_remember(store, "proj", "agent", fact="some fact")
        call_kwargs = store.save.call_args[1]
        assert call_kwargs.get("failed_approaches") is None

    def test_brain_remember_returns_saved_true(self) -> None:
        from tapps_brain.services.memory_service import brain_remember

        store = self._make_store("computed-key")
        with patch("tapps_brain.agent_brain._content_key", return_value="computed-key"):
            result = brain_remember(
                store,
                "proj",
                "agent",
                fact="the fact",
                failed_approaches=["dead end"],
            )
        assert result["saved"] is True
        assert result["key"] == "computed-key"
