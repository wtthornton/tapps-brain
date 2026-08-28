"""Retention SLO assertions (TAP-6698, KB-3.6).

Five deterministic, read-only checks against a live Postgres connection.
Shared by ``tests/test_retention_slo.py`` (as pytest nodes) and
``/healthz?deep=1`` (as the ``retention_ok`` field) so the two surfaces can
never drift apart.

1. No row past 2x its tier half-life is ``status='active'``.
2. The newest ``experience_events`` partition is >= 3 months ahead of ``now()``.
3. ``experience_events_default`` holds 0 rows.
4. No ``flywheel_meta`` feedback cursor is > 48h behind the newest
   ``feedback_events`` row for the same ``(project_id, agent_id)``.
5. If ``TAPPS_BRAIN_EVENTS_RETENTION_MONTHS`` is set, a maintenance-service
   heartbeat must be recent (a retention setting with no active manager
   enforcing it is itself a violation).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from psycopg import sql

from tapps_brain.decay import _TIER_HALF_LIFE_ATTR, DecayConfig
from tapps_brain.services.maintenance_heartbeat import has_recent_heartbeat
from tapps_brain.services.partition_manager import (
    default_partition_row_count,
    newest_partition_horizon,
)

_DEFAULT_HORIZON_MONTHS = 3
_DEFAULT_FLYWHEEL_LAG_HOURS = 48
_DEFAULT_HEARTBEAT_MAX_AGE_SECONDS = 7200
_MAX_SAMPLE = 20


def _tier_half_lives(config: DecayConfig | None = None) -> dict[str, float]:
    cfg = config or DecayConfig()
    return {tier.value: float(getattr(cfg, attr)) for tier, attr in _TIER_HALF_LIFE_ATTR.items()}


def check_no_overdue_active_rows(conn: Any, *, config: DecayConfig | None = None) -> dict[str, Any]:
    """SLO 1: no ``status='active'`` row is older than 2x its tier half-life."""
    half_lives = _tier_half_lives(config)
    values_clause = sql.SQL(", ").join(sql.SQL("(%s, %s::double precision)") for _ in half_lives)
    params: list[Any] = []
    for tier, half_life in half_lives.items():
        params.extend([tier, half_life])

    query = sql.SQL("""
        WITH tier_half_life(tier, half_life_days) AS (VALUES {values_clause})
        SELECT pm.project_id, pm.agent_id, pm.key, pm.tier,
               EXTRACT(EPOCH FROM (now() - COALESCE(pm.last_reinforced, pm.updated_at)))
                   / 86400.0 AS age_days,
               t.half_life_days
        FROM private_memories pm
        JOIN tier_half_life t ON t.tier = pm.tier
        WHERE pm.status = 'active'
          AND EXTRACT(EPOCH FROM (now() - COALESCE(pm.last_reinforced, pm.updated_at)))
                  / 86400.0 > 2 * t.half_life_days
        ORDER BY age_days DESC
        LIMIT %s
    """).format(values_clause=values_clause)
    params.append(_MAX_SAMPLE)
    with conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()

    violations = [
        {
            "project_id": r[0],
            "agent_id": r[1],
            "key": r[2],
            "tier": r[3],
            "age_days": round(float(r[4]), 2),
            "half_life_days": float(r[5]),
        }
        for r in rows
    ]
    return {"ok": not violations, "violations": violations}


def check_partition_horizon(
    conn: Any, *, months_ahead: int = _DEFAULT_HORIZON_MONTHS
) -> dict[str, Any]:
    """SLO 2: newest ``experience_events`` partition is >= *months_ahead* out."""
    horizon = newest_partition_horizon(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT (date_trunc('month', now()) + %s)::date", (f"{months_ahead} months",))
        required = cur.fetchone()[0]
    ok = horizon is not None and horizon >= required
    return {
        "ok": ok,
        "newest_partition_upper_bound": horizon.isoformat() if horizon else None,
        "required_horizon": required.isoformat() if isinstance(required, date) else str(required),
    }


def check_default_partition_empty(conn: Any) -> dict[str, Any]:
    """SLO 3: ``experience_events_default`` holds 0 rows."""
    count = default_partition_row_count(conn)
    return {"ok": count == 0, "row_count": count}


def check_flywheel_lag(
    conn: Any, *, max_age_hours: int = _DEFAULT_FLYWHEEL_LAG_HOURS
) -> dict[str, Any]:
    """SLO 4: no ``flywheel_meta`` feedback cursor is stale relative to ``feedback_events``."""
    query = """
        SELECT fe.project_id, fe.agent_id, MAX(fe.timestamp) AS newest_feedback,
               fm.updated_at AS cursor_updated_at
        FROM feedback_events fe
        LEFT JOIN flywheel_meta fm
            ON fm.project_id = fe.project_id
           AND fm.agent_id = fe.agent_id
           AND fm.key = 'feedback_cursor'
        GROUP BY fe.project_id, fe.agent_id, fm.updated_at
        HAVING fm.updated_at IS NULL
            OR (MAX(fe.timestamp) - fm.updated_at) > (%s || ' hours')::interval
        LIMIT %s
    """
    with conn.cursor() as cur:
        cur.execute(query, (max_age_hours, _MAX_SAMPLE))
        rows = cur.fetchall()

    violations = [
        {
            "project_id": r[0],
            "agent_id": r[1],
            "newest_feedback": r[2].isoformat() if r[2] else None,
            "cursor_updated_at": r[3].isoformat() if r[3] else None,
        }
        for r in rows
    ]
    return {"ok": not violations, "violations": violations}


def check_retention_manager_active(
    conn: Any,
    *,
    retention_env: str,
    max_heartbeat_age_seconds: int = _DEFAULT_HEARTBEAT_MAX_AGE_SECONDS,
) -> dict[str, Any]:
    """SLO 5: a retention setting with no active manager enforcing it is a violation."""
    if not retention_env.strip():
        return {"ok": True, "retention_env_set": False, "manager_active": None}
    active = has_recent_heartbeat(conn, max_age_seconds=max_heartbeat_age_seconds)
    return {"ok": active, "retention_env_set": True, "manager_active": active}


def evaluate_retention_slos(
    conn: Any,
    *,
    retention_env: str = "",
    config: DecayConfig | None = None,
) -> dict[str, Any]:
    """Run all five SLO checks; return ``{"retention_ok": bool, "checks": {...}}``."""
    checks = {
        "no_overdue_active_rows": check_no_overdue_active_rows(conn, config=config),
        "partition_horizon": check_partition_horizon(conn),
        "default_partition_empty": check_default_partition_empty(conn),
        "flywheel_lag": check_flywheel_lag(conn),
        "retention_manager_active": check_retention_manager_active(
            conn, retention_env=retention_env
        ),
    }
    return {"retention_ok": all(c["ok"] for c in checks.values()), "checks": checks}
