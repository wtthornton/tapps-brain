"""TAP-2725: brain_resolve_entity MCP tool — unit tests.

Covers:
- Happy path: UUID returned for a resolved entity
- Idempotency: two calls with the same (type, name) return the same UUID
- Alias hit: alias-matched entity returns its canonical UUID
- DB unavailable: structured error envelope
- Missing required params: structured bad_request error
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("mcp", reason="MCP extra not installed")

from tapps_brain.mcp_server.context import ToolContext


@pytest.fixture
def fake_ctx() -> ToolContext:
    fake_store = MagicMock(name="store")

    def _resolve_store_for_call(_call_agent_id: str) -> Any:
        return fake_store

    def _resolve_per_call_agent_id(call_val: str, *, default: str) -> str:
        return call_val.strip() if call_val and call_val.strip() else default

    def _hive_for_tools() -> tuple[Any, bool]:
        return MagicMock(name="hive"), False

    def _pid() -> str:
        return "test-project"

    return ToolContext(
        store=fake_store,
        server_agent_id="test-agent",
        resolve_store_for_call=_resolve_store_for_call,
        hive_for_tools=_hive_for_tools,
        pid=_pid,
        require_operator_enabled=lambda: None,
        resolved_dir=Path("/tmp/test-project-root"),
        resolve_per_call_agent_id=_resolve_per_call_agent_id,
    )


@pytest.fixture
def mcp() -> Any:
    from mcp.server.fastmcp import FastMCP

    return FastMCP("test-tapps-brain-resolve-entity")


def _get_tool(mcp: Any, name: str) -> Any:
    return next(t for t in mcp._tool_manager.list_tools() if t.name == name)


class TestBrainResolveEntity:
    def test_returns_entity_id_string(self, mcp: Any, fake_ctx: ToolContext) -> None:
        """Happy path: resolved entity returns its UUID."""
        from tapps_brain.mcp_server.tools_kg import register_kg_tools

        uuid = "11111111-1111-1111-1111-111111111111"
        with patch("tapps_brain.mcp_server.tools_kg.kg_service") as svc:
            svc._get_or_create_cm.return_value = MagicMock(name="cm")
            svc._DEFAULT_BRAIN_ID = "brain"
            svc.resolve_entity.return_value = {
                "entity_id": uuid,
                "entity_type": "module",
                "canonical_name": "auth",
                "created": False,
                "confidence": 0.9,
                "reason": "exact_match",
            }
            register_kg_tools(mcp, fake_ctx)
            tool = _get_tool(mcp, "brain_resolve_entity")
            body = json.loads(tool.fn(entity_type="module", canonical_name="auth"))

        assert body["entity_id"] == uuid
        assert body["entity_type"] == "module"
        assert body["canonical_name"] == "auth"
        svc.resolve_entity.assert_called_once()

    def test_create_then_resolve_idempotency(self, mcp: Any, fake_ctx: ToolContext) -> None:
        """Calling twice with the same (entity_type, canonical_name) returns the same UUID."""
        from tapps_brain.mcp_server.tools_kg import register_kg_tools

        fixed_uuid = "22222222-2222-2222-2222-222222222222"

        with patch("tapps_brain.mcp_server.tools_kg.kg_service") as svc:
            svc._get_or_create_cm.return_value = MagicMock(name="cm")
            svc._DEFAULT_BRAIN_ID = "brain"
            svc.resolve_entity.return_value = {
                "entity_id": fixed_uuid,
                "entity_type": "tool",
                "canonical_name": "search",
                "created": True,
                "confidence": 0.6,
                "reason": "created",
            }
            register_kg_tools(mcp, fake_ctx)
            tool = _get_tool(mcp, "brain_resolve_entity")

            body1 = json.loads(tool.fn(entity_type="tool", canonical_name="search"))
            body2 = json.loads(tool.fn(entity_type="tool", canonical_name="search"))

        assert body1["entity_id"] == fixed_uuid
        assert body2["entity_id"] == fixed_uuid
        # Service layer is the authority on idempotency; tool delegates each call.
        assert svc.resolve_entity.call_count == 2
        assert svc.resolve_entity.call_args_list[0] == svc.resolve_entity.call_args_list[1]

    def test_alias_hit_returns_canonical_uuid(self, mcp: Any, fake_ctx: ToolContext) -> None:
        """Alias-matched entity returns the canonical entity's UUID with reason=alias_match."""
        from tapps_brain.mcp_server.tools_kg import register_kg_tools

        canonical_uuid = "33333333-3333-3333-3333-333333333333"

        with patch("tapps_brain.mcp_server.tools_kg.kg_service") as svc:
            svc._get_or_create_cm.return_value = MagicMock(name="cm")
            svc._DEFAULT_BRAIN_ID = "brain"
            svc.resolve_entity.return_value = {
                "entity_id": canonical_uuid,
                "entity_type": "module",
                "canonical_name": "auth_module",
                "created": False,
                "confidence": 0.8,
                "reason": "alias_match",
            }
            register_kg_tools(mcp, fake_ctx)
            tool = _get_tool(mcp, "brain_resolve_entity")
            # Caller passes alias "auth"; service resolves to canonical entity.
            body = json.loads(tool.fn(entity_type="module", canonical_name="auth"))

        assert body["entity_id"] == canonical_uuid
        assert body["reason"] == "alias_match"

    def test_db_unavailable_returns_structured_error(
        self, mcp: Any, fake_ctx: ToolContext
    ) -> None:
        """When no DB is configured the tool returns a structured error and skips the service."""
        from tapps_brain.mcp_server.tools_kg import register_kg_tools

        with patch("tapps_brain.mcp_server.tools_kg.kg_service") as svc:
            svc._get_or_create_cm.return_value = None
            svc._DEFAULT_BRAIN_ID = "brain"
            register_kg_tools(mcp, fake_ctx)
            tool = _get_tool(mcp, "brain_resolve_entity")
            body = json.loads(tool.fn(entity_type="module", canonical_name="auth"))

        assert body["error"] == "db_unavailable"
        svc.resolve_entity.assert_not_called()

    def test_empty_entity_type_returns_bad_request(
        self, mcp: Any, fake_ctx: ToolContext
    ) -> None:
        """Missing entity_type short-circuits before any DB call."""
        from tapps_brain.mcp_server.tools_kg import register_kg_tools

        with patch("tapps_brain.mcp_server.tools_kg.kg_service") as svc:
            svc._get_or_create_cm.return_value = MagicMock(name="cm")
            svc._DEFAULT_BRAIN_ID = "brain"
            register_kg_tools(mcp, fake_ctx)
            tool = _get_tool(mcp, "brain_resolve_entity")
            body = json.loads(tool.fn(entity_type="", canonical_name="auth"))

        assert body["error"] == "bad_request"
        assert "entity_type" in body["detail"]
        svc.resolve_entity.assert_not_called()

    def test_empty_canonical_name_returns_bad_request(
        self, mcp: Any, fake_ctx: ToolContext
    ) -> None:
        """Missing canonical_name short-circuits before any DB call."""
        from tapps_brain.mcp_server.tools_kg import register_kg_tools

        with patch("tapps_brain.mcp_server.tools_kg.kg_service") as svc:
            svc._get_or_create_cm.return_value = MagicMock(name="cm")
            svc._DEFAULT_BRAIN_ID = "brain"
            register_kg_tools(mcp, fake_ctx)
            tool = _get_tool(mcp, "brain_resolve_entity")
            body = json.loads(tool.fn(entity_type="module", canonical_name=""))

        assert body["error"] == "bad_request"
        assert "canonical_name" in body["detail"]
        svc.resolve_entity.assert_not_called()

    def test_delegates_to_kg_service_resolve_entity(
        self, mcp: Any, fake_ctx: ToolContext
    ) -> None:
        """Tool passes entity_type and canonical_name through to kg_service.resolve_entity."""
        from tapps_brain.mcp_server.tools_kg import register_kg_tools

        with patch("tapps_brain.mcp_server.tools_kg.kg_service") as svc:
            svc._get_or_create_cm.return_value = MagicMock(name="cm")
            svc._DEFAULT_BRAIN_ID = "brain-1"
            svc.resolve_entity.return_value = {
                "entity_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "entity_type": "agent",
                "canonical_name": "ralph",
                "created": False,
                "confidence": 1.0,
                "reason": "exact_match",
            }
            register_kg_tools(mcp, fake_ctx)
            tool = _get_tool(mcp, "brain_resolve_entity")
            json.loads(tool.fn(entity_type="agent", canonical_name="ralph"))

        svc.resolve_entity.assert_called_once_with(
            svc._get_or_create_cm.return_value,
            "test-project",
            "brain-1",
            entity_type="agent",
            canonical_name="ralph",
        )
