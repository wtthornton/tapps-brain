"""TAP-1973: ``brain_record_events_batch`` MCP tool — per-event tx semantics.

The new tool is a sibling of ``brain_record_event`` for N-event backfill.
Unlike the REST ``/v1/experience:batch`` endpoint (TAP-1934 — single-tx,
all-or-nothing), each event in this MCP batch runs in its own Postgres
transaction so partial success is allowed.

These tests pin the contract:

* Malformed ``events_json`` returns the EPIC-300 ``bad_json`` envelope.
* Empty / non-array / too-large payloads return ``bad_request`` /
  ``too_many_events``.
* Each event is delegated to ``record_event``; per-event errors land in
  ``failed`` while successful events land in ``succeeded`` — the whole call
  reports both.
* The service-layer ``record_events_batch_per_event_tx`` function is the
  canonical owner of the per-event-tx loop.
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

    return FastMCP("test-tap-1973")


def _get_tool(mcp: Any, name: str) -> Any:
    return next(t for t in mcp._tool_manager.list_tools() if t.name == name)


# ---------------------------------------------------------------------------
# Service-layer: record_events_batch_per_event_tx
# ---------------------------------------------------------------------------


class TestServiceBatchPerEventTx:
    """Direct tests of the service-layer per-event-tx loop (no MCP plumbing)."""

    def test_rejects_non_list(self) -> None:
        from tapps_brain.services.kg_service import record_events_batch_per_event_tx

        out = record_events_batch_per_event_tx(
            MagicMock(),
            "proj",
            "brain",
            "agent",
            events={"not": "a list"},  # type: ignore[arg-type]
        )
        assert out == {"error": "bad_request", "detail": "events must be a JSON array."}

    def test_rejects_empty_list(self) -> None:
        from tapps_brain.services.kg_service import record_events_batch_per_event_tx

        out = record_events_batch_per_event_tx(MagicMock(), "proj", "brain", "agent", events=[])
        assert out["error"] == "bad_request"

    def test_rejects_oversize_batch(self) -> None:
        from tapps_brain.services.kg_service import record_events_batch_per_event_tx

        oversize = [{"event_type": "x"}] * 201
        out = record_events_batch_per_event_tx(
            MagicMock(), "proj", "brain", "agent", events=oversize
        )
        assert out["error"] == "too_many_events"
        assert "201" in out["detail"]
        assert "200" in out["detail"]

    def test_partial_success_reports_both_succeeded_and_failed(self) -> None:
        from tapps_brain.services import kg_service

        events = [
            {"event_type": "workflow_completed"},
            {"event_type": "  "},  # invalid — empty event_type
            {"event_type": "tool_called"},
            {"not_a_dict": True},  # invalid shape (becomes a dict in JSON but missing event_type)
        ]
        # event_idx 3 IS a dict (just missing event_type), so it fails pre-validate.
        # Patch record_event to succeed for the two well-formed entries.
        with patch.object(kg_service, "record_event") as rec:
            rec.side_effect = [
                {"event_id": "evt-0"},
                {"event_id": "evt-2"},
            ]
            out = kg_service.record_events_batch_per_event_tx(
                MagicMock(), "proj", "brain", "agent", events=events
            )
        assert out["count"] == 4
        assert out["succeeded_count"] == 2
        assert out["failed_count"] == 2
        # Successes carry the original index for caller correlation.
        idxs = sorted(s["index"] for s in out["succeeded"])
        assert idxs == [0, 2]
        fail_idxs = sorted(f["index"] for f in out["failed"])
        assert fail_idxs == [1, 3]
        assert rec.call_count == 2

    def test_record_event_exception_lands_in_failed(self) -> None:
        from tapps_brain.services import kg_service

        events = [{"event_type": "workflow_completed"}, {"event_type": "tool_called"}]
        with patch.object(kg_service, "record_event") as rec:
            rec.side_effect = [
                {"event_id": "evt-0"},
                RuntimeError("boom"),
            ]
            out = kg_service.record_events_batch_per_event_tx(
                MagicMock(), "proj", "brain", "agent", events=events
            )
        assert out["succeeded_count"] == 1
        assert out["failed_count"] == 1
        assert out["failed"][0] == {"index": 1, "error": "RuntimeError", "detail": "boom"}

    def test_record_event_returning_error_dict_lands_in_failed(self) -> None:
        from tapps_brain.services import kg_service

        events = [{"event_type": "workflow_completed"}]
        with patch.object(kg_service, "record_event") as rec:
            rec.return_value = {"error": "validation_error", "detail": "bad spec"}
            out = kg_service.record_events_batch_per_event_tx(
                MagicMock(), "proj", "brain", "agent", events=events
            )
        assert out["succeeded_count"] == 0
        assert out["failed_count"] == 1
        assert out["failed"][0] == {
            "index": 0,
            "error": "validation_error",
            "detail": "bad spec",
        }


# ---------------------------------------------------------------------------
# MCP wrapper: brain_record_events_batch
# ---------------------------------------------------------------------------


class TestMcpBrainRecordEventsBatch:
    def test_bad_json_envelope_on_malformed_events_json(
        self, mcp: Any, fake_ctx: ToolContext
    ) -> None:
        from tapps_brain.mcp_server.tools_kg import register_kg_tools

        with patch("tapps_brain.mcp_server.tools_kg.kg_service") as svc:
            register_kg_tools(mcp, fake_ctx)
            tool = _get_tool(mcp, "brain_record_events_batch")
            body = json.loads(tool.fn(events_json="[bad json"))
        assert body["error"] == "bad_json"
        assert body["field"] == "events_json"
        assert body["detail"]
        svc.record_events_batch_per_event_tx.assert_not_called()

    def test_bad_json_envelope_on_non_array(self, mcp: Any, fake_ctx: ToolContext) -> None:
        from tapps_brain.mcp_server.tools_kg import register_kg_tools

        with patch("tapps_brain.mcp_server.tools_kg.kg_service") as svc:
            register_kg_tools(mcp, fake_ctx)
            tool = _get_tool(mcp, "brain_record_events_batch")
            body = json.loads(tool.fn(events_json='{"not": "an array"}'))
        assert body == {
            "error": "bad_json",
            "field": "events_json",
            "detail": "expected JSON array, got dict",
        }
        svc.record_events_batch_per_event_tx.assert_not_called()

    def test_empty_events_json_returns_bad_request(self, mcp: Any, fake_ctx: ToolContext) -> None:
        from tapps_brain.mcp_server.tools_kg import register_kg_tools

        with patch("tapps_brain.mcp_server.tools_kg.kg_service"):
            register_kg_tools(mcp, fake_ctx)
            tool = _get_tool(mcp, "brain_record_events_batch")
            body = json.loads(tool.fn(events_json=""))
        assert body == {"error": "bad_request", "detail": "events_json is required."}

    def test_happy_path_delegates_to_service(self, mcp: Any, fake_ctx: ToolContext) -> None:
        from tapps_brain.mcp_server.tools_kg import register_kg_tools

        with patch("tapps_brain.mcp_server.tools_kg.kg_service") as svc:
            svc._get_or_create_cm.return_value = MagicMock(name="cm")
            svc._DEFAULT_BRAIN_ID = "brain"
            svc.record_events_batch_per_event_tx.return_value = {
                "succeeded": [{"index": 0, "result": {"event_id": "evt-0"}}],
                "failed": [],
                "count": 1,
                "succeeded_count": 1,
                "failed_count": 0,
            }
            register_kg_tools(mcp, fake_ctx)
            tool = _get_tool(mcp, "brain_record_events_batch")
            body = json.loads(tool.fn(events_json='[{"event_type": "workflow_completed"}]'))
        assert body["succeeded_count"] == 1
        assert body["failed_count"] == 0
        # The wrapper passes the parsed list straight to the service.
        called_events = svc.record_events_batch_per_event_tx.call_args.kwargs["events"]
        assert called_events == [{"event_type": "workflow_completed"}]

    def test_db_unavailable_returns_envelope(self, mcp: Any, fake_ctx: ToolContext) -> None:
        from tapps_brain.mcp_server.tools_kg import register_kg_tools

        with patch("tapps_brain.mcp_server.tools_kg.kg_service") as svc:
            svc._get_or_create_cm.return_value = None
            register_kg_tools(mcp, fake_ctx)
            tool = _get_tool(mcp, "brain_record_events_batch")
            body = json.loads(tool.fn(events_json='[{"event_type": "workflow_completed"}]'))
        assert body == {
            "error": "db_unavailable",
            "detail": "TAPPS_BRAIN_DATABASE_URL is not set.",
        }
        svc.record_events_batch_per_event_tx.assert_not_called()
