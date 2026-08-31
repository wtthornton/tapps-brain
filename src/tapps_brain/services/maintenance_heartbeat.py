"""Maintenance-service heartbeat signal (TAP-6698).

The maintenance service is not tenant-scoped for its cross-tenant passes
(partition pre-create/drop, namespace reaper), so its "I am alive" signal
cannot live under a real ``(project_id, agent_id)`` tenant. It writes a
sentinel ``audit_log`` row every cycle under a reserved, unmistakably-fake
tenant pair instead — cheap to query, no new table, no new migration.

Consumed by two callers that must never drift apart on the definition of
"active":

* ``http_adapter.py`` at startup — warns once if
  ``TAPPS_BRAIN_EVENTS_RETENTION_MONTHS`` is set and no heartbeat exists.
* ``services/retention_slo.py`` SLO 5 — same check, exposed as a pytest node
  and in ``/healthz?deep=1``.
"""

from __future__ import annotations

from typing import Any

HEARTBEAT_PROJECT_ID = "__brain_maintenance__"
HEARTBEAT_AGENT_ID = "__brain_maintenance__"
HEARTBEAT_EVENT_TYPE = "maintenance_heartbeat"
DEFAULT_MAX_HEARTBEAT_AGE_SECONDS = 7200


def record_heartbeat(conn: Any, *, details: dict[str, Any] | None = None) -> None:
    """Write one heartbeat row. Called once per maintenance cycle, dry-run included."""
    import json

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO audit_log (project_id, agent_id, event_type, key, details)
            VALUES (%s, %s, %s, '', %s)
            """,
            (
                HEARTBEAT_PROJECT_ID,
                HEARTBEAT_AGENT_ID,
                HEARTBEAT_EVENT_TYPE,
                json.dumps(details or {}, default=str),
            ),
        )


def has_recent_heartbeat(
    conn: Any, *, max_age_seconds: int = DEFAULT_MAX_HEARTBEAT_AGE_SECONDS
) -> bool:
    """True if a heartbeat row was written within the last *max_age_seconds*."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM audit_log
                WHERE project_id = %s AND agent_id = %s AND event_type = %s
                  AND timestamp > now() - make_interval(secs => %s)
            )
            """,
            (HEARTBEAT_PROJECT_ID, HEARTBEAT_AGENT_ID, HEARTBEAT_EVENT_TYPE, max_age_seconds),
        )
        row = cur.fetchone()
    return bool(row[0])


def should_warn_missing_manager(retention_env: str, *, manager_active: bool) -> bool:
    """Pure decision: warn only when retention is configured but unenforced.

    No DB access — unit-testable in isolation from the heartbeat probe above.
    """
    return bool(retention_env.strip()) and not manager_active
