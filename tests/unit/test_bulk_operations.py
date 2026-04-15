"""Unit tests for STORY-070.6 — Bulk operations.

Covers:
- memory_save_many / memory_recall_many / memory_reinforce_many service functions
- POST /v1/remember:batch HTTP endpoint
- POST /v1/recall:batch HTTP endpoint
- POST /v1/reinforce:batch HTTP endpoint
- MCP tools: memory_save_many, memory_recall_many, memory_reinforce_many
- Batch size limit enforcement (TAPPS_BRAIN_MAX_BATCH_SIZE)
"""

from __future__ import annotations

import json
import threading
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

import tapps_brain.http_adapter as _adapter_mod
from tapps_brain.http_adapter import (
    _service_version,
    _Settings,
    create_app,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _make_store() -> MagicMock:
    store = MagicMock()
    profile = MagicMock()
    profile.layer_names = ["architectural", "pattern", "procedural", "context"]
    store.profile = profile
    return store


def _make_settings(*, store: Any = None, dsn: str | None = None) -> _Settings:
    s = _Settings.__new__(_Settings)
    s.dsn = dsn
    s.auth_token = None
    s.admin_token = None
    s.allowed_origins = []
    s.version = _service_version()
    s.store = store
    s.snapshot_lock = threading.Lock()
    s.snapshot_cache = None
    s.snapshot_cache_at = 0.0
    return s


def _client_with_store(settings: _Settings) -> TestClient:
    mcp_dummy = MagicMock()
    mcp_dummy.session_manager = None
    with patch.object(_adapter_mod, "_settings", settings), \
         patch.object(_adapter_mod, "get_settings", return_value=settings):
        app = create_app(store=settings.store, mcp_server=mcp_dummy)
        return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Service-layer tests
# ---------------------------------------------------------------------------


class TestMemorySaveMany:
    """memory_save_many() service function."""

    def test_ac1_saves_multiple_entries(self) -> None:
        """AC-1: POST /v1/remember:batch saves all valid entries."""
        from tapps_brain.services.memory_service import memory_save_many

        store = _make_store()
        mock_entry = MagicMock(
            key="k1", tier=MagicMock(__str__=lambda s: "pattern"),
            confidence=0.8, memory_group=None,
        )
        store.save.return_value = mock_entry

        entries = [
            {"key": "k1", "value": "v1"},
            {"key": "k2", "value": "v2"},
        ]
        result = memory_save_many(store, "proj", "agent", entries=entries)

        assert result["saved_count"] == 2
        assert result["error_count"] == 0
        assert len(result["results"]) == 2
        assert store.save.call_count == 2

    def test_ac2_max_100_default_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-2: max 100 entries by default."""
        from tapps_brain.services.memory_service import memory_save_many

        monkeypatch.delenv("TAPPS_BRAIN_MAX_BATCH_SIZE", raising=False)
        store = _make_store()
        entries = [{"key": f"k{i}", "value": "v"} for i in range(101)]
        result = memory_save_many(store, "proj", "agent", entries=entries)

        assert result["error"] == "batch_too_large"
        assert result["limit"] == 100

    def test_ac8_configurable_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-8: TAPPS_BRAIN_MAX_BATCH_SIZE overrides default limit."""
        from tapps_brain.services.memory_service import memory_save_many

        monkeypatch.setenv("TAPPS_BRAIN_MAX_BATCH_SIZE", "5")
        store = _make_store()
        entries = [{"key": f"k{i}", "value": "v"} for i in range(6)]
        result = memory_save_many(store, "proj", "agent", entries=entries)

        assert result["error"] == "batch_too_large"
        assert result["limit"] == 5

    def test_ac6_partial_failure_returns_per_item_status(self) -> None:
        """AC-6: partial failure returns per-item status array."""
        from tapps_brain.services.memory_service import memory_save_many

        store = _make_store()
        good_entry = MagicMock(
            key="k1", tier=MagicMock(__str__=lambda s: "pattern"),
            confidence=0.8, memory_group=None,
        )
        store.save.return_value = good_entry

        entries = [
            {"key": "k1", "value": "v1"},
            {"key": "", "value": "v2"},  # bad — missing key
            {"key": "k3", "value": "v3"},
        ]
        result = memory_save_many(store, "proj", "agent", entries=entries)

        assert result["saved_count"] == 2
        assert result["error_count"] == 1
        assert result["results"][1]["error"] == "bad_entry"
        assert result["results"][0]["status"] == "saved"
        assert result["results"][2]["status"] == "saved"

    def test_non_dict_entry_returns_bad_entry(self) -> None:
        from tapps_brain.services.memory_service import memory_save_many

        store = _make_store()
        result = memory_save_many(store, "proj", "agent", entries=["not-a-dict"])
        assert result["error_count"] == 1
        assert result["results"][0]["error"] == "bad_entry"

    def test_empty_batch_succeeds(self) -> None:
        from tapps_brain.services.memory_service import memory_save_many

        store = _make_store()
        result = memory_save_many(store, "proj", "agent", entries=[])
        assert result["saved_count"] == 0
        assert result["error_count"] == 0


class TestMemoryRecallMany:
    """memory_recall_many() service function."""

    def test_ac3_recall_multiple_queries(self) -> None:
        """AC-3: GET /v1/recall:batch with queries list."""
        from tapps_brain.services.memory_service import memory_recall_many

        store = _make_store()
        mock_result = MagicMock(
            memory_section="mem", memory_count=1, token_count=5,
            recall_time_ms=10, truncated=False, memories=["m"],
            recall_diagnostics=None, quality_warning=None,
        )
        store.recall.return_value = mock_result

        result = memory_recall_many(store, "proj", "agent", queries=["q1", "q2"])

        assert result["query_count"] == 2
        assert len(result["results"]) == 2
        assert store.recall.call_count == 2

    def test_ac4_max_50_default_read_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-4: max 50 queries by default for reads."""
        from tapps_brain.services.memory_service import memory_recall_many

        monkeypatch.delenv("TAPPS_BRAIN_MAX_BATCH_SIZE", raising=False)
        store = _make_store()
        queries = [f"q{i}" for i in range(51)]
        result = memory_recall_many(store, "proj", "agent", queries=queries)

        assert result["error"] == "batch_too_large"
        assert result["limit"] == 50

    def test_empty_query_returns_bad_query(self) -> None:
        from tapps_brain.services.memory_service import memory_recall_many

        store = _make_store()
        result = memory_recall_many(store, "proj", "agent", queries=[""])
        assert result["results"][0]["error"] == "bad_query"

    def test_dict_query_with_group(self) -> None:
        from tapps_brain.services.memory_service import memory_recall_many

        store = _make_store()
        mock_result = MagicMock(
            memory_section="", memory_count=0, token_count=0,
            recall_time_ms=1, truncated=False, memories=[],
            recall_diagnostics=None, quality_warning=None,
        )
        store.recall.return_value = mock_result

        result = memory_recall_many(
            store, "proj", "agent",
            queries=[{"message": "my query", "group": "g1"}],
        )
        assert result["query_count"] == 1
        store.recall.assert_called_once_with("my query", memory_group="g1")

    def test_preserves_query_order(self) -> None:
        from tapps_brain.services.memory_service import memory_recall_many

        store = _make_store()
        call_order: list[str] = []

        def fake_recall(msg: str, **kw: Any) -> Any:
            call_order.append(msg)
            r = MagicMock(
                memory_section=f"mem-{msg}", memory_count=1, token_count=1,
                recall_time_ms=1, truncated=False, memories=[msg],
                recall_diagnostics=None, quality_warning=None,
            )
            return r

        store.recall.side_effect = fake_recall
        result = memory_recall_many(
            store, "proj", "agent", queries=["q3", "q1", "q2"]
        )
        assert call_order == ["q3", "q1", "q2"]
        assert result["results"][0]["memory_section"] == "mem-q3"


class TestMemoryReinforceMany:
    """memory_reinforce_many() service function."""

    def test_ac5_reinforce_multiple_entries(self) -> None:
        """AC-5: POST /v1/reinforce:batch reinforces all valid entries."""
        from tapps_brain.services.memory_service import memory_reinforce_many

        store = _make_store()
        mock_entry = MagicMock(key="k1", confidence=0.9, access_count=3)
        store.reinforce.return_value = mock_entry

        entries = [{"key": "k1"}, {"key": "k2"}]
        result = memory_reinforce_many(store, "proj", "agent", entries=entries)

        assert result["reinforced_count"] == 2
        assert result["error_count"] == 0
        assert store.reinforce.call_count == 2

    def test_partial_failure_not_found(self) -> None:
        from tapps_brain.services.memory_service import memory_reinforce_many

        store = _make_store()
        good = MagicMock(key="k1", confidence=0.9, access_count=1)
        store.reinforce.side_effect = [good, KeyError("k2")]

        entries = [{"key": "k1"}, {"key": "k2"}]
        result = memory_reinforce_many(store, "proj", "agent", entries=entries)

        assert result["reinforced_count"] == 1
        assert result["error_count"] == 1
        assert result["results"][1]["error"] == "not_found"

    def test_missing_key_returns_bad_entry(self) -> None:
        from tapps_brain.services.memory_service import memory_reinforce_many

        store = _make_store()
        result = memory_reinforce_many(store, "proj", "agent", entries=[{"key": ""}])
        assert result["error_count"] == 1
        assert result["results"][0]["error"] == "bad_entry"


# ---------------------------------------------------------------------------
# HTTP adapter tests
# ---------------------------------------------------------------------------


class TestV1RememberBatch:
    """HTTP POST /v1/remember:batch (STORY-070.6)."""

    def _setup(self) -> tuple[TestClient, MagicMock]:
        store = _make_store()
        store.save.return_value = MagicMock(
            key="k1", tier=MagicMock(__str__=lambda s: "pattern"),
            confidence=0.8, memory_group=None,
        )
        settings = _make_settings(store=store)
        return _client_with_store(settings), store

    def test_ac1_post_v1rememberbatch_entries(self) -> None:
        """AC-1: POST /v1/remember:batch saves entries and returns per-item results."""
        client, _ = self._setup()
        with client:
            resp = client.post(
                "/v1/remember:batch",
                headers={"x-project-id": "proj"},
                json={"entries": [{"key": "k1", "value": "v1"}, {"key": "k2", "value": "v2"}]},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "results" in body
        assert body["saved_count"] == 2
        assert body["error_count"] == 0

    def test_missing_project_id_returns_400(self) -> None:
        client, _ = self._setup()
        with client:
            resp = client.post("/v1/remember:batch", json={"entries": []})
        assert resp.status_code == 400
        assert resp.json()["error"] == "bad_request"

    def test_missing_entries_field_returns_400(self) -> None:
        client, _ = self._setup()
        with client:
            resp = client.post(
                "/v1/remember:batch",
                headers={"x-project-id": "proj"},
                json={"wrong_key": []},
            )
        assert resp.status_code == 400

    def test_no_store_returns_503(self) -> None:
        settings = _make_settings(store=None)
        client = _client_with_store(settings)
        with client:
            resp = client.post(
                "/v1/remember:batch",
                headers={"x-project-id": "proj"},
                json={"entries": [{"key": "k", "value": "v"}]},
            )
        assert resp.status_code == 503

    def test_ac2_over_limit_returns_400(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-2: exceeding max 100 entries returns 400 with batch_too_large error."""
        monkeypatch.delenv("TAPPS_BRAIN_MAX_BATCH_SIZE", raising=False)
        client, _ = self._setup()
        entries = [{"key": f"k{i}", "value": "v"} for i in range(101)]
        with client:
            resp = client.post(
                "/v1/remember:batch",
                headers={"x-project-id": "proj"},
                json={"entries": entries},
            )
        assert resp.status_code == 400
        assert resp.json()["error"] == "batch_too_large"


class TestV1RecallBatch:
    """HTTP POST /v1/recall:batch (STORY-070.6)."""

    def _setup(self) -> tuple[TestClient, MagicMock]:
        store = _make_store()
        store.recall.return_value = MagicMock(
            memory_section="mem", memory_count=1, token_count=5,
            recall_time_ms=10, truncated=False, memories=["m"],
            recall_diagnostics=None, quality_warning=None,
        )
        settings = _make_settings(store=store)
        return _client_with_store(settings), store

    def test_ac3_post_v1recallbatch_queries(self) -> None:
        """AC-3: POST /v1/recall:batch recalls for each query."""
        client, _ = self._setup()
        with client:
            resp = client.post(
                "/v1/recall:batch",
                headers={"x-project-id": "proj"},
                json={"queries": ["query one", "query two"]},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "results" in body
        assert body["query_count"] == 2
        assert len(body["results"]) == 2

    def test_missing_project_id_returns_400(self) -> None:
        client, _ = self._setup()
        with client:
            resp = client.post("/v1/recall:batch", json={"queries": ["q"]})
        assert resp.status_code == 400

    def test_missing_queries_field_returns_400(self) -> None:
        client, _ = self._setup()
        with client:
            resp = client.post(
                "/v1/recall:batch",
                headers={"x-project-id": "proj"},
                json={"wrong": []},
            )
        assert resp.status_code == 400

    def test_ac4_over_limit_returns_400(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-4: exceeding max 50 queries returns 400 with batch_too_large error."""
        monkeypatch.delenv("TAPPS_BRAIN_MAX_BATCH_SIZE", raising=False)
        client, _ = self._setup()
        queries = [f"q{i}" for i in range(51)]
        with client:
            resp = client.post(
                "/v1/recall:batch",
                headers={"x-project-id": "proj"},
                json={"queries": queries},
            )
        assert resp.status_code == 400
        assert resp.json()["error"] == "batch_too_large"

    def test_no_store_returns_503(self) -> None:
        settings = _make_settings(store=None)
        client = _client_with_store(settings)
        with client:
            resp = client.post(
                "/v1/recall:batch",
                headers={"x-project-id": "proj"},
                json={"queries": ["q"]},
            )
        assert resp.status_code == 503


class TestV1ReinforceBatch:
    """HTTP POST /v1/reinforce:batch (STORY-070.6)."""

    def _setup(self) -> tuple[TestClient, MagicMock]:
        store = _make_store()
        store.reinforce.return_value = MagicMock(key="k1", confidence=0.9, access_count=2)
        settings = _make_settings(store=store)
        return _client_with_store(settings), store

    def test_ac5_post_v1reinforcebatch(self) -> None:
        """AC-5: POST /v1/reinforce:batch reinforces all valid entries."""
        client, _ = self._setup()
        with client:
            resp = client.post(
                "/v1/reinforce:batch",
                headers={"x-project-id": "proj"},
                json={"entries": [{"key": "k1"}, {"key": "k2"}]},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["reinforced_count"] == 2
        assert body["error_count"] == 0

    def test_missing_project_id_returns_400(self) -> None:
        client, _ = self._setup()
        with client:
            resp = client.post("/v1/reinforce:batch", json={"entries": []})
        assert resp.status_code == 400

    def test_missing_entries_field_returns_400(self) -> None:
        client, _ = self._setup()
        with client:
            resp = client.post(
                "/v1/reinforce:batch",
                headers={"x-project-id": "proj"},
                json={"wrong": []},
            )
        assert resp.status_code == 400

    def test_no_store_returns_503(self) -> None:
        settings = _make_settings(store=None)
        client = _client_with_store(settings)
        with client:
            resp = client.post(
                "/v1/reinforce:batch",
                headers={"x-project-id": "proj"},
                json={"entries": [{"key": "k"}]},
            )
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# MCP server tool tests
# ---------------------------------------------------------------------------


pytestmark_mcp = pytest.mark.requires_mcp


class TestMcpBulkTools:
    """AC-7: MCP tools memory_save_many / memory_recall_many / memory_reinforce_many."""

    @pytest.mark.requires_mcp
    def test_memory_save_many_tool_registered(self, tmp_path: Any) -> None:
        from tapps_brain.mcp_server import create_server

        server = create_server(tmp_path, enable_hive=False)
        tool_names = [t.name for t in server._tool_manager.list_tools()]
        assert "memory_save_many" in tool_names
        server._tapps_store.close()

    @pytest.mark.requires_mcp
    def test_memory_recall_many_tool_registered(self, tmp_path: Any) -> None:
        from tapps_brain.mcp_server import create_server

        server = create_server(tmp_path, enable_hive=False)
        tool_names = [t.name for t in server._tool_manager.list_tools()]
        assert "memory_recall_many" in tool_names
        server._tapps_store.close()

    @pytest.mark.requires_mcp
    def test_memory_reinforce_many_tool_registered(self, tmp_path: Any) -> None:
        from tapps_brain.mcp_server import create_server

        server = create_server(tmp_path, enable_hive=False)
        tool_names = [t.name for t in server._tool_manager.list_tools()]
        assert "memory_reinforce_many" in tool_names
        server._tapps_store.close()

    @pytest.mark.requires_mcp
    def test_memory_save_many_saves_entries(self, tmp_path: Any) -> None:
        from tapps_brain.mcp_server import create_server

        server = create_server(tmp_path, enable_hive=False)
        store = server._tapps_store

        # Find the tool
        fn = None
        for tool in server._tool_manager.list_tools():
            if tool.name == "memory_save_many":
                fn = tool.fn
                break
        assert fn is not None

        entries = [{"key": "bulk-k1", "value": "v1"}, {"key": "bulk-k2", "value": "v2"}]
        raw = fn(entries=entries)
        result = json.loads(raw)
        assert result["saved_count"] == 2
        assert result["error_count"] == 0

        store.close()

    @pytest.mark.requires_mcp
    def test_memory_recall_many_returns_results(self, tmp_path: Any) -> None:
        from tapps_brain.mcp_server import create_server

        server = create_server(tmp_path, enable_hive=False)
        store = server._tapps_store
        store.save(key="recall-test", value="hello world", tier="pattern")

        fn = None
        for tool in server._tool_manager.list_tools():
            if tool.name == "memory_recall_many":
                fn = tool.fn
                break
        assert fn is not None

        raw = fn(queries=["hello"])
        result = json.loads(raw)
        assert result["query_count"] == 1
        assert len(result["results"]) == 1

        store.close()

    @pytest.mark.requires_mcp
    def test_memory_reinforce_many_reinforces_entries(self, tmp_path: Any) -> None:
        from tapps_brain.mcp_server import create_server

        server = create_server(tmp_path, enable_hive=False)
        store = server._tapps_store
        store.save(key="reinforce-test", value="value", tier="pattern")

        fn = None
        for tool in server._tool_manager.list_tools():
            if tool.name == "memory_reinforce_many":
                fn = tool.fn
                break
        assert fn is not None

        raw = fn(entries=[{"key": "reinforce-test", "confidence_boost": 0.1}])
        result = json.loads(raw)
        assert result["reinforced_count"] == 1
        assert result["error_count"] == 0

        store.close()
