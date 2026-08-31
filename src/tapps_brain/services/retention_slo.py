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
   enforcing it is itself a violation).  Conditional by design: with no
   retention window configured the check reports ``applicable: False`` rather
   than an indistinguishable pass, and :func:`evaluate_retention_slos` lists it
   under ``not_applicable``.

Every enumerating check returns a **bounded sample plus a true total**:
``violations`` is capped at :data:`_MAX_SAMPLE` rows (a health probe must not
stream thousands of rows) while ``violating_total`` counts the whole filtered
population, and ``sample_truncated`` says when the two differ.  Reporting only
the capped list made a live breach of 5,603 rows read as 20 (TAP-6698).
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
#: Max violating rows enumerated per check.  The cap bounds the *sample* only —
#: ``violating_total`` is always the true count, and ``ok`` is derived from that
#: total, never from the truncated list.
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

    # LEFT JOIN, not INNER (TAP-6698, VAL-09): ``private_memories.tier`` is a
    # free-text column that also carries EPIC-010 profile layer names, so a row
    # can hold a tier this half-life table does not list. Under an inner join
    # those rows were dropped before the WHERE ever saw them — the check meant
    # to catch stale rows was structurally blind to exactly the rows most
    # likely to be malformed. They are surfaced as their own violation reason
    # instead: an unpriceable tier has no "2x half-life" to be measured
    # against, which is itself the defect worth reporting.
    #
    # ``count(*) OVER ()`` is evaluated over the whole filtered set *before*
    # the LIMIT, so ``violating_total`` is the true population count while
    # ``violations`` stays a bounded sample — a health probe must not stream
    # thousands of rows, but it must not report 20 when the breach is 5,603.
    query = sql.SQL("""
        WITH tier_half_life(tier, half_life_days) AS (VALUES {values_clause})
        SELECT pm.project_id, pm.agent_id, pm.key, pm.tier,
               EXTRACT(EPOCH FROM (now() - COALESCE(pm.last_reinforced, pm.updated_at)))
                   / 86400.0 AS age_days,
               t.half_life_days,
               CASE WHEN t.tier IS NULL THEN 'unrecognised_tier' ELSE 'overdue' END AS reason,
               count(*) OVER () AS violating_total,
               count(*) FILTER (WHERE t.tier IS NULL) OVER () AS unrecognised_tier_total
        FROM private_memories pm
        LEFT JOIN tier_half_life t ON t.tier = pm.tier
        WHERE pm.status = 'active'
          AND (
                t.tier IS NULL
                OR EXTRACT(EPOCH FROM (now() - COALESCE(pm.last_reinforced, pm.updated_at)))
                       / 86400.0 > 2 * t.half_life_days
              )
        ORDER BY (t.tier IS NULL) DESC, age_days DESC
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
            "half_life_days": None if r[5] is None else float(r[5]),
            "reason": r[6],
        }
        for r in rows
    ]
    # Both totals come from window aggregates over the pre-LIMIT set — deriving
    # them from ``violations`` would reintroduce the very cap this fixes.
    violating_total = int(rows[0][7]) if rows else 0
    unrecognised_total = int(rows[0][8]) if rows else 0
    return {
        "ok": violating_total == 0,
        "violating_total": violating_total,
        "unrecognised_tier_total": unrecognised_total,
        "sample_truncated": violating_total > len(violations),
        "violations": violations,
    }


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
    # Same bounded-sample-with-true-total shape as SLO 1: window functions run
    # after HAVING, so ``count(*) OVER ()`` counts every violating tenant while
    # the LIMIT keeps the enumerated sample small (TAP-6698).
    query = """
        SELECT v.*,
               count(*) OVER () AS violating_total,
               count(*) FILTER (WHERE v.cursor_missing) OVER () AS missing_cursor_total
        FROM (
            SELECT fe.project_id, fe.agent_id, MAX(fe.timestamp) AS newest_feedback,
                   fm.updated_at AS cursor_updated_at,
                   (fm.updated_at IS NULL) AS cursor_missing
            FROM feedback_events fe
            LEFT JOIN flywheel_meta fm
                ON fm.project_id = fe.project_id
               AND fm.agent_id = fe.agent_id
               AND fm.key = 'feedback_cursor'
            GROUP BY fe.project_id, fe.agent_id, fm.updated_at
            HAVING fm.updated_at IS NULL
                OR (MAX(fe.timestamp) - fm.updated_at) > (%s || ' hours')::interval
        ) v
        ORDER BY v.newest_feedback DESC
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
            "reason": "no_cursor_row" if r[4] else "stale_cursor",
        }
        for r in rows
    ]
    violating_total = int(rows[0][5]) if rows else 0
    missing_cursor_total = int(rows[0][6]) if rows else 0
    return {
        "ok": violating_total == 0,
        "violating_total": violating_total,
        "missing_cursor_total": missing_cursor_total,
        "sample_truncated": violating_total > len(violations),
        "violations": violations,
    }


