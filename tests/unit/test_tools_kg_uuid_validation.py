"""TAP-2726: UUID input validation for KG MCP tools.

Each KG tool must reject non-UUID identifiers with a structured
``{"error": "bad_uuid", "field": ..., "detail": ...}`` envelope before
any DB call, preventing raw ``invalid input syntax for type uuid`` errors
from leaking to callers.

Covered tools and fields:
* ``brain_get_neighbors`` — entity_ids_json array elements
* ``brain_explain_connection`` — subject_id, object_id
* ``brain_record_feedback`` — edge_id
* ``brain_record_event`` — edges[*].subject_entity_id / object_entity_id
* ``brain_record_events_batch`` — events[*].edges.subject_entity_id / object_entity_id
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("mcp", reason="MCP extra not installed")

from tapps_brain.mcp_server.context import ToolContext

VALID_UUID_A = "11111111-1111-1111-1111-111111111111"
VALID_UUID_B = "22222222-2222-2222-2222-222222222222"
VALID_UUID_C = "33333333-3333-3333-3333-333333333333"
BAD_UUID = "tap-1630-probe"  # the exact probe string from production logs
BAD_UUID_2 = "contract.smoke"


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

    return FastMCP("test-tapps-brain-uuid-validation")


def _get_tool(mcp: Any, name: str) -> Any:
    return next(t for t in mcp._tool_manager.list_tools() if t.name == name)


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------


class TestValidateUuid:
    def test_valid_uuid_returns_none(self) -> None:
        from tapps_brain.mcp_server.tools_kg import _validate_uuid

        assert _validate_uuid(VALID_UUID_A, "some_field") is None

    def test_non_uuid_string_returns_bad_uuid_envelope(self) -> None:
        from tapps_brain.mcp_server.tools_kg import _validate_uuid

        err = _validate_uuid(BAD_UUID, "entity_id")
        assert err is not None
        assert err["error"] == "bad_uuid"
        assert err["field"] == "entity_id"
        assert "UUID" in err["detail"]
        assert BAD_UUID in err["detail"]

    def test_empty_string_returns_bad_uuid_envelope(self) -> None:
        from tapps_brain.mcp_server.tools_kg import _validate_uuid

        err = _validate_uuid("", "entity_id")
        assert err is not None
        assert err["error"] == "bad_uuid"

    def test_various_bad_formats_are_rejected(self) -> None:
        from tapps_brain.mcp_server.tools_kg import _validate_uuid

        for bad in ("not-a-uuid", "12345", "contract.smoke", "tap-1630-probe", "none", "null"):
            assert _validate_uuid(bad, "f") is not None, f"{bad!r} should be rejected"

    def test_uuid_with_uppercase_is_valid(self) -> None:
        from tapps_brain.mcp_server.tools_kg import _validate_uuid

        assert _validate_uuid(VALID_UUID_A.upper(), "entity_id") is None


# ---------------------------------------------------------------------------
# brain_get_neighbors
# ---------------------------------------------------------------------------


class TestGetNeighborsUuidValidation:
    def test_non_uuid_entity_id_returns_bad_uuid_envelope(
        self, mcp: Any, fake_ctx: ToolContext
    ) -> None:
        from tapps_brain.mcp_server.tools_kg import register_kg_tools

        with patch("tapps_brain.mcp_server.tools_kg.kg_service") as svc:
            svc._get_or_create_cm.return_value = MagicMock(name="cm")
            svc._DEFAULT_BRAIN_ID = "brain"
            register_kg_tools(mcp, fake_ctx)
            tool = _get_tool(mcp, "brain_get_neighbors")
            body = json.loads(tool.fn(entity_ids_json=json.dumps([BAD_UUID])))

        assert body["error"] == "bad_uuid"
        assert "entity_ids_json[0]" in body["field"]
        assert "UUID" in body["detail"]
        assert BAD_UUID in body["detail"]
        svc.get_neighbors.assert_not_called()

    def test_non_uuid_among_valid_uuids_returns_bad_uuid_envelope(
        self, mcp: Any, fake_ctx: ToolContext
    ) -> None:
        from tapps_brain.mcp_server.tools_kg import register_kg_tools

        with patch("tapps_brain.mcp_server.tools_kg.kg_service") as svc:
            svc._get_or_create_cm.return_value = MagicMock(name="cm")
            svc._DEFAULT_BRAIN_ID = "brain"
            register_kg_tools(mcp, fake_ctx)
            tool = _get_tool(mcp, "brain_get_neighbors")
            ids = [VALID_UUID_A, BAD_UUID_2, VALID_UUID_B]
            body = json.loads(tool.fn(entity_ids_json=json.dumps(ids)))

        assert body["error"] == "bad_uuid"
        # Second element (index 1) triggered the error
        assert "entity_ids_json[1]" in body["field"]
        svc.get_neighbors.assert_not_called()

    def test_valid_uuids_pass_through_to_service(self, mcp: Any, fake_ctx: ToolContext) -> None:
        from tapps_brain.mcp_server.tools_kg import register_kg_tools

        with patch("tapps_brain.mcp_server.tools_kg.kg_service") as svc:
            svc._get_or_create_cm.return_value = MagicMock(name="cm")
            svc._DEFAULT_BRAIN_ID = "brain"
            svc.collect_neighbor_entity_ids.return_value = {
                "entity_ids": [VALID_UUID_A],
            }
            svc.get_neighbors.return_value = {"neighbors": [], "entity_ids": [VALID_UUID_A]}
            register_kg_tools(mcp, fake_ctx)
            tool = _get_tool(mcp, "brain_get_neighbors")
            body = json.loads(tool.fn(entity_ids_json=json.dumps([VALID_UUID_A])))

        assert "error" not in body
        svc.collect_neighbor_entity_ids.assert_called_once()
        svc.get_neighbors.assert_called_once()


# ---------------------------------------------------------------------------
# brain_explain_connection
# ---------------------------------------------------------------------------


class TestExplainConnectionUuidValidation:
    def test_non_uuid_subject_id_returns_bad_uuid_envelope(
        self, mcp: Any, fake_ctx: ToolContext
    ) -> None:
        from tapps_brain.mcp_server.tools_kg import register_kg_tools

        with patch("tapps_brain.mcp_server.tools_kg.kg_service") as svc:
            svc._get_or_create_cm.return_value = MagicMock(name="cm")
            svc._DEFAULT_BRAIN_ID = "brain"
            register_kg_tools(mcp, fake_ctx)
            tool = _get_tool(mcp, "brain_explain_connection")
            body = json.loads(tool.fn(subject_id=BAD_UUID, object_id=VALID_UUID_B))

        assert body["error"] == "bad_uuid"
        assert body["field"] == "subject_id"
        assert "UUID" in body["detail"]
        svc.explain_connection.assert_not_called()

    def test_non_uuid_object_id_returns_bad_uuid_envelope(
        self, mcp: Any, fake_ctx: ToolContext
    ) -> None:
        from tapps_brain.mcp_server.tools_kg import register_kg_tools

        with patch("tapps_brain.mcp_server.tools_kg.kg_service") as svc:
            svc._get_or_create_cm.return_value = MagicMock(name="cm")
            svc._DEFAULT_BRAIN_ID = "brain"
            register_kg_tools(mcp, fake_ctx)
            tool = _get_tool(mcp, "brain_explain_connection")
            body = json.loads(tool.fn(subject_id=VALID_UUID_A, object_id=BAD_UUID_2))

        assert body["error"] == "bad_uuid"
        assert body["field"] == "object_id"
        svc.explain_connection.assert_not_called()

    def test_valid_uuids_pass_through_to_service(self, mcp: Any, fake_ctx: ToolContext) -> None:
        from tapps_brain.mcp_server.tools_kg import register_kg_tools

        with patch("tapps_brain.mcp_server.tools_kg.kg_service") as svc:
            svc._get_or_create_cm.return_value = MagicMock(name="cm")
            svc._DEFAULT_BRAIN_ID = "brain"
            svc.explain_max_hops_ceiling.return_value = 3
            svc.explain_connection.return_value = {
                "found": False,
                "path": [],
                "hops": None,
                "subject_id": VALID_UUID_A,
                "object_id": VALID_UUID_B,
            }
            register_kg_tools(mcp, fake_ctx)
            tool = _get_tool(mcp, "brain_explain_connection")
            body = json.loads(tool.fn(subject_id=VALID_UUID_A, object_id=VALID_UUID_B))

        assert "error" not in body
        svc.explain_connection.assert_called_once()


# ---------------------------------------------------------------------------
# brain_record_feedback — edge_id
# ---------------------------------------------------------------------------


class TestRecordFeedbackEdgeIdValidation:
    def test_non_uuid_edge_id_returns_bad_uuid_envelope(
        self, mcp: Any, fake_ctx: ToolContext
    ) -> None:
        from tapps_brain.mcp_server.tools_kg import register_kg_tools

        with patch("tapps_brain.mcp_server.tools_kg.kg_service") as svc:
            register_kg_tools(mcp, fake_ctx)
            tool = _get_tool(mcp, "brain_record_feedback")
            body = json.loads(tool.fn(feedback_type="edge_helpful", edge_id=BAD_UUID))

        assert body["error"] == "bad_uuid"
        assert body["field"] == "edge_id"
        assert "UUID" in body["detail"]
        svc.record_kg_feedback.assert_not_called()

    def test_empty_edge_id_routes_to_memory_path_without_uuid_check(
        self, mcp: Any, fake_ctx: ToolContext
    ) -> None:
        """When edge_id is absent the memory path is taken; no UUID check fires."""
        from tapps_brain.mcp_server.tools_kg import register_kg_tools

        with (
            patch("tapps_brain.mcp_server.tools_kg.kg_service"),
            patch("tapps_brain.services.feedback_service.feedback_record") as fb,
        ):
            fb.return_value = {}
            register_kg_tools(mcp, fake_ctx)
            tool = _get_tool(mcp, "brain_record_feedback")
            body = json.loads(tool.fn(feedback_type="recall_rated", entry_key="k1"))

        assert body.get("recorded") is True

    def test_valid_uuid_edge_id_proceeds_to_service(self, mcp: Any, fake_ctx: ToolContext) -> None:
        from tapps_brain.mcp_server.tools_kg import register_kg_tools

        with patch("tapps_brain.mcp_server.tools_kg.kg_service") as svc:
            svc._get_or_create_cm.return_value = MagicMock(name="cm")
            svc._DEFAULT_BRAIN_ID = "brain"
            svc.record_kg_feedback.return_value = {}
            register_kg_tools(mcp, fake_ctx)
            tool = _get_tool(mcp, "brain_record_feedback")
            body = json.loads(tool.fn(feedback_type="edge_helpful", edge_id=VALID_UUID_A))

        # Should reach the service and not return a bad_uuid error
        assert body.get("error") != "bad_uuid"
        svc.record_kg_feedback.assert_called_once()


# ---------------------------------------------------------------------------
# brain_record_event — edges[*].subject_entity_id / object_entity_id
# ---------------------------------------------------------------------------


class TestRecordEventEdgeUuidValidation:
    def test_non_uuid_subject_entity_id_in_edge_returns_bad_uuid(
        self, mcp: Any, fake_ctx: ToolContext
    ) -> None:
        from tapps_brain.mcp_server.tools_kg import register_kg_tools

        edge = {
            "subject_entity_id": BAD_UUID,
            "object_entity_id": VALID_UUID_B,
            "predicate": "calls",
        }
        with patch("tapps_brain.mcp_server.tools_kg.kg_service") as svc:
            svc._get_or_create_cm.return_value = MagicMock(name="cm")
            svc._DEFAULT_BRAIN_ID = "brain"
            register_kg_tools(mcp, fake_ctx)
            tool = _get_tool(mcp, "brain_record_event")
            body = json.loads(tool.fn(event_type="workflow_completed", edges=[edge]))

        assert body["error"] == "bad_uuid"
        assert "edges[0].subject_entity_id" in body["field"]
        assert "UUID" in body["detail"]
        svc.record_event.assert_not_called()

    def test_non_uuid_object_entity_id_in_edge_returns_bad_uuid(
        self, mcp: Any, fake_ctx: ToolContext
    ) -> None:
        from tapps_brain.mcp_server.tools_kg import register_kg_tools

        edge = {
            "subject_entity_id": VALID_UUID_A,
            "object_entity_id": BAD_UUID_2,
            "predicate": "calls",
        }
        with patch("tapps_brain.mcp_server.tools_kg.kg_service") as svc:
            svc._get_or_create_cm.return_value = MagicMock(name="cm")
            svc._DEFAULT_BRAIN_ID = "brain"
            register_kg_tools(mcp, fake_ctx)
            tool = _get_tool(mcp, "brain_record_event")
            body = json.loads(tool.fn(event_type="workflow_completed", edges=[edge]))

        assert body["error"] == "bad_uuid"
        assert "edges[0].object_entity_id" in body["field"]
        svc.record_event.assert_not_called()

    def test_valid_edge_uuids_pass_through_to_service(
        self, mcp: Any, fake_ctx: ToolContext
    ) -> None:
        from tapps_brain.mcp_server.tools_kg import register_kg_tools

        edge = {
            "subject_entity_id": VALID_UUID_A,
            "object_entity_id": VALID_UUID_B,
            "predicate": "calls",
        }
        with patch("tapps_brain.mcp_server.tools_kg.kg_service") as svc:
            svc._get_or_create_cm.return_value = MagicMock(name="cm")
            svc._DEFAULT_BRAIN_ID = "brain"
            svc.record_event.return_value = {"event_id": "evt-1"}
            register_kg_tools(mcp, fake_ctx)
            tool = _get_tool(mcp, "brain_record_event")
            body = json.loads(tool.fn(event_type="workflow_completed", edges=[edge]))

        assert "error" not in body
        svc.record_event.assert_called_once()

    def test_no_edges_skips_uuid_check(self, mcp: Any, fake_ctx: ToolContext) -> None:
        """Events with no edge list proceed without UUID validation overhead."""
        from tapps_brain.mcp_server.tools_kg import register_kg_tools

        with patch("tapps_brain.mcp_server.tools_kg.kg_service") as svc:
            svc._get_or_create_cm.return_value = MagicMock(name="cm")
            svc._DEFAULT_BRAIN_ID = "brain"
            svc.record_event.return_value = {"event_id": "evt-2"}
            register_kg_tools(mcp, fake_ctx)
            tool = _get_tool(mcp, "brain_record_event")
            body = json.loads(tool.fn(event_type="tool_called"))

        assert "error" not in body
        svc.record_event.assert_called_once()


# ---------------------------------------------------------------------------
# brain_record_events_batch — per-event edge UUID validation
# ---------------------------------------------------------------------------


class TestRecordEventsBatchUuidValidation:
    def _make_events_json(self, events: list[dict[str, Any]]) -> str:
        return json.dumps(events)

    def test_event_with_non_uuid_subject_entity_id_lands_in_failed(
        self, mcp: Any, fake_ctx: ToolContext
    ) -> None:
        from tapps_brain.mcp_server.tools_kg import register_kg_tools

        bad_event = {
            "event_type": "tool_called",
            "edges": [{"subject_entity_id": BAD_UUID, "object_entity_id": VALID_UUID_B}],
        }
        good_event = {"event_type": "workflow_completed"}
        with patch("tapps_brain.mcp_server.tools_kg.kg_service") as svc:
            svc._get_or_create_cm.return_value = MagicMock(name="cm")
            svc._DEFAULT_BRAIN_ID = "brain"
            svc.record_events_batch_per_event_tx.return_value = {
                "succeeded": [{"index": 0, "result": {"event_id": "e1"}}],
                "failed": [],
                "count": 1,
                "succeeded_count": 1,
                "failed_count": 0,
            }
            register_kg_tools(mcp, fake_ctx)
            tool = _get_tool(mcp, "brain_record_events_batch")
            body = json.loads(tool.fn(events_json=self._make_events_json([bad_event, good_event])))

        # The bad event is excluded; only good_event goes to the service.
        assert svc.record_events_batch_per_event_tx.call_count == 1
        called_events = svc.record_events_batch_per_event_tx.call_args.kwargs["events"]
        assert len(called_events) == 1
        assert called_events[0]["event_type"] == "workflow_completed"

        # The bad event lands in failed[].
        assert body["failed_count"] >= 1
        bad_failures = [f for f in body["failed"] if f.get("error") == "bad_uuid"]
        assert len(bad_failures) == 1
        assert "subject_entity_id" in bad_failures[0]["field"]

    def test_all_bad_uuid_events_returns_empty_succeeded(
        self, mcp: Any, fake_ctx: ToolContext
    ) -> None:
        from tapps_brain.mcp_server.tools_kg import register_kg_tools

        all_bad = [
            {
                "event_type": "tool_called",
                "edges": [{"subject_entity_id": BAD_UUID, "object_entity_id": VALID_UUID_A}],
            }
            for _ in range(3)
        ]
        with patch("tapps_brain.mcp_server.tools_kg.kg_service") as svc:
            svc._get_or_create_cm.return_value = MagicMock(name="cm")
            svc._DEFAULT_BRAIN_ID = "brain"
            register_kg_tools(mcp, fake_ctx)
            tool = _get_tool(mcp, "brain_record_events_batch")
            body = json.loads(tool.fn(events_json=self._make_events_json(all_bad)))

        svc.record_events_batch_per_event_tx.assert_not_called()
        assert body["succeeded"] == []
        assert body["failed_count"] == 3

    def test_all_valid_uuid_events_pass_through_unmodified(
        self, mcp: Any, fake_ctx: ToolContext
    ) -> None:
        from tapps_brain.mcp_server.tools_kg import register_kg_tools

        events = [
            {
                "event_type": "tool_called",
                "edges": [{"subject_entity_id": VALID_UUID_A, "object_entity_id": VALID_UUID_B}],
            }
        ]
        with patch("tapps_brain.mcp_server.tools_kg.kg_service") as svc:
            svc._get_or_create_cm.return_value = MagicMock(name="cm")
            svc._DEFAULT_BRAIN_ID = "brain"
            svc.record_events_batch_per_event_tx.return_value = {
                "succeeded": [{"index": 0, "result": {}}],
                "failed": [],
                "count": 1,
                "succeeded_count": 1,
                "failed_count": 0,
            }
            register_kg_tools(mcp, fake_ctx)
            tool = _get_tool(mcp, "brain_record_events_batch")
            body = json.loads(tool.fn(events_json=self._make_events_json(events)))

        assert "error" not in body
        svc.record_events_batch_per_event_tx.assert_called_once()
        assert body["failed_count"] == 0
