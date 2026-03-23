"""End-to-end diagnostics pipeline (STORY-030.7): store, history, MCP, CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.requires_mcp, pytest.mark.requires_cli]

from typer.testing import CliRunner

from tapps_brain.cli import app
from tapps_brain.mcp_server import create_server
from tapps_brain.store import MemoryStore


def _tool_fn(mcp_server: object, name: str):
    for tool in mcp_server._tool_manager.list_tools():
        if tool.name == name:
            return tool.fn
    msg = f"tool not found: {name}"
    raise KeyError(msg)


def test_diagnostics_pipeline_cli_mcp_history(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    try:
        store.save("diag-a", "content one", tier="pattern")
        store.save("diag-b", "content two", tier="architectural")
        store.report_gap("missing topic xyz", session_id="sess-diag")

        r1 = store.diagnostics(record_history=True)
        assert r1.circuit_state in ("closed", "degraded", "open", "half_open")
        assert 0.0 <= r1.composite_score <= 1.0

        r2 = store.diagnostics(record_history=True)
        hist = store.diagnostics_history(limit=10)
        assert len(hist) >= 2
        assert all("composite_score" in row for row in hist)
    finally:
        store.close()

    runner = CliRunner()
    out = runner.invoke(
        app,
        ["diagnostics", "report", "--json", "--project-dir", str(tmp_path)],
    )
    assert out.exit_code == 0
    payload = json.loads(out.stdout)
    assert payload["gap_count"] >= 1

    mcp = create_server(tmp_path)
    try:
        dr = _tool_fn(mcp, "diagnostics_report")
        snap = json.loads(dr(record_history=False))
        assert "dimensions" in snap
        uri_res = next(
            r
            for r in mcp._resource_manager.list_resources()
            if str(r.uri) == "memory://diagnostics"
        )
        body = json.loads(uri_res.fn())
        assert body["circuit_state"] == snap["circuit_state"]
    finally:
        mcp._tapps_store.close()