def check_retention_manager_active(
    conn: Any,
    *,
    retention_env: str,
    max_heartbeat_age_seconds: int = _DEFAULT_HEARTBEAT_MAX_AGE_SECONDS,
) -> dict[str, Any]:
    """SLO 5: a retention setting with no active manager enforcing it is a violation.

    The check is **conditional by design**: with no retention window configured
    there is no retention promise to break, so there is nothing to enforce and
    nothing to fail.  That is correct — but as deployed
    (``TAPPS_BRAIN_EVENTS_RETENTION_MONTHS`` is unset on ``tapps-brain-http``)
    it means SLO 5 short-circuits without touching the DB and can never fail,
    so only four of the five SLOs actually move ``retention_ok``.

    Reporting that as a plain ``ok: True`` is what made it invisible.  The
    ``applicable`` flag says which of the two states produced the pass, and
    :func:`evaluate_retention_slos` lists inapplicable checks at the top level,
    so an operator reading ``retention_ok`` can see it was carried by four
    checks rather than five.  No failure mode is added: an unset retention
    window still passes (TAP-6698, VAL-09 defect 4).
    """
    if not retention_env.strip():
        return {
            "ok": True,
            "applicable": False,
            "reason": "no retention window configured (TAPPS_BRAIN_EVENTS_RETENTION_MONTHS unset)",
            "retention_env_set": False,
            "manager_active": None,
        }
    active = has_recent_heartbeat(conn, max_age_seconds=max_heartbeat_age_seconds)
    return {
        "ok": active,
        "applicable": True,
        "retention_env_set": True,
        "manager_active": active,
    }


def evaluate_retention_slos(
    conn: Any,
    *,
    retention_env: str = "",
    config: DecayConfig | None = None,
) -> dict[str, Any]:
    """Run all five SLO checks.

    Returns ``{"retention_ok": bool, "checks": {...}, "not_applicable": [...]}``.
    ``not_applicable`` names the checks that passed vacuously because their
    precondition was absent (currently only SLO 5, when no retention window is
    configured).  ``retention_ok`` is unchanged — an inapplicable check still
    contributes ``ok: True`` — but a caller can now tell how many checks the
    verdict actually rests on (TAP-6698).
    """
    checks = {
        "no_overdue_active_rows": check_no_overdue_active_rows(conn, config=config),
        "partition_horizon": check_partition_horizon(conn),
        "default_partition_empty": check_default_partition_empty(conn),
        "flywheel_lag": check_flywheel_lag(conn),
        "retention_manager_active": check_retention_manager_active(
            conn, retention_env=retention_env
        ),
    }
    return {
        "retention_ok": all(c["ok"] for c in checks.values()),
        "checks": checks,
        "not_applicable": sorted(
            name for name, c in checks.items() if c.get("applicable") is False
        ),
    }
