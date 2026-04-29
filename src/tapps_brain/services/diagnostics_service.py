"""Diagnostics service functions (EPIC-070 STORY-070.1).

Exposes memory quality reporting and system health checks via MCP diagnostic
tools and the HTTP adapter. Delegates to ``MemoryStore.diagnostics()``,
``MemoryStore.diagnostics_history()``, and ``health_check.run_health_check()``.
"""

from __future__ import annotations

import traceback
from typing import Any


def diagnostics_report(
    store: Any, project_id: str, agent_id: str, *, record_history: bool = True
) -> dict[str, Any]:
    """Return the current diagnostics scorecard; optionally persists a history snapshot."""
    rep = store.diagnostics(record_history=record_history)
    return rep.model_dump(mode="json")  # type: ignore[no-any-return]


def diagnostics_history(
    store: Any, project_id: str, agent_id: str, *, limit: int = 50
) -> dict[str, Any]:
    """Return the most recent diagnostics history records up to ``limit`` rows."""
    rows = store.diagnostics_history(limit=limit)
    return {"records": rows, "count": len(rows)}


def tapps_brain_health(
    store: Any, project_id: str, agent_id: str, *, check_hive: bool = True
) -> dict[str, Any]:
    """Run a full system health check covering the store, Postgres, and optionally Hive."""
    try:
        from tapps_brain.health_check import run_health_check

        root = getattr(store, "_project_root", None)
        report = run_health_check(
            project_root=root,
            check_hive=check_hive,
            store=store,
        )
        return report.model_dump(mode="json")
    except Exception as exc:
        return {
            "status": "error",
            "errors": [str(exc)],
            "warnings": [],
            "traceback": traceback.format_exc(),
        }
