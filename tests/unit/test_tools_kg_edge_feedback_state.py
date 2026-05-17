"""TAP-1975: brain_record_feedback edge response surfaces post-update state.

The MCP wrapper used to nest the post-update edge state under ``kg_update``
only; agents that emitted ``edge_misleading`` had to drill into the nested
dict or issue a follow-up ``brain_get_neighbors`` to learn whether the edge
had crossed their retry threshold.

These tests pin the new behaviour: the response surfaces ``confidence``,
``helpful_count``, ``misleading_count`` (and ``flagged_for_review`` when
present) at the top level for the edge feedback path, while the nested
``kg_update`` dict is preserved for back-compat.  The memory feedback path
is unchanged.
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

    return FastMCP("test-tap-1975")


def _get_tool(mcp: Any, name: str) -> Any:
    return next(t for t in mcp._tool_manager.list_tools() if t.name == name)


class TestEdgeFeedbackResponseShape:
    def test_edge_helpful_response_includes_confidence_and_counters(
        self, mcp: Any, fake_ctx: ToolContext
    ) -> None:
        from tapps_brain.mcp_server.tools_kg import register_kg_tools

        with patch("tapps_brain.mcp_server.tools_kg.kg_service") as svc:
            svc.record_kg_feedback.return_value = {
                "status": "recorded",
                "kg_update": {
                    "applied": True,
                    "feedback_type": "edge_helpful",
                    "edge_id": "11111111-1111-1111-1111-111111111111",
                    "positive_feedback_count": 4.0,
                    "negative_feedback_count": 1.0,
                    "confidence": 0.85,
                },
            }
            register_kg_tools(mcp, fake_ctx)
            tool = _get_tool(mcp, "brain_record_feedback")
            body = json.loads(
                tool.fn(
                    feedback_type="edge_helpful",
                    edge_id="11111111-1111-1111-1111-111111111111",
                )
            )

        assert body["recorded"] is True
        assert body["feedback_type"] == "edge_helpful"
        assert body["edge_id"] == "11111111-1111-1111-1111-111111111111"
        # TAP-1975: post-update state at the top level.
        assert body["confidence"] == 0.85
        assert body["helpful_count"] == 4
        assert body["misleading_count"] == 1
        # Back-compat: nested kg_update still present.
        assert body["kg_update"]["positive_feedback_count"] == 4.0

    def test_edge_misleading_response_includes_flagged_for_review(
        self, mcp: Any, fake_ctx: ToolContext
    ) -> None:
        from tapps_brain.mcp_server.tools_kg import register_kg_tools

        with patch("tapps_brain.mcp_server.tools_kg.kg_service") as svc:
            svc.record_kg_feedback.return_value = {
                "status": "recorded",
                "kg_update": {
                    "applied": True,
                    "feedback_type": "edge_misleading",
                    "edge_id": "22222222-2222-2222-2222-222222222222",
                    "positive_feedback_count": 1.0,
                    "negative_feedback_count": 5.0,
                    "confidence": 0.42,
                    "flagged_for_review": True,
                },
            }
            register_kg_tools(mcp, fake_ctx)
            tool = _get_tool(mcp, "brain_record_feedback")
            body = json.loads(
                tool.fn(
                    feedback_type="edge_misleading",
                    edge_id="22222222-2222-2222-2222-222222222222",
                    utility_score=-0.8,
                )
            )

        assert body["confidence"] == 0.42
        assert body["helpful_count"] == 1
        assert body["misleading_count"] == 5
        assert body["flagged_for_review"] is True

    def test_edge_helpful_response_when_kg_update_skipped(
        self, mcp: Any, fake_ctx: ToolContext
    ) -> None:
        """When the KG update is skipped (no DB), no top-level state fields surface."""
        from tapps_brain.mcp_server.tools_kg import register_kg_tools

        with patch("tapps_brain.mcp_server.tools_kg.kg_service") as svc:
            svc.record_kg_feedback.return_value = {
                "status": "recorded",
                "kg_update": "skipped_no_db",
            }
            register_kg_tools(mcp, fake_ctx)
            tool = _get_tool(mcp, "brain_record_feedback")
            body = json.loads(
                tool.fn(
                    feedback_type="edge_helpful",
                    edge_id="33333333-3333-3333-3333-333333333333",
                )
            )

        assert body["recorded"] is True
        # No top-level confidence / counter fields when kg_update is a string.
        assert "confidence" not in body
        assert "helpful_count" not in body
        assert "misleading_count" not in body

    def test_edge_helpful_response_when_edge_not_found(
        self, mcp: Any, fake_ctx: ToolContext
    ) -> None:
        """`applied=False` (edge_not_found) must not surface stale counters."""
        from tapps_brain.mcp_server.tools_kg import register_kg_tools

        with patch("tapps_brain.mcp_server.tools_kg.kg_service") as svc:
            svc.record_kg_feedback.return_value = {
                "status": "recorded",
                "kg_update": {
                    "applied": False,
                    "reason": "edge_not_found",
                    "edge_id": "44444444-4444-4444-4444-444444444444",
                },
            }
            register_kg_tools(mcp, fake_ctx)
            tool = _get_tool(mcp, "brain_record_feedback")
            body = json.loads(
                tool.fn(
                    feedback_type="edge_helpful",
                    edge_id="44444444-4444-4444-4444-444444444444",
                )
            )

        assert body["recorded"] is True
        # Don't fabricate state on a no-op write.
        assert "confidence" not in body
        assert "helpful_count" not in body
        assert "misleading_count" not in body
        assert body["kg_update"]["applied"] is False

    def test_memory_feedback_path_unaffected(self, mcp: Any, fake_ctx: ToolContext) -> None:
        """The memory feedback path must NOT grow the TAP-1975 fields."""
        from tapps_brain.mcp_server.tools_kg import register_kg_tools

        with (
            patch("tapps_brain.mcp_server.tools_kg.kg_service"),
            patch("tapps_brain.services.feedback_service.feedback_record") as fb_record,
        ):
            fb_record.return_value = {"status": "recorded", "event": {}}
            register_kg_tools(mcp, fake_ctx)
            tool = _get_tool(mcp, "brain_record_feedback")
            body = json.loads(
                tool.fn(
                    feedback_type="recall_rated",
                    entry_key="some-key",
                )
            )

        assert body["recorded"] is True
        assert body["entry_key"] == "some-key"
        assert "confidence" not in body
        assert "helpful_count" not in body
        assert "misleading_count" not in body
