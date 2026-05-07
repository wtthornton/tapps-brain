"""Unit tests for EPIC-076 STORY-076.6 — edge feedback events via FeedbackStore.

Covers:
* ``edge_helpful`` and ``edge_misleading`` are accepted by ``FeedbackStore``
  (BUILTIN_EVENT_TYPES includes both).
* ``apply_edge_feedback`` updates counters, confidence, and review-flag
  (mocked DB cursor).
* Auto-flag threshold: ``negative_feedback_count > 3 × positive_feedback_count``.
* ``brain_record_feedback`` MCP tool routes edge and memory feedback in one schema.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tapps_brain.feedback import (
    BUILTIN_EVENT_TYPES,
    FeedbackConfig,
    FeedbackEvent,
    InMemoryFeedbackStore,
)
from tapps_brain.postgres_kg import PostgresKnowledgeGraphStore

# ---------------------------------------------------------------------------
# FeedbackStore event-type acceptance
# ---------------------------------------------------------------------------


def test_edge_helpful_in_builtin_event_types() -> None:
    assert "edge_helpful" in BUILTIN_EVENT_TYPES


def test_edge_misleading_in_builtin_event_types() -> None:
    assert "edge_misleading" in BUILTIN_EVENT_TYPES


def test_inmemory_store_accepts_edge_helpful() -> None:
    store = InMemoryFeedbackStore(config=FeedbackConfig(strict_event_types=True))
    event = FeedbackEvent(event_type="edge_helpful", entry_key="some-edge-uuid")
    store.record(event)
    results = store.query(event_type="edge_helpful")
    assert len(results) == 1
    assert results[0].entry_key == "some-edge-uuid"


def test_inmemory_store_accepts_edge_misleading() -> None:
    store = InMemoryFeedbackStore(config=FeedbackConfig(strict_event_types=True))
    event = FeedbackEvent(event_type="edge_misleading", entry_key="edge-uuid-2")
    store.record(event)
    results = store.query(event_type="edge_misleading")
    assert len(results) == 1
    assert results[0].entry_key == "edge-uuid-2"


def test_strict_mode_rejects_unknown_edge_type() -> None:
    store = InMemoryFeedbackStore(config=FeedbackConfig(strict_event_types=True))
    with pytest.raises(ValueError, match="Unknown event_type"):
        store.record(FeedbackEvent(event_type="edge_neutral", entry_key="x"))


# ---------------------------------------------------------------------------
# apply_edge_feedback — edge_helpful
# ---------------------------------------------------------------------------


def _make_kg(cm: Any, project_id: str = "test-proj") -> PostgresKnowledgeGraphStore:
    """Build a KG store against a mocked connection manager."""
    return PostgresKnowledgeGraphStore(
        cm, brain_id="test-brain", project_id=project_id
    )


def _mock_cm(fetchone_return: Any = None) -> MagicMock:
    """Return a connection manager mock that yields a cursor with a preset fetchone.

    ``project_context`` is set to ``None`` so ``_scoped_conn`` falls through to
    ``get_connection()`` rather than trying to call the auto-created MagicMock.
    """
    cm = MagicMock()
    # Prevent MagicMock from auto-creating a truthy project_context attribute —
    # PostgresKnowledgeGraphStore._scoped_conn checks `if pc is not None`.
    cm.project_context = None

    conn = MagicMock()
    cur = MagicMock()
    cur.fetchone.return_value = fetchone_return
    cur.rowcount = 1
    conn.cursor.return_value.__enter__ = lambda self: cur
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    cm.get_connection.return_value.__enter__ = lambda self: conn
    cm.get_connection.return_value.__exit__ = MagicMock(return_value=False)
    return cm


def test_apply_edge_helpful_returns_applied_true() -> None:
    edge_id = "aaaaaaaa-0000-0000-0000-000000000001"
    # APPLY_EDGE_HELPFUL_SQL: returns (id, positive_feedback_count, negative_feedback_count)
    helpful_row = (edge_id, 3.0, 0.0)

    cm = _mock_cm(fetchone_return=helpful_row)
    kg = _make_kg(cm)

    # Patch reinforce_edge so we don't need a second DB round-trip
    with patch.object(kg, "reinforce_edge", return_value=True) as mock_reinforce:
        result = kg.apply_edge_feedback(edge_id, "edge_helpful")

    assert result["applied"] is True
    assert result["feedback_type"] == "edge_helpful"
    assert result["positive_feedback_count"] == 3.0
    assert result["negative_feedback_count"] == 0.0
    mock_reinforce.assert_called_once_with(edge_id, was_useful=True)


def test_apply_edge_helpful_edge_not_found() -> None:
    cm = _mock_cm(fetchone_return=None)
    kg = _make_kg(cm)
    result = kg.apply_edge_feedback("nonexistent-uuid", "edge_helpful")
    assert result["applied"] is False
    assert result["reason"] == "edge_not_found"


# ---------------------------------------------------------------------------
# apply_edge_feedback — edge_misleading + auto-flag threshold
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pos_count, neg_count, expect_flagged",
    [
        # neg_after = 1, pos = 1 → 1 > 3 → False
        (1.0, 0.0, False),
        # neg_after = 4, pos = 1 → 4 > 3 → True
        (1.0, 3.0, True),
        # neg_after = 7, pos = 2 → 7 > 6 → True
        (2.0, 6.0, True),
        # neg_after = 6, pos = 2 → 6 == 6 → False (strictly greater)
        (2.0, 5.0, False),
        # pos = 0, neg_after = 1 → 1 > 0 → True
        (0.0, 0.0, True),
    ],
)
def test_apply_edge_misleading_flag_threshold(
    pos_count: float,
    neg_count: float,
    expect_flagged: bool,
) -> None:
    """The review_flagged column from the DB drives the returned flag."""
    edge_id = "bbbbbbbb-0000-0000-0000-000000000002"
    neg_after = neg_count + 1.0
    flagged_str = "true" if neg_after > 3 * pos_count else None

    # APPLY_EDGE_MISLEADING_SQL returns:
    # (id, positive_feedback_count, negative_feedback_count, confidence, review_flagged)
    misleading_row = (edge_id, pos_count, neg_after, 0.5, flagged_str)

    cm = _mock_cm(fetchone_return=misleading_row)
    kg = _make_kg(cm)
    result = kg.apply_edge_feedback(edge_id, "edge_misleading", confidence_delta=0.05)

    assert result["applied"] is True
    assert result["feedback_type"] == "edge_misleading"
    assert result["flagged_for_review"] is expect_flagged


def test_apply_edge_misleading_lowers_confidence() -> None:
    edge_id = "cccccccc-0000-0000-0000-000000000003"
    # Simulated confidence after delta reduction (SQL handles the GREATEST clamp)
    post_confidence = 0.45
    misleading_row = (edge_id, 0.0, 1.0, post_confidence, None)

    cm = _mock_cm(fetchone_return=misleading_row)
    kg = _make_kg(cm)
    result = kg.apply_edge_feedback(edge_id, "edge_misleading", confidence_delta=0.05)

    assert result["applied"] is True
    assert result["confidence"] == pytest.approx(0.45)
    assert result["flagged_for_review"] is False


def test_apply_edge_misleading_edge_not_found() -> None:
    cm = _mock_cm(fetchone_return=None)
    kg = _make_kg(cm)
    result = kg.apply_edge_feedback("nonexistent", "edge_misleading")
    assert result["applied"] is False
    assert result["reason"] == "edge_not_found"


def test_apply_edge_feedback_unknown_type() -> None:
    cm = _mock_cm(fetchone_return=None)
    kg = _make_kg(cm)
    result = kg.apply_edge_feedback("some-edge", "edge_neutral")
    assert result["applied"] is False
    assert result["reason"] == "unknown_feedback_type"


# ---------------------------------------------------------------------------
# brain_record_feedback MCP tool — unified schema routing
# ---------------------------------------------------------------------------


def _make_tool_context(project_id: str = "proj") -> MagicMock:
    ctx = MagicMock()
    ctx.server_agent_id = "test-agent"
    ctx.pid.return_value = project_id
    ctx.resolve_per_call_agent_id.return_value = "test-agent"
    ctx.resolve_store_for_call.return_value = MagicMock()
    return ctx


def test_brain_record_feedback_edge_path() -> None:
    """brain_record_feedback routes to kg_service when edge_id is set."""
    from tapps_brain.mcp_server.tools_kg import register_kg_tools

    mcp = MagicMock()
    registered: dict[str, Any] = {}

    def fake_tool() -> Any:
        def decorator(fn: Any) -> Any:
            registered[fn.__name__] = fn
            return fn
        return decorator

    mcp.tool = fake_tool
    ctx = _make_tool_context()
    register_kg_tools(mcp, ctx)

    assert "brain_record_feedback" in registered
    fn = registered["brain_record_feedback"]

    edge_uuid = "dddddddd-0000-0000-0000-000000000004"
    kg_result = {
        "status": "recorded",
        "event": {"id": "evt-1", "event_type": "edge_helpful"},
        "kg_update": {"applied": True, "feedback_type": "edge_helpful"},
    }

    with patch(
        "tapps_brain.services.kg_service.record_kg_feedback",
        return_value=kg_result,
    ) as mock_kg:
        raw = fn(
            feedback_type="edge_helpful",
            edge_id=edge_uuid,
            session_id="s1",
        )

    resp = json.loads(raw)
    assert resp["recorded"] is True
    assert resp["feedback_type"] == "edge_helpful"
    assert resp["edge_id"] == edge_uuid
    assert resp["entry_key"] is None
    mock_kg.assert_called_once()


def test_brain_record_feedback_memory_path() -> None:
    """brain_record_feedback routes to feedback_service when only entry_key is set."""
    from tapps_brain.mcp_server.tools_kg import register_kg_tools

    mcp = MagicMock()
    registered: dict[str, Any] = {}

    def fake_tool() -> Any:
        def decorator(fn: Any) -> Any:
            registered[fn.__name__] = fn
            return fn
        return decorator

    mcp.tool = fake_tool
    ctx = _make_tool_context()
    register_kg_tools(mcp, ctx)

    fn = registered["brain_record_feedback"]

    fb_result = {"status": "recorded", "event": {"id": "evt-2", "event_type": "recall_rated"}}

    with patch(
        "tapps_brain.services.feedback_service.feedback_record",
        return_value=fb_result,
    ) as mock_fb:
        raw = fn(
            feedback_type="recall_rated",
            entry_key="my-memory-key",
            utility_score=0.8,
        )

    resp = json.loads(raw)
    assert resp["recorded"] is True
    assert resp["feedback_type"] == "recall_rated"
    assert resp["edge_id"] is None
    assert resp["entry_key"] == "my-memory-key"
    mock_fb.assert_called_once()


def test_brain_record_feedback_edge_error_propagated() -> None:
    """Error from kg_service.record_kg_feedback is returned as-is."""
    from tapps_brain.mcp_server.tools_kg import register_kg_tools

    mcp = MagicMock()
    registered: dict[str, Any] = {}

    def fake_tool() -> Any:
        def decorator(fn: Any) -> Any:
            registered[fn.__name__] = fn
            return fn
        return decorator

    mcp.tool = fake_tool
    ctx = _make_tool_context()
    register_kg_tools(mcp, ctx)

    fn = registered["brain_record_feedback"]

    with patch(
        "tapps_brain.services.kg_service.record_kg_feedback",
        return_value={"error": "bad_request", "detail": "feedback_type must be..."},
    ):
        raw = fn(feedback_type="bad_type", edge_id="some-edge")

    resp = json.loads(raw)
    assert resp.get("error") == "bad_request"
