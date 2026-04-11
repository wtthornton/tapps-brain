"""End-to-end feedback pipeline (STORY-029.6).

Uses real SQLite (project + Hive), explicit and implicit feedback,
``query_feedback``, custom event types, MCP tools, CLI, Hive propagation,
and audit log presence.  Skipped when ``mcp`` or ``cli`` extras are missing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.requires_mcp, pytest.mark.requires_cli]

from typer.testing import CliRunner

from tapps_brain.cli import app
from tapps_brain.feedback import FeedbackConfig
from tapps_brain.mcp_server import create_server
from tapps_brain.profile import get_builtin_profile
from tapps_brain.store import MemoryStore


def _mcp_tool_fn(mcp_server: object, name: str):
    for tool in mcp_server._tool_manager.list_tools():
        if tool.name == name:
            return tool.fn
    msg = f"tool not found: {name}"
    raise KeyError(msg)


@pytest.mark.skip(
    reason="SQLite HiveStore removed in v3 (ADR-007); test requires PostgresHiveBackend"
)
def test_full_feedback_pipeline(tmp_path: Path) -> None:
    hs: object = None  # HiveStore removed in v3 (ADR-007); placeholder for dead code below
    raise RuntimeError("HiveStore (SQLite) removed in v3 — see ADR-007")
    profile = get_builtin_profile("repo-brain").model_copy(
        update={"feedback": FeedbackConfig(custom_event_types=["deploy_completed"])},
    )

    store = MemoryStore(
        tmp_path,
        hive_store=hs,
        hive_agent_id="integ-agent",
        profile=profile,
    )
    try:
        hs.save(
            key="hive-shared",
            value="hive integration marker hfb99zz",
            namespace="universal",
            source_agent="integ-agent",
            conflict_policy="last_write_wins",
        )
        store.save("local-k", "local value for reinforce test hfb99aa", tier="pattern")

        res = store.recall("hfb99zz", session_id="integ-sess")
        assert any(m.get("source") == "hive" for m in res.memories)

        store.recall("hfb99aa", session_id="integ-sess")
        store.reinforce("local-k", session_id="integ-sess", confidence_boost=0.0)

        store.rate_recall("hive-shared", session_id="integ-sess", rating="partial")
        store.report_gap("undocumented workflow", session_id="integ-sess")
        store.report_issue("local-k", "stale", session_id="integ-sess")
        store.record_feedback("deploy_completed", entry_key="local-k", utility_score=0.2)

        types = {e.event_type for e in store.query_feedback(limit=500)}
        assert "recall_rated" in types
        assert "gap_reported" in types
        assert "issue_flagged" in types
        assert "implicit_positive" in types
        assert "deploy_completed" in types

        hive_rows = hs.query_feedback_events(namespace="universal", limit=50)
        assert any(r["entry_key"] == "hive-shared" for r in hive_rows)

        audit_path = tmp_path / ".tapps-brain" / "memory" / "memory_log.jsonl"
        audit_text = audit_path.read_text(encoding="utf-8")
        assert "feedback_record" in audit_text
    finally:
        store.close()

    runner = CliRunner()
    cli_out = runner.invoke(
        app,
        ["feedback", "list", "--json", "--project-dir", str(tmp_path), "--limit", "50"],
    )
    assert cli_out.exit_code == 0
    cli_events = json.loads(cli_out.stdout)
    assert len(cli_events) >= 5

    mcp = create_server(tmp_path)
    try:
        fq = _mcp_tool_fn(mcp, "feedback_query")
        raw = fq(limit=30)
        payload = json.loads(raw)
        assert payload["count"] >= 1
    finally:
        mcp._tapps_store.close()

    hs.close()
