"""TAP-1967 / TAP-1968 / TAP-1969: structured ``bad_json`` envelope on decode failure.

The three KG MCP tools (``brain_record_event``, ``brain_get_neighbors``,
``brain_record_feedback``) used to silently swallow ``json.JSONDecodeError``
when a legacy ``*_json`` string parameter could not be parsed.  Operators
got a quiet ``{}`` / ``[]`` substitution and no signal that their payload was
malformed.

These tests pin the new behaviour: every ``*_json`` decode error returns
``{"error": "bad_json", "field": "<name>", "detail": "<msg>"}`` and never
writes a row / performs any side effect.
"""

from __future__ import annotations

import json
import warnings
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

    return FastMCP("test-tapps-brain-bad-json")


def _get_tool(mcp: Any, name: str) -> Any:
    return next(t for t in mcp._tool_manager.list_tools() if t.name == name)


# ---------------------------------------------------------------------------
# Direct unit tests for the coerce helpers (no MCP plumbing needed)
# ---------------------------------------------------------------------------


class TestCoerceHelpers:
    def test_coerce_payload_native_dict_wins(self) -> None:
        from tapps_brain.mcp_server.tools_kg import _coerce_payload

        out, err = _coerce_payload({"a": 1}, '{"b": 2}')
        assert out == {"a": 1}
        assert err is None

    def test_coerce_payload_empty_returns_empty_no_error(self) -> None:
        from tapps_brain.mcp_server.tools_kg import _coerce_payload

        out, err = _coerce_payload(None, "")
        assert out == {}
        assert err is None

    def test_coerce_payload_empty_object_string_returns_empty_no_error(self) -> None:
        from tapps_brain.mcp_server.tools_kg import _coerce_payload

        out, err = _coerce_payload(None, "{}")
        assert out == {}
        assert err is None

    def test_coerce_payload_bad_json_returns_error_envelope(self) -> None:
        from tapps_brain.mcp_server.tools_kg import _coerce_payload

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            out, err = _coerce_payload(None, "{not: valid}")
        assert out == {}
        assert err is not None
        assert err["error"] == "bad_json"
        assert err["field"] == "payload_json"
        assert err["detail"]  # non-empty json.JSONDecodeError message

    def test_coerce_payload_non_object_json_returns_error(self) -> None:
        from tapps_brain.mcp_server.tools_kg import _coerce_payload

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            out, err = _coerce_payload(None, "[1, 2, 3]")
        assert out == {}
        assert err == {
            "error": "bad_json",
            "field": "payload_json",
            "detail": "expected JSON object, got list",
        }

    def test_coerce_list_native_list_wins(self) -> None:
        from tapps_brain.mcp_server.tools_kg import _coerce_list

        out, err = _coerce_list([{"id": "a"}], '[{"id": "b"}]', "entities")
        assert out == [{"id": "a"}]
        assert err is None

    @pytest.mark.parametrize("field", ["entities", "edges", "evidence"])
    def test_coerce_list_bad_json_returns_field_specific_error(self, field: str) -> None:
        from tapps_brain.mcp_server.tools_kg import _coerce_list

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            out, err = _coerce_list(None, "[bad", field)
        assert out == []
        assert err is not None
        assert err["error"] == "bad_json"
        assert err["field"] == f"{field}_json"
        assert err["detail"]

    def test_coerce_list_non_array_json_returns_error(self) -> None:
        from tapps_brain.mcp_server.tools_kg import _coerce_list

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            out, err = _coerce_list(None, '{"foo": "bar"}', "entities")
        assert out == []
        assert err == {
            "error": "bad_json",
            "field": "entities_json",
            "detail": "expected JSON array, got dict",
        }


# ---------------------------------------------------------------------------
# brain_record_event wrapper — error short-circuits before any side effect
# ---------------------------------------------------------------------------


class TestBrainRecordEventBadJson:
    @pytest.mark.parametrize(
        ("kwarg", "field_name"),
        [
            ("payload_json", "payload_json"),
            ("entities_json", "entities_json"),
            ("edges_json", "edges_json"),
            ("evidence_json", "evidence_json"),
        ],
    )
    def test_bad_json_short_circuits_with_envelope(
        self, mcp: Any, fake_ctx: ToolContext, kwarg: str, field_name: str
    ) -> None:
        from tapps_brain.mcp_server.tools_kg import register_kg_tools

        with patch("tapps_brain.mcp_server.tools_kg.kg_service") as svc:
            register_kg_tools(mcp, fake_ctx)
            tool = _get_tool(mcp, "brain_record_event")
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                body = json.loads(tool.fn(event_type="workflow_completed", **{kwarg: "{bad json"}))
            assert body == {
                "error": "bad_json",
                "field": field_name,
                "detail": body["detail"],  # tolerate concrete json message
            }
            assert body["detail"]
            # No row written.
            svc.record_event.assert_not_called()

    def test_empty_json_strings_still_succeed(self, mcp: Any, fake_ctx: ToolContext) -> None:
        from tapps_brain.mcp_server.tools_kg import register_kg_tools

        with patch("tapps_brain.mcp_server.tools_kg.kg_service") as svc:
            svc._get_or_create_cm.return_value = MagicMock(name="cm")
            svc._DEFAULT_BRAIN_ID = "brain"
            svc.record_event.return_value = {"event_id": "evt-1"}
            register_kg_tools(mcp, fake_ctx)
            tool = _get_tool(mcp, "brain_record_event")
            body = json.loads(
                tool.fn(
                    event_type="workflow_completed",
                    payload_json="",
                    entities_json="[]",
                    edges_json="",
                    evidence_json="[]",
                )
            )
            assert body == {"event_id": "evt-1"}
            svc.record_event.assert_called_once()
