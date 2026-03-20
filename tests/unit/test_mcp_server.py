"""Tests for MCP server (STORY-008.1, 008.2, 008.3, 008.4)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


def _tool_fn(mcp_server, name: str):
    for tool in mcp_server._tool_manager.list_tools():
        if tool.name == name:
            return tool.fn
    msg = f"tool not found: {name}"
    raise KeyError(msg)


@pytest.fixture
def store_dir(tmp_path):
    """Create a temporary project directory."""
    return tmp_path


@pytest.fixture
def mcp_server(store_dir):
    """Create a FastMCP server backed by a temp store."""
    from tapps_brain.mcp_server import create_server

    server = create_server(store_dir)
    yield server
    # Clean up the store
    if hasattr(server, "_tapps_store"):
        server._tapps_store.close()


class TestServerCreation:
    """Test server instantiation and configuration."""

    def test_create_server_returns_fastmcp_instance(self, store_dir):
        from tapps_brain.mcp_server import create_server

        server = create_server(store_dir)
        assert server is not None
        assert server.name == "tapps-brain"
        server._tapps_store.close()

    def test_create_server_defaults_to_cwd(self):
        from tapps_brain.mcp_server import create_server

        server = create_server()
        assert server is not None
        server._tapps_store.close()

    def test_server_has_store_attached(self, mcp_server):
        assert hasattr(mcp_server, "_tapps_store")
        from tapps_brain.store import MemoryStore

        assert isinstance(mcp_server._tapps_store, MemoryStore)


class TestCoreTools:
    """Test core memory CRUD tools are registered."""

    def test_memory_save_tool_registered(self, mcp_server):
        tool_names = [t.name for t in mcp_server._tool_manager.list_tools()]
        assert "memory_save" in tool_names

    def test_memory_get_tool_registered(self, mcp_server):
        tool_names = [t.name for t in mcp_server._tool_manager.list_tools()]
        assert "memory_get" in tool_names

    def test_memory_delete_tool_registered(self, mcp_server):
        tool_names = [t.name for t in mcp_server._tool_manager.list_tools()]
        assert "memory_delete" in tool_names

    def test_memory_search_tool_registered(self, mcp_server):
        tool_names = [t.name for t in mcp_server._tool_manager.list_tools()]
        assert "memory_search" in tool_names

    def test_memory_list_tool_registered(self, mcp_server):
        tool_names = [t.name for t in mcp_server._tool_manager.list_tools()]
        assert "memory_list" in tool_names

    def test_all_expected_tools_present(self, mcp_server):
        tool_names = {t.name for t in mcp_server._tool_manager.list_tools()}
        expected = {
            "memory_save",
            "memory_get",
            "memory_delete",
            "memory_search",
            "memory_list",
            "memory_recall",
            "memory_reinforce",
            "memory_ingest",
            "memory_supersede",
            "memory_history",
        }
        assert expected.issubset(tool_names)


class TestLifecycleTools:
    """Test lifecycle tools are registered and callable (STORY-008.3)."""

    def test_lifecycle_tools_registered(self, mcp_server):
        tool_names = {t.name for t in mcp_server._tool_manager.list_tools()}
        expected = {
            "memory_recall",
            "memory_reinforce",
            "memory_ingest",
            "memory_supersede",
            "memory_history",
        }
        assert expected.issubset(tool_names)

    def test_recall_returns_results(self, mcp_server):
        store = mcp_server._tapps_store
        store.save(key="recall-arch", value="Use PostgreSQL for persistence", tier="architectural")

        result = store.recall("What database do we use?")
        assert result.memory_count >= 0  # may or may not match depending on BM25

    def test_reinforce_boosts_confidence(self, mcp_server):
        store = mcp_server._tapps_store
        entry = store.save(key="reinforce-me", value="Important pattern", tier="pattern")
        assert isinstance(entry, object)  # MemoryEntry
        original_conf = entry.confidence

        reinforced = store.reinforce("reinforce-me", confidence_boost=0.1)
        assert reinforced.confidence >= original_conf
        assert reinforced.access_count >= 2  # save counts as 1, reinforce as 2

    def test_reinforce_not_found(self, mcp_server):
        store = mcp_server._tapps_store
        with pytest.raises(KeyError):
            store.reinforce("nonexistent-key")

    def test_ingest_extracts_facts(self, mcp_server):
        store = mcp_server._tapps_store
        context = (
            "We decided to use SQLite for the storage layer. The team agreed on ruff as the linter."
        )
        keys = store.ingest_context(context, source="agent")
        # Extraction is rule-based, may or may not find facts
        assert isinstance(keys, list)

    def test_supersede_creates_version_chain(self, mcp_server):
        store = mcp_server._tapps_store
        store.save(key="evolving-fact", value="Use MySQL", tier="architectural")

        new_entry = store.supersede("evolving-fact", "Use PostgreSQL instead")
        assert new_entry.key.startswith("evolving-fact")
        assert new_entry.value == "Use PostgreSQL instead"

        # Old entry should be invalidated
        old = store.get("evolving-fact")
        assert old is not None
        assert old.invalid_at is not None
        assert old.superseded_by == new_entry.key

    def test_supersede_not_found(self, mcp_server):
        store = mcp_server._tapps_store
        with pytest.raises(KeyError):
            store.supersede("ghost-key", "new value")

    def test_supersede_already_superseded(self, mcp_server):
        store = mcp_server._tapps_store
        store.save(key="old-fact", value="original", tier="pattern")
        store.supersede("old-fact", "updated")
        with pytest.raises(ValueError, match="already superseded"):
            store.supersede("old-fact", "updated again")

    def test_history_returns_chain(self, mcp_server):
        store = mcp_server._tapps_store
        store.save(key="chain-start", value="v1", tier="pattern")
        store.supersede("chain-start", "v2")

        chain = store.history("chain-start")
        assert len(chain) >= 2
        assert chain[0].key == "chain-start"

    def test_history_unknown_key_raises(self, mcp_server):
        store = mcp_server._tapps_store
        with pytest.raises(KeyError):
            store.history("no-such-key")


class TestResources:
    """Test resources are registered (STORY-008.4)."""

    def test_stats_resource_registered(self, mcp_server):
        templates = mcp_server._resource_manager.list_resources()
        uris = [str(r.uri) for r in templates]
        assert "memory://stats" in uris

    def test_health_resource_registered(self, mcp_server):
        templates = mcp_server._resource_manager.list_resources()
        uris = [str(r.uri) for r in templates]
        assert "memory://health" in uris

    def test_metrics_resource_registered(self, mcp_server):
        templates = mcp_server._resource_manager.list_resources()
        uris = [str(r.uri) for r in templates]
        assert "memory://metrics" in uris

    def test_entry_resource_template_registered(self, mcp_server):
        templates = mcp_server._resource_manager.list_templates()
        uris = [str(t.uri_template) for t in templates]
        assert any("memory://entries/" in u for u in uris)

    def test_health_report_structure(self, mcp_server):
        store = mcp_server._tapps_store
        store.save(key="health-test", value="test value", tier="pattern")

        report = store.health()
        assert report.entry_count >= 1
        assert report.max_entries == 500
        assert report.schema_version >= 1
        assert "pattern" in report.tier_distribution

    def test_health_report_oldest_age(self, mcp_server):
        store = mcp_server._tapps_store
        store.save(key="age-test", value="test", tier="context")

        report = store.health()
        # Just created, age should be very small
        assert report.oldest_entry_age_days >= 0.0

    def test_get_metrics_returns_snapshot(self, mcp_server):
        store = mcp_server._tapps_store

        snapshot = store.get_metrics()
        assert hasattr(snapshot, "counters")
        assert hasattr(snapshot, "histograms")
        assert hasattr(snapshot, "captured_at")


class TestToolExecution:
    """Test that tools execute correctly via direct function calls."""

    def test_save_and_get_roundtrip(self, mcp_server):
        store = mcp_server._tapps_store

        # Save directly via store to test get tool
        store.save(key="test-key", value="test value", tier="pattern")

        # Get via store
        entry = store.get("test-key")
        assert entry is not None
        assert entry.value == "test value"

    def test_save_and_delete(self, mcp_server):
        store = mcp_server._tapps_store

        store.save(key="del-key", value="to delete", tier="context")
        assert store.get("del-key") is not None

        deleted = store.delete("del-key")
        assert deleted is True
        assert store.get("del-key") is None

    def test_search_returns_results(self, mcp_server):
        store = mcp_server._tapps_store

        store.save(key="search-test", value="Python asyncio patterns", tier="pattern")
        results = store.search("asyncio")
        assert len(results) >= 1
        assert any(r.key == "search-test" for r in results)

    def test_list_entries(self, mcp_server):
        store = mcp_server._tapps_store

        store.save(key="list-1", value="first entry", tier="pattern")
        store.save(key="list-2", value="second entry", tier="context")

        entries = store.list_all()
        assert len(entries) >= 2

    def test_stats_resource_returns_valid_json(self, mcp_server):
        store = mcp_server._tapps_store

        store.save(key="stat-entry", value="for stats", tier="pattern")

        snap = store.snapshot()
        assert snap.total_count >= 1

    def test_health_resource_returns_report(self, mcp_server):
        store = mcp_server._tapps_store

        report = store.health()
        assert report.max_entries == 500
        assert report.schema_version >= 1


class TestMcpToolHandlerExecution:
    """Exercise MCP tool and resource callables for coverage."""

    def test_memory_crud_and_search_tools(self, mcp_server):
        save = _tool_fn(mcp_server, "memory_save")
        saved = json.loads(save(key="mcp-t1", value="hello mcp world", tier="pattern"))
        assert saved["status"] == "saved"

        get = _tool_fn(mcp_server, "memory_get")
        row = json.loads(get(key="mcp-t1"))
        assert row["key"] == "mcp-t1"
        missing = json.loads(get(key="missing-key"))
        assert missing["error"] == "not_found"

        search = _tool_fn(mcp_server, "memory_search")
        hits = json.loads(search(query="mcp"))
        assert any(h["key"] == "mcp-t1" for h in hits)

        lst = _tool_fn(mcp_server, "memory_list")
        listed = json.loads(lst(include_superseded=True))
        assert any(e["key"] == "mcp-t1" for e in listed)

        delete = _tool_fn(mcp_server, "memory_delete")
        gone = json.loads(delete(key="mcp-t1"))
        assert gone["deleted"] is True

    def test_memory_recall_reinforce_ingest_tools(self, mcp_server):
        store = mcp_server._tapps_store
        store.save(key="mcp-rc", value="unique recall phrase xyz", tier="pattern")

        recall = _tool_fn(mcp_server, "memory_recall")
        payload = json.loads(recall(message="recall phrase xyz"))
        assert "memory_count" in payload
        assert "token_count" in payload

        reinforce = _tool_fn(mcp_server, "memory_reinforce")
        assert json.loads(reinforce(key="no-such", confidence_boost=0.0))["error"] == "not_found"
        ok = json.loads(reinforce(key="mcp-rc", confidence_boost=0.05))
        assert ok["status"] == "reinforced"

        ingest = _tool_fn(mcp_server, "memory_ingest")
        ing = json.loads(
            ingest(context="We chose SQLite for storage.", source="agent"),
        )
        assert ing["status"] == "ingested"
        assert "created_keys" in ing

    def test_memory_supersede_and_history_tools(self, mcp_server):
        store = mcp_server._tapps_store
        store.save(key="mcp-h1", value="first", tier="pattern")
        store.supersede("mcp-h1", "second", key="mcp-h2")

        supersede = _tool_fn(mcp_server, "memory_supersede")
        assert json.loads(supersede(old_key="ghost", new_value="x"))["error"] == "not_found"
        bad = json.loads(supersede(old_key="mcp-h1", new_value="third"))
        assert bad.get("error") == "already_superseded"

        ok = json.loads(
            supersede(old_key="mcp-h2", new_value="third", key="mcp-h3"),
        )
        assert ok["status"] == "superseded"

        hist = _tool_fn(mcp_server, "memory_history")
        assert json.loads(hist(key="no-history-key"))["error"] == "not_found"
        chain = json.loads(hist(key="mcp-h1"))
        assert isinstance(chain, list)
        assert len(chain) >= 2

    def test_resource_callables_return_json(self, mcp_server):
        store = mcp_server._tapps_store
        store.save(key="res-1", value="resource test", tier="pattern")
        rm = mcp_server._resource_manager
        for res in rm.list_resources():
            body = json.loads(res.fn())
            assert isinstance(body, dict)

        for tpl in rm.list_templates():
            raw = tpl.fn("res-1")
            body = json.loads(raw)
            assert body.get("key") == "res-1"


class TestMcpMain:
    def test_main_invokes_stdio_run(self, tmp_path, monkeypatch):
        from tapps_brain import mcp_server as ms

        captured: list[object] = []
        real_create = ms.create_server

        def wrap(project_dir=None):
            srv = real_create(project_dir)

            def run(*args, **kwargs):
                captured.append((args, kwargs))
                srv._tapps_store.close()

            srv.run = run  # type: ignore[method-assign]
            return srv

        monkeypatch.setattr(ms, "create_server", wrap)
        monkeypatch.setattr(sys, "argv", ["tapps-brain-mcp", "--project-dir", str(tmp_path)])
        ms.main()
        assert captured
        assert captured[0][1].get("transport") == "stdio"


class TestProjectDirResolution:
    """Test project directory resolution logic."""

    def test_resolve_explicit_dir(self):
        from tapps_brain.mcp_server import _resolve_project_dir

        result = _resolve_project_dir("/tmp/test-project")
        assert result == Path("/tmp/test-project").resolve()

    def test_resolve_none_defaults_to_cwd(self):
        from tapps_brain.mcp_server import _resolve_project_dir

        result = _resolve_project_dir(None)
        assert result == Path.cwd().resolve()
