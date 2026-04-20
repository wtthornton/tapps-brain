"""Tests for the split mcp_server tool-registration modules (TAP-605).

These tests verify that each ``register_*`` helper attaches the correct
set of tools to a fresh FastMCP instance, and that the operator gate
closure is invoked for operator-only tools.  The tests mock the store
and service layer so no Postgres is required.
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
    """Minimal :class:`ToolContext` that short-circuits every service call."""
    fake_store = MagicMock(name="store")
    fake_store._default_store = MagicMock(name="default_store")

    def _resolve_store_for_call(_call_agent_id: str) -> Any:
        return fake_store

    def _resolve_per_call_agent_id(call_val: str, *, default: str) -> str:
        return call_val.strip() if call_val and call_val.strip() else default

    def _hive_for_tools() -> tuple[Any, bool]:
        return MagicMock(name="hive"), False

    def _pid() -> str:
        return "test-project"

    # Default: operator gate is CLOSED.  Tests opting into operator tools
    # replace this with a lambda that no-ops (or an open gate).
    def _require_operator_enabled() -> None:
        raise RuntimeError("operator tools disabled")

    return ToolContext(
        store=fake_store,
        server_agent_id="test-agent",
        resolve_store_for_call=_resolve_store_for_call,
        hive_for_tools=_hive_for_tools,
        pid=_pid,
        require_operator_enabled=_require_operator_enabled,
        resolved_dir=Path("/tmp/test-project-root"),
        resolve_per_call_agent_id=_resolve_per_call_agent_id,
    )


@pytest.fixture
def mcp() -> Any:
    """Fresh FastMCP instance with no tools registered."""
    from mcp.server.fastmcp import FastMCP

    return FastMCP("test-tapps-brain")


def _tool_names(mcp: Any) -> set[str]:
    return {t.name for t in mcp._tool_manager.list_tools()}


# ---------------------------------------------------------------------------
# register_brain_tools — six Agent Brain tools
# ---------------------------------------------------------------------------


def test_register_brain_tools_registers_six_tools(mcp: Any, fake_ctx: ToolContext) -> None:
    from tapps_brain.mcp_server.tools_brain import register_brain_tools

    register_brain_tools(mcp, fake_ctx)
    names = _tool_names(mcp)
    expected = {
        "brain_remember",
        "brain_recall",
        "brain_forget",
        "brain_learn_success",
        "brain_learn_failure",
        "brain_status",
    }
    assert expected.issubset(names)


def test_brain_remember_happy_path(mcp: Any, fake_ctx: ToolContext) -> None:
    from tapps_brain.mcp_server.tools_brain import register_brain_tools

    with patch("tapps_brain.mcp_server.tools_brain.memory_service") as svc:
        svc.brain_remember.return_value = {"saved": True, "key": "foo"}
        register_brain_tools(mcp, fake_ctx)
        tool = next(t for t in mcp._tool_manager.list_tools() if t.name == "brain_remember")
        result = tool.fn(fact="hello world", tier="pattern")
        body = json.loads(result)
        assert body["saved"] is True
        svc.brain_remember.assert_called_once()
        kwargs = svc.brain_remember.call_args.kwargs
        assert kwargs["fact"] == "hello world"
        assert kwargs["tier"] == "pattern"


# ---------------------------------------------------------------------------
# register_memory_tools — core memory_* tools
# ---------------------------------------------------------------------------


def test_register_memory_tools_registers_core_set(mcp: Any, fake_ctx: ToolContext) -> None:
    from tapps_brain.mcp_server.tools_memory import register_memory_tools

    register_memory_tools(mcp, fake_ctx)
    names = _tool_names(mcp)
    for expected in [
        "memory_save",
        "memory_get",
        "memory_delete",
        "memory_search",
        "memory_list",
        "memory_list_groups",
        "memory_recall",
        "memory_reinforce",
        "memory_ingest",
        "memory_supersede",
        "memory_history",
        "memory_save_many",
        "memory_recall_many",
        "memory_reinforce_many",
        "memory_index_session",
        "memory_search_sessions",
        "memory_capture",
    ]:
        assert expected in names, f"missing tool {expected}"


def test_memory_get_happy_path(mcp: Any, fake_ctx: ToolContext) -> None:
    from tapps_brain.mcp_server.tools_memory import register_memory_tools

    with patch("tapps_brain.mcp_server.tools_memory.memory_service") as svc:
        svc.memory_get.return_value = {"key": "k", "value": "v"}
        register_memory_tools(mcp, fake_ctx)
        tool = next(t for t in mcp._tool_manager.list_tools() if t.name == "memory_get")
        body = json.loads(tool.fn(key="k"))
        assert body == {"key": "k", "value": "v"}


def test_register_knowledge_tools_registers_graph_and_tag_tools(
    mcp: Any, fake_ctx: ToolContext
) -> None:
    from tapps_brain.mcp_server.tools_memory import register_knowledge_tools

    register_knowledge_tools(mcp, fake_ctx)
    names = _tool_names(mcp)
    for expected in [
        "memory_relations",
        "memory_relations_get_batch",
        "memory_find_related",
        "memory_query_relations",
        "memory_audit",
        "memory_list_tags",
        "memory_update_tags",
        "memory_entries_by_tag",
    ]:
        assert expected in names, f"missing tool {expected}"


# ---------------------------------------------------------------------------
# register_feedback_tools — feedback + diagnostics + flywheel
# ---------------------------------------------------------------------------


def test_register_feedback_tools_registers_all(mcp: Any, fake_ctx: ToolContext) -> None:
    from tapps_brain.mcp_server.tools_feedback import register_feedback_tools

    register_feedback_tools(mcp, fake_ctx)
    names = _tool_names(mcp)
    for expected in [
        "feedback_rate",
        "feedback_gap",
        "feedback_issue",
        "feedback_record",
        "feedback_query",
        "diagnostics_report",
        "diagnostics_history",
        "flywheel_process",
        "flywheel_gaps",
        "flywheel_report",
        "flywheel_evaluate",
        "flywheel_hive_feedback",
    ]:
        assert expected in names, f"missing tool {expected}"


def test_flywheel_evaluate_requires_operator(mcp: Any, fake_ctx: ToolContext) -> None:
    """flywheel_evaluate must call the operator-enabled guard before executing."""
    from tapps_brain.mcp_server.tools_feedback import register_feedback_tools

    register_feedback_tools(mcp, fake_ctx)
    tool = next(t for t in mcp._tool_manager.list_tools() if t.name == "flywheel_evaluate")
    with pytest.raises(RuntimeError, match="operator tools disabled"):
        tool.fn(suite_path="/tmp/suite", k=5)


def test_flywheel_evaluate_runs_when_operator_enabled(
    mcp: Any, fake_ctx: ToolContext
) -> None:
    fake_ctx.require_operator_enabled = lambda: None  # type: ignore[method-assign]
    from tapps_brain.mcp_server.tools_feedback import register_feedback_tools

    with patch("tapps_brain.mcp_server.tools_feedback.flywheel_service") as svc:
        svc.flywheel_evaluate.return_value = {"ok": True}
        register_feedback_tools(mcp, fake_ctx)
        tool = next(t for t in mcp._tool_manager.list_tools() if t.name == "flywheel_evaluate")
        body = json.loads(tool.fn(suite_path="/tmp/s", k=3))
        assert body == {"ok": True}


# ---------------------------------------------------------------------------
# register_maintenance_tools — operator-gated
# ---------------------------------------------------------------------------


def test_register_maintenance_tools_registers_all(mcp: Any, fake_ctx: ToolContext) -> None:
    from tapps_brain.mcp_server.tools_maintenance import register_maintenance_tools

    register_maintenance_tools(mcp, fake_ctx)
    names = _tool_names(mcp)
    for expected in [
        "maintenance_consolidate",
        "maintenance_gc",
        "maintenance_stale",
        "tapps_brain_health",
        "memory_gc_config",
        "memory_gc_config_set",
        "memory_consolidation_config",
        "memory_consolidation_config_set",
        "memory_export",
        "memory_import",
        "tapps_brain_relay_export",
        "profile_info",
        "memory_profile_onboarding",
        "profile_switch",
        "tapps_brain_session_end",
    ]:
        assert expected in names, f"missing tool {expected}"


def test_maintenance_gc_requires_operator(mcp: Any, fake_ctx: ToolContext) -> None:
    from tapps_brain.mcp_server.tools_maintenance import register_maintenance_tools

    register_maintenance_tools(mcp, fake_ctx)
    tool = next(t for t in mcp._tool_manager.list_tools() if t.name == "maintenance_gc")
    with pytest.raises(RuntimeError, match="operator tools disabled"):
        tool.fn(dry_run=True)


def test_profile_info_no_operator_gate(mcp: Any, fake_ctx: ToolContext) -> None:
    """profile_info / onboarding / switch are NOT operator-gated."""
    from tapps_brain.mcp_server.tools_maintenance import register_maintenance_tools

    with patch("tapps_brain.mcp_server.tools_maintenance.profile_service") as svc:
        svc.profile_info.return_value = {"name": "repo-brain"}
        register_maintenance_tools(mcp, fake_ctx)
        tool = next(t for t in mcp._tool_manager.list_tools() if t.name == "profile_info")
        body = json.loads(tool.fn())
        assert body == {"name": "repo-brain"}


# ---------------------------------------------------------------------------
# register_hive_tools
# ---------------------------------------------------------------------------


def test_register_hive_tools_registers_six_tools(mcp: Any, fake_ctx: ToolContext) -> None:
    from tapps_brain.mcp_server.tools_hive import register_hive_tools

    register_hive_tools(mcp, fake_ctx)
    names = _tool_names(mcp)
    for expected in [
        "hive_status",
        "hive_search",
        "hive_propagate",
        "hive_push",
        "hive_write_revision",
        "hive_wait_write",
    ]:
        assert expected in names, f"missing tool {expected}"


def test_hive_search_happy_path(mcp: Any, fake_ctx: ToolContext) -> None:
    from tapps_brain.mcp_server.tools_hive import register_hive_tools

    with patch("tapps_brain.mcp_server.tools_hive.hive_service") as svc:
        svc.hive_search.return_value = {"matches": []}
        register_hive_tools(mcp, fake_ctx)
        tool = next(t for t in mcp._tool_manager.list_tools() if t.name == "hive_search")
        body = json.loads(tool.fn(query="foo", namespace=None))
        assert body == {"matches": []}


# ---------------------------------------------------------------------------
# register_agent_tools
# ---------------------------------------------------------------------------


def test_register_agent_tools_registers_four(mcp: Any, fake_ctx: ToolContext) -> None:
    from tapps_brain.mcp_server.tools_agents import register_agent_tools

    register_agent_tools(mcp, fake_ctx)
    names = _tool_names(mcp)
    for expected in ["agent_register", "agent_create", "agent_list", "agent_delete"]:
        assert expected in names, f"missing tool {expected}"


def test_agent_list_happy_path(mcp: Any, fake_ctx: ToolContext) -> None:
    from tapps_brain.mcp_server.tools_agents import register_agent_tools

    with patch("tapps_brain.mcp_server.tools_agents.agents_service") as svc:
        svc.agent_list.return_value = {"agents": []}
        register_agent_tools(mcp, fake_ctx)
        tool = next(t for t in mcp._tool_manager.list_tools() if t.name == "agent_list")
        body = json.loads(tool.fn())
        assert body == {"agents": []}


# ---------------------------------------------------------------------------
# register_resources_and_prompts
# ---------------------------------------------------------------------------


def test_register_resources_and_prompts_registers_resources(
    mcp: Any, fake_ctx: ToolContext
) -> None:
    from tapps_brain.mcp_server.tools_resources import register_resources_and_prompts

    register_resources_and_prompts(mcp, fake_ctx)
    # FastMCP exposes resources on _resource_manager.  We don't assert
    # exact URIs (API-compat fragile), just that registration didn't raise
    # and some resources landed.
    resources = mcp._resource_manager.list_resources()
    assert len(list(resources)) >= 1


# ---------------------------------------------------------------------------
# Public-API smoke — stable import paths (TAP-605 backward compat)
# ---------------------------------------------------------------------------


def test_public_imports_still_work() -> None:
    """``from tapps_brain.mcp_server import ...`` must keep working post-split."""
    from tapps_brain.mcp_server import (  # noqa: F401
        REQUEST_AGENT_ID,
        REQUEST_GROUP,
        REQUEST_PROFILE,
        REQUEST_PROJECT_ID,
        REQUEST_SCOPE,
        ToolContext,
        create_operator_server,
        create_server,
        main,
        main_operator,
    )


def test_tool_context_is_dataclass() -> None:
    """ToolContext must be a dataclass carrying the fields ``tools_*`` require."""
    import dataclasses

    assert dataclasses.is_dataclass(ToolContext)
    fields = {f.name for f in dataclasses.fields(ToolContext)}
    assert {
        "store",
        "server_agent_id",
        "resolve_store_for_call",
        "hive_for_tools",
        "pid",
        "require_operator_enabled",
        "resolved_dir",
        "resolve_per_call_agent_id",
    }.issubset(fields)
