"""Feedback, diagnostics, and flywheel MCP tool registrations.

Extracted from ``tapps_brain.mcp_server.__init__`` (TAP-605).

Note: unlike the agent-brain / memory tools, these feedback and flywheel
tools do **not** perform per-call agent_id resolution — they use the
server-level agent_id and the :class:`_StoreProxy` directly.  That matches
the pre-split behaviour.
"""

from __future__ import annotations

import json
from typing import Any

from tapps_brain.mcp_server.context import ToolContext
from tapps_brain.services import diagnostics_service, feedback_service, flywheel_service


def register_feedback_tools(mcp: Any, ctx: ToolContext) -> None:
    """Register feedback + diagnostics + flywheel tools on *mcp*."""
    store = ctx.store
    agent_id = ctx.server_agent_id
    _pid = ctx.pid
    _require_operator_enabled = ctx.require_operator_enabled

    @mcp.tool()  # type: ignore[untyped-decorator]
    def feedback_rate(
        entry_key: str,
        rating: str = "helpful",
        session_id: str = "",
        details_json: str = "",
    ) -> str:
        """Rate a recalled memory entry (creates ``recall_rated`` event)."""
        return json.dumps(
            feedback_service.feedback_rate(
                store,
                _pid(),
                agent_id,
                entry_key=entry_key,
                rating=rating,
                session_id=session_id,
                details_json=details_json,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def feedback_gap(
        query: str,
        session_id: str = "",
        details_json: str = "",
    ) -> str:
        """Report a knowledge gap (``gap_reported`` event)."""
        return json.dumps(
            feedback_service.feedback_gap(
                store,
                _pid(),
                agent_id,
                query=query,
                session_id=session_id,
                details_json=details_json,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def feedback_issue(
        entry_key: str,
        issue: str,
        session_id: str = "",
        details_json: str = "",
    ) -> str:
        """Flag a quality issue with a memory entry (``issue_flagged``)."""
        return json.dumps(
            feedback_service.feedback_issue(
                store,
                _pid(),
                agent_id,
                entry_key=entry_key,
                issue=issue,
                session_id=session_id,
                details_json=details_json,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def feedback_record(
        event_type: str,
        entry_key: str = "",
        session_id: str = "",
        utility_score: float | None = None,
        details_json: str = "",
    ) -> str:
        """Record a generic feedback event (built-in or custom type)."""
        return json.dumps(
            feedback_service.feedback_record(
                store,
                _pid(),
                agent_id,
                event_type=event_type,
                entry_key=entry_key,
                session_id=session_id,
                utility_score=utility_score,
                details_json=details_json,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def feedback_query(
        event_type: str = "",
        entry_key: str = "",
        session_id: str = "",
        since: str = "",
        until: str = "",
        limit: int = 100,
    ) -> str:
        """Query recorded feedback events with optional filters."""
        return json.dumps(
            feedback_service.feedback_query(
                store,
                _pid(),
                agent_id,
                event_type=event_type,
                entry_key=entry_key,
                session_id=session_id,
                since=since,
                until=until,
                limit=limit,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def diagnostics_report(
        record_history: bool = True,
    ) -> str:
        """Run quality diagnostics (EPIC-030): composite score, dimensions, circuit state."""
        return json.dumps(
            diagnostics_service.diagnostics_report(
                store,
                _pid(),
                agent_id,
                record_history=record_history,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def diagnostics_history(
        limit: int = 50,
    ) -> str:
        """Return recent persisted diagnostics snapshots."""
        return json.dumps(
            diagnostics_service.diagnostics_history(
                store,
                _pid(),
                agent_id,
                limit=limit,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def flywheel_process(since: str = "") -> str:
        """Run feedback → confidence pipeline (EPIC-031)."""
        return json.dumps(flywheel_service.flywheel_process(store, _pid(), agent_id, since=since))

    @mcp.tool()  # type: ignore[untyped-decorator]
    def flywheel_gaps(limit: int = 10, semantic: bool = False) -> str:
        """Return top knowledge gaps as JSON."""
        return json.dumps(
            flywheel_service.flywheel_gaps(
                store,
                _pid(),
                agent_id,
                limit=limit,
                semantic=semantic,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def flywheel_report(period_days: int = 7) -> str:
        """Generate quality report (markdown + structured summary)."""
        return json.dumps(
            flywheel_service.flywheel_report(
                store,
                _pid(),
                agent_id,
                period_days=period_days,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def flywheel_evaluate(suite_path: str, k: int = 5) -> str:
        """Run BEIR-format directory or YAML suite evaluation."""
        _require_operator_enabled()
        return json.dumps(
            flywheel_service.flywheel_evaluate(
                store,
                _pid(),
                agent_id,
                suite_path=suite_path,
                k=k,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def flywheel_hive_feedback(threshold: int = 3) -> str:
        """Aggregate / apply Hive cross-project feedback penalties."""
        _require_operator_enabled()
        return json.dumps(
            flywheel_service.flywheel_hive_feedback(
                store,
                _pid(),
                agent_id,
                threshold=threshold,
            )
        )
