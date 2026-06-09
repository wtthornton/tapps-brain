"""Unit tests for kg_service.query_events (TAP-3157 / STORY-074.1)."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from tapps_brain.services import kg_service


class _FakeCM:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows
        self.last_sql: str = ""
        self.last_params: list[Any] = []

    @contextmanager
    def project_context(self, project_id: str):
        _ = project_id
        cm = self

        class _FakeCursor:
            def execute(self, sql: str, params: list[Any]) -> None:
                cm.last_sql = sql
                cm.last_params = params

            def fetchall(self) -> list[tuple[Any, ...]]:
                return cm._rows

            def __enter__(self) -> _FakeCursor:
                return self

            def __exit__(self, *args: object) -> None:
                return None

        class _FakeConn:
            def cursor(self) -> _FakeCursor:
                return _FakeCursor()

            def __enter__(self) -> _FakeConn:
                return self

            def __exit__(self, *args: object) -> None:
                return None

        yield _FakeConn()


def test_query_events_requires_event_type() -> None:
    cm = _FakeCM([])
    out = kg_service.query_events(cm, "proj", event_type="")
    assert out == {"error": "bad_request", "detail": "event_type is required."}


def test_query_events_maps_rows_to_contract() -> None:
    ts = datetime(2026, 6, 9, 12, 0, 1, tzinfo=UTC)
    rows = [
        (
            "evt-uuid-1",
            "quality_metric",
            {"score": 88.5, "file_path": "src/foo.py", "gate_passed": True},
            ts,
            "agent-a",
            "sess-1",
        ),
    ]
    cm = _FakeCM(rows)
    out = kg_service.query_events(cm, "proj", event_type="quality_metric", entity_id="src/foo.py")
    assert out["count"] == 1
    evt = out["events"][0]
    assert evt["event_id"] == "evt-uuid-1"
    assert evt["event_type"] == "quality_metric"
    assert evt["payload"]["score"] == 88.5
    assert evt["ts"] == ts.isoformat()
    assert evt["agent_id"] == "agent-a"
    assert evt["session_id"] == "sess-1"


def test_query_events_caps_limit_at_500() -> None:
    cm = _FakeCM([])
    kg_service.query_events(cm, "proj", event_type="tool_called", limit=9999)
    assert cm.last_params[-1] == 500


def test_query_events_entity_id_adds_payload_and_subject_filters() -> None:
    cm = _FakeCM([])
    kg_service.query_events(cm, "proj", event_type="quality_metric", entity_id="path/to/file.py")
    assert "payload->>'file_path'" in cm.last_sql
    assert "subject_key" in cm.last_sql
    assert cm.last_params.count("path/to/file.py") == 2


@pytest.mark.parametrize("limit", [0, -3])
def test_query_events_clamps_low_limit_to_one(limit: int) -> None:
    cm = _FakeCM([])
    kg_service.query_events(cm, "proj", event_type="x", limit=limit)
    assert cm.last_params[-1] == 1


def test_query_events_omits_session_id_when_null() -> None:
    ts = datetime(2026, 6, 9, 12, 0, 1, tzinfo=UTC)
    rows = [("id-1", "tool_called", {}, ts, "agent", None)]
    cm = _FakeCM(rows)
    out = kg_service.query_events(cm, "proj", event_type="tool_called")
    assert "session_id" not in out["events"][0]


def test_mcp_tool_delegates_to_query_events() -> None:
    from unittest.mock import patch

    from tapps_brain.mcp_server.tools_kg import register_kg_tools

    captured: dict[str, Any] = {}

    def _capture_query(*args: Any, **kwargs: Any) -> dict[str, Any]:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {"events": [], "count": 0}

    mcp = MagicMock()
    tools: dict[str, Any] = {}

    def _tool_decorator() -> Any:
        def _wrap(fn: Any) -> Any:
            tools[fn.__name__] = fn
            return fn

        return _wrap

    mcp.tool = _tool_decorator
    fake_ctx = MagicMock()
    fake_ctx.server_agent_id = "server-agent"
    fake_ctx.pid.return_value = "my-proj"
    fake_ctx.resolve_per_call_agent_id.side_effect = lambda aid, default: aid or default

    with patch("tapps_brain.mcp_server.tools_kg.kg_service") as svc:
        svc._get_or_create_cm.return_value = MagicMock()
        svc.query_events.side_effect = _capture_query
        register_kg_tools(mcp, fake_ctx)

        raw = tools["brain_query_events"](
            event_type="quality_metric",
            entity_id="src/a.py",
            since="2026-06-01T00:00:00Z",
            limit=25,
        )

    import json

    assert json.loads(raw) == {"events": [], "count": 0}
    assert captured["kwargs"]["event_type"] == "quality_metric"
    assert captured["kwargs"]["entity_id"] == "src/a.py"
    assert captured["kwargs"]["since"] == "2026-06-01T00:00:00Z"
    assert captured["kwargs"]["limit"] == 25
