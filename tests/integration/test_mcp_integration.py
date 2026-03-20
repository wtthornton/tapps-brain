"""MCP protocol-level integration tests (STORY-008.7).

Uses the MCP SDK's in-memory transport to run a real client-server pair,
verifying the full protocol flow: initialize, tools/list, tools/call,
resources/list, resources/read, prompts/list, prompts/get, and error handling.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import AnyUrl

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def mcp_server(project_dir: Path):
    from tapps_brain.mcp_server import create_server

    server = create_server(project_dir)
    yield server
    if hasattr(server, "_tapps_store"):
        server._tapps_store.close()


# ------------------------------------------------------------------
# Tools discovery
# ------------------------------------------------------------------


class TestToolsDiscovery:
    """Verify tools/list returns all expected tools via the MCP protocol."""

    async def test_list_tools_returns_all_expected(self, mcp_server):
        async with create_connected_server_and_client_session(mcp_server) as session:
            result = await session.list_tools()
            tool_names = {t.name for t in result.tools}
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
                "federation_status",
                "federation_subscribe",
                "federation_unsubscribe",
                "federation_publish",
                "maintenance_consolidate",
                "maintenance_gc",
                "memory_export",
                "memory_import",
            }
            assert expected.issubset(tool_names)

    async def test_tools_have_descriptions(self, mcp_server):
        async with create_connected_server_and_client_session(mcp_server) as session:
            result = await session.list_tools()
            for tool in result.tools:
                assert tool.description, f"Tool {tool.name} missing description"

    async def test_tools_have_input_schemas(self, mcp_server):
        async with create_connected_server_and_client_session(mcp_server) as session:
            result = await session.list_tools()
            for tool in result.tools:
                assert tool.inputSchema is not None, f"Tool {tool.name} missing inputSchema"


# ------------------------------------------------------------------
# Core CRUD tools via tools/call
# ------------------------------------------------------------------


class TestCoreCrudTools:
    """Test CRUD tool execution through the MCP protocol."""

    async def test_save_and_get_roundtrip(self, mcp_server):
        async with create_connected_server_and_client_session(mcp_server) as session:
            # Save
            save_result = await session.call_tool(
                "memory_save",
                {"key": "int-test-1", "value": "integration test value", "tier": "pattern"},
            )
            assert not save_result.isError
            saved = json.loads(save_result.content[0].text)
            assert saved["status"] == "saved"
            assert saved["key"] == "int-test-1"

            # Get
            get_result = await session.call_tool("memory_get", {"key": "int-test-1"})
            assert not get_result.isError
            entry = json.loads(get_result.content[0].text)
            assert entry["key"] == "int-test-1"
            assert entry["value"] == "integration test value"

    async def test_get_not_found(self, mcp_server):
        async with create_connected_server_and_client_session(mcp_server) as session:
            result = await session.call_tool("memory_get", {"key": "nonexistent"})
            body = json.loads(result.content[0].text)
            assert body["error"] == "not_found"

    async def test_delete(self, mcp_server):
        async with create_connected_server_and_client_session(mcp_server) as session:
            await session.call_tool(
                "memory_save", {"key": "del-me", "value": "to delete", "tier": "context"}
            )
            del_result = await session.call_tool("memory_delete", {"key": "del-me"})
            body = json.loads(del_result.content[0].text)
            assert body["deleted"] is True

            # Confirm deleted
            get_result = await session.call_tool("memory_get", {"key": "del-me"})
            body = json.loads(get_result.content[0].text)
            assert body["error"] == "not_found"

    async def test_search(self, mcp_server):
        async with create_connected_server_and_client_session(mcp_server) as session:
            await session.call_tool(
                "memory_save",
                {"key": "search-int", "value": "Python asyncio patterns", "tier": "pattern"},
            )
            result = await session.call_tool("memory_search", {"query": "asyncio"})
            hits = json.loads(result.content[0].text)
            assert isinstance(hits, list)
            assert any(h["key"] == "search-int" for h in hits)

    async def test_list(self, mcp_server):
        async with create_connected_server_and_client_session(mcp_server) as session:
            await session.call_tool(
                "memory_save", {"key": "list-a", "value": "first", "tier": "pattern"}
            )
            await session.call_tool(
                "memory_save", {"key": "list-b", "value": "second", "tier": "context"}
            )
            result = await session.call_tool("memory_list", {})
            entries = json.loads(result.content[0].text)
            keys = {e["key"] for e in entries}
            assert {"list-a", "list-b"}.issubset(keys)


# ------------------------------------------------------------------
# Lifecycle tools via tools/call
# ------------------------------------------------------------------


class TestLifecycleTools:
    """Test recall, reinforce, ingest, supersede, history through MCP protocol."""

    async def test_recall(self, mcp_server):
        async with create_connected_server_and_client_session(mcp_server) as session:
            await session.call_tool(
                "memory_save",
                {
                    "key": "recall-db",
                    "value": "Use PostgreSQL for persistence",
                    "tier": "architectural",
                },
            )
            result = await session.call_tool("memory_recall", {"message": "What database?"})
            body = json.loads(result.content[0].text)
            assert "memory_count" in body
            assert "token_count" in body

    async def test_reinforce(self, mcp_server):
        async with create_connected_server_and_client_session(mcp_server) as session:
            await session.call_tool(
                "memory_save", {"key": "boost-me", "value": "important fact", "tier": "pattern"}
            )
            result = await session.call_tool(
                "memory_reinforce", {"key": "boost-me", "confidence_boost": 0.1}
            )
            body = json.loads(result.content[0].text)
            assert body["status"] == "reinforced"
            assert body["access_count"] >= 2

    async def test_reinforce_not_found(self, mcp_server):
        async with create_connected_server_and_client_session(mcp_server) as session:
            result = await session.call_tool("memory_reinforce", {"key": "ghost"})
            body = json.loads(result.content[0].text)
            assert body["error"] == "not_found"

    async def test_ingest(self, mcp_server):
        async with create_connected_server_and_client_session(mcp_server) as session:
            result = await session.call_tool(
                "memory_ingest",
                {"context": "We decided to use SQLite for storage.", "source": "agent"},
            )
            body = json.loads(result.content[0].text)
            assert body["status"] == "ingested"
            assert isinstance(body["created_keys"], list)

    async def test_supersede_and_history(self, mcp_server):
        async with create_connected_server_and_client_session(mcp_server) as session:
            # Create original
            await session.call_tool(
                "memory_save", {"key": "evolve", "value": "Use MySQL", "tier": "architectural"}
            )
            # Supersede
            sup_result = await session.call_tool(
                "memory_supersede",
                {"old_key": "evolve", "new_value": "Use PostgreSQL", "key": "evolve-v2"},
            )
            body = json.loads(sup_result.content[0].text)
            assert body["status"] == "superseded"
            assert body["new_key"] == "evolve-v2"

            # History
            hist_result = await session.call_tool("memory_history", {"key": "evolve"})
            chain = json.loads(hist_result.content[0].text)
            assert isinstance(chain, list)
            assert len(chain) >= 2

    async def test_supersede_not_found(self, mcp_server):
        async with create_connected_server_and_client_session(mcp_server) as session:
            result = await session.call_tool(
                "memory_supersede", {"old_key": "nope", "new_value": "x"}
            )
            body = json.loads(result.content[0].text)
            assert body["error"] == "not_found"

    async def test_supersede_already_superseded(self, mcp_server):
        async with create_connected_server_and_client_session(mcp_server) as session:
            await session.call_tool(
                "memory_save", {"key": "old-one", "value": "v1", "tier": "pattern"}
            )
            await session.call_tool("memory_supersede", {"old_key": "old-one", "new_value": "v2"})
            result = await session.call_tool(
                "memory_supersede", {"old_key": "old-one", "new_value": "v3"}
            )
            body = json.loads(result.content[0].text)
            assert body["error"] == "already_superseded"

    async def test_history_not_found(self, mcp_server):
        async with create_connected_server_and_client_session(mcp_server) as session:
            result = await session.call_tool("memory_history", {"key": "no-such"})
            body = json.loads(result.content[0].text)
            assert body["error"] == "not_found"


# ------------------------------------------------------------------
# Resources via protocol
# ------------------------------------------------------------------


class TestResources:
    """Test resources/list and resources/read through the MCP protocol."""

    async def test_list_resources(self, mcp_server):
        async with create_connected_server_and_client_session(mcp_server) as session:
            result = await session.list_resources()
            uris = {str(r.uri) for r in result.resources}
            assert "memory://stats" in uris
            assert "memory://health" in uris
            assert "memory://metrics" in uris

    async def test_read_stats_resource(self, mcp_server):
        async with create_connected_server_and_client_session(mcp_server) as session:
            result = await session.read_resource(AnyUrl("memory://stats"))
            body = json.loads(result.contents[0].text)
            assert "total_entries" in body
            assert "tier_distribution" in body
            assert body["max_entries"] == 500

    async def test_read_health_resource(self, mcp_server):
        async with create_connected_server_and_client_session(mcp_server) as session:
            result = await session.read_resource(AnyUrl("memory://health"))
            body = json.loads(result.contents[0].text)
            assert "entry_count" in body
            assert "max_entries" in body

    async def test_read_metrics_resource(self, mcp_server):
        async with create_connected_server_and_client_session(mcp_server) as session:
            result = await session.read_resource(AnyUrl("memory://metrics"))
            body = json.loads(result.contents[0].text)
            assert "counters" in body
            assert "histograms" in body

    async def test_entry_resource_template(self, mcp_server):
        async with create_connected_server_and_client_session(mcp_server) as session:
            # Save an entry first
            await session.call_tool(
                "memory_save", {"key": "res-entry", "value": "resource test", "tier": "pattern"}
            )
            result = await session.read_resource(AnyUrl("memory://entries/res-entry"))
            body = json.loads(result.contents[0].text)
            assert body["key"] == "res-entry"
            assert body["value"] == "resource test"

    async def test_entry_resource_not_found(self, mcp_server):
        async with create_connected_server_and_client_session(mcp_server) as session:
            result = await session.read_resource(AnyUrl("memory://entries/missing-key"))
            body = json.loads(result.contents[0].text)
            assert body["error"] == "not_found"

    async def test_resource_templates_listed(self, mcp_server):
        async with create_connected_server_and_client_session(mcp_server) as session:
            result = await session.list_resource_templates()
            uris = [str(t.uriTemplate) for t in result.resourceTemplates]
            assert any("memory://entries/" in u for u in uris)


# ------------------------------------------------------------------
# Prompts via protocol
# ------------------------------------------------------------------


class TestPrompts:
    """Test prompts/list and prompts/get through the MCP protocol."""

    async def test_list_prompts(self, mcp_server):
        async with create_connected_server_and_client_session(mcp_server) as session:
            result = await session.list_prompts()
            prompt_names = {p.name for p in result.prompts}
            assert {"recall", "store_summary", "remember"}.issubset(prompt_names)

    async def test_prompts_have_descriptions(self, mcp_server):
        async with create_connected_server_and_client_session(mcp_server) as session:
            result = await session.list_prompts()
            for prompt in result.prompts:
                assert prompt.description, f"Prompt {prompt.name} missing description"

    async def test_recall_prompt(self, mcp_server):
        async with create_connected_server_and_client_session(mcp_server) as session:
            result = await session.get_prompt("recall", {"topic": "testing"})
            assert len(result.messages) == 1
            assert result.messages[0].role == "user"
            assert "testing" in result.messages[0].content.text

    async def test_store_summary_prompt(self, mcp_server):
        async with create_connected_server_and_client_session(mcp_server) as session:
            result = await session.get_prompt("store_summary", {})
            assert len(result.messages) == 1
            assert result.messages[0].role == "user"
            assert "Total entries" in result.messages[0].content.text

    async def test_remember_prompt(self, mcp_server):
        async with create_connected_server_and_client_session(mcp_server) as session:
            result = await session.get_prompt("remember", {"fact": "Use ruff for linting"})
            assert len(result.messages) == 1
            content = result.messages[0].content.text
            assert "ruff for linting" in content
            assert "memory_save" in content


# ------------------------------------------------------------------
# Maintenance and federation tools via protocol
# ------------------------------------------------------------------


class TestMaintenanceTools:
    """Test maintenance tools through the MCP protocol."""

    async def test_consolidate(self, mcp_server):
        async with create_connected_server_and_client_session(mcp_server) as session:
            result = await session.call_tool("maintenance_consolidate", {})
            body = json.loads(result.content[0].text)
            assert "scanned" in body
            assert "groups_found" in body

    async def test_gc_dry_run(self, mcp_server):
        async with create_connected_server_and_client_session(mcp_server) as session:
            await session.call_tool(
                "memory_save", {"key": "gc-int", "value": "gc test", "tier": "context"}
            )
            result = await session.call_tool("maintenance_gc", {"dry_run": True})
            body = json.loads(result.content[0].text)
            assert body["dry_run"] is True

    async def test_gc_run(self, mcp_server):
        async with create_connected_server_and_client_session(mcp_server) as session:
            result = await session.call_tool("maintenance_gc", {"dry_run": False})
            body = json.loads(result.content[0].text)
            assert "archived_count" in body


class TestExportImportTools:
    """Test export/import tools through the MCP protocol."""

    async def test_export(self, mcp_server):
        async with create_connected_server_and_client_session(mcp_server) as session:
            await session.call_tool(
                "memory_save", {"key": "exp-1", "value": "export me", "tier": "pattern"}
            )
            result = await session.call_tool("memory_export", {})
            body = json.loads(result.content[0].text)
            assert body["entry_count"] >= 1
            assert any(m["key"] == "exp-1" for m in body["memories"])

    async def test_import(self, mcp_server):
        async with create_connected_server_and_client_session(mcp_server) as session:
            payload = json.dumps(
                {"memories": [{"key": "imp-1", "value": "imported", "tier": "pattern"}]}
            )
            result = await session.call_tool("memory_import", {"memories_json": payload})
            body = json.loads(result.content[0].text)
            assert body["imported"] == 1

            # Verify via get
            get_result = await session.call_tool("memory_get", {"key": "imp-1"})
            entry = json.loads(get_result.content[0].text)
            assert entry["value"] == "imported"

    async def test_import_invalid_json(self, mcp_server):
        async with create_connected_server_and_client_session(mcp_server) as session:
            result = await session.call_tool("memory_import", {"memories_json": "not json"})
            body = json.loads(result.content[0].text)
            assert body["error"] == "invalid_json"


class TestFederationTools:
    """Test federation tools through the MCP protocol."""

    async def test_federation_status(self, mcp_server, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "tapps_brain.federation._DEFAULT_HUB_DIR", tmp_path / ".tapps-brain" / "memory"
        )
        async with create_connected_server_and_client_session(mcp_server) as session:
            result = await session.call_tool("federation_status", {})
            body = json.loads(result.content[0].text)
            assert "projects" in body
            assert "hub_stats" in body

    async def test_federation_subscribe_and_unsubscribe(self, mcp_server, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "tapps_brain.federation._DEFAULT_HUB_DIR", tmp_path / ".tapps-brain" / "memory"
        )
        async with create_connected_server_and_client_session(mcp_server) as session:
            sub = await session.call_tool("federation_subscribe", {"project_id": "test-proj"})
            sub_body = json.loads(sub.content[0].text)
            assert sub_body["status"] == "subscribed"

            unsub = await session.call_tool("federation_unsubscribe", {"project_id": "test-proj"})
            unsub_body = json.loads(unsub.content[0].text)
            assert unsub_body["status"] == "unsubscribed"


# ------------------------------------------------------------------
# Error handling
# ------------------------------------------------------------------


class TestErrorHandling:
    """Test error conditions through the MCP protocol."""

    async def test_call_nonexistent_tool_returns_error(self, mcp_server):
        async with create_connected_server_and_client_session(mcp_server) as session:
            result = await session.call_tool("nonexistent_tool", {})
            assert result.isError

    async def test_full_crud_workflow(self, mcp_server):
        """End-to-end workflow: save, search, reinforce, supersede, history, delete."""
        async with create_connected_server_and_client_session(mcp_server) as session:
            # 1. Save
            r = await session.call_tool(
                "memory_save",
                {
                    "key": "workflow-key",
                    "value": "Architecture decision: use SQLite",
                    "tier": "architectural",
                    "tags": ["database", "storage"],
                },
            )
            assert json.loads(r.content[0].text)["status"] == "saved"

            # 2. Search
            r = await session.call_tool("memory_search", {"query": "SQLite"})
            hits = json.loads(r.content[0].text)
            assert any(h["key"] == "workflow-key" for h in hits)

            # 3. Reinforce
            r = await session.call_tool(
                "memory_reinforce", {"key": "workflow-key", "confidence_boost": 0.05}
            )
            assert json.loads(r.content[0].text)["status"] == "reinforced"

            # 4. Supersede
            r = await session.call_tool(
                "memory_supersede",
                {
                    "old_key": "workflow-key",
                    "new_value": "Architecture decision: use PostgreSQL",
                    "key": "workflow-key-v2",
                },
            )
            body = json.loads(r.content[0].text)
            assert body["status"] == "superseded"
            assert body["new_key"] == "workflow-key-v2"

            # 5. History
            r = await session.call_tool("memory_history", {"key": "workflow-key"})
            chain = json.loads(r.content[0].text)
            assert len(chain) >= 2

            # 6. Delete new version
            r = await session.call_tool("memory_delete", {"key": "workflow-key-v2"})
            assert json.loads(r.content[0].text)["deleted"] is True
