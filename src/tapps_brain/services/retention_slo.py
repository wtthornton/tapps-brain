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

SLO 1 scans **tenant by tenant** (TAP-6698, Ruling 15).  ``private_memories``
has FORCED, fail-closed RLS with no admin bypass, and the service connects as a
non-bypassing role, so one unscoped query saw an empty table and reported a
clean bill of health over 6,047 real violations.  The check now sets
``app.project_id`` per tenant, in its own transaction, and accumulates — a scan,
not a bypass.

Every enumerating check returns a **bounded sample plus a true total**:
``violations`` is capped at :data:`_MAX_SAMPLE` rows (a health probe must not
stream thousands of rows) while ``violating_total`` counts the whole filtered
population, and ``sample_truncated`` says when the two differ.  Reporting only
the capped list made a live breach of 5,603 rows read as 20 (TAP-6698).
"""

from __future__ import annotations

from datetime import date
from typing import Any

import structlog
from psycopg import sql

from tapps_brain.decay import _TIER_HALF_LIFE_ATTR, DecayConfig
from tapps_brain.services.maintenance_heartbeat import has_recent_heartbeat
from tapps_brain.services.partition_manager import (
    default_partition_row_count,
    newest_partition_horizon,
)

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

_DEFAULT_HORIZON_MONTHS = 3
_DEFAULT_FLYWHEEL_LAG_HOURS = 48
_DEFAULT_HEARTBEAT_MAX_AGE_SECONDS = 7200
#: Max violating rows enumerated per check.  The cap bounds the *sample* only —
#: ``violating_total`` is always the true count, and ``ok`` is derived from that
#: total, never from the truncated list.
_MAX_SAMPLE = 20

#: Sentinel written into ``app.project_id`` by
#: :func:`_assert_transaction_local_tenant_setting`.  Never a real project id.
_TENANT_SETTING_PROBE = "__retention_slo_probe__"

#: Tenant-list sources for :func:`list_known_tenants`.
#:
#: ``private_memories`` cannot enumerate its own tenants: migration 009 gives it
#: FORCED, fail-closed RLS with **no admin bypass**, so the runtime role sees
#: nothing until ``app.project_id`` names one specific tenant.  The list must
#: therefore come from tables the runtime role may legitimately read whole:
#:
#: * ``project_profiles`` — the sanctioned project registry.  RLS'd, but with an
#:   ``app.is_admin`` bypass policy (009) that exists for exactly this kind of
#:   registry bookkeeping.
#: * the non-RLS tenanted tables — ``audit_log`` records every write, so a tenant
#:   that has ever stored a memory through the service is named there; the rest
#:   close residual gaps for tenants whose audit rows have aged out.
#:
#: Every table listed is created by a private migration, so the query cannot trip
#: over a table that only exists once some runtime path has created it
#: (``private_relations`` is built lazily by ``_ensure_relations_table`` — it was
#: measured to add no tenants ``audit_log`` does not already name, so it is left
#: out rather than guarded).
#:
#: This is a **superset heuristic, not a proof of completeness**.  A tenant whose
#: rows exist in ``private_memories`` and in none of these tables is invisible to
#: the scan — a smaller version of the very blind spot this fix closes.  That is
#: why the check reports ``rows_scanned`` against the catalog row estimate and
#: sets ``tenant_list_complete`` (see :func:`check_no_overdue_active_rows`):
#: the gap is surfaced rather than silently absorbed.
_TENANT_SOURCES_SQL = """
    SELECT project_id FROM project_profiles
    UNION SELECT project_id FROM audit_log
    UNION SELECT project_id FROM gc_archive
    UNION SELECT project_id FROM feedback_events
    UNION SELECT project_id FROM flywheel_meta
    UNION SELECT project_id FROM session_chunks
    UNION SELECT project_id FROM diagnostics_history
"""

_TABLE_ROW_ESTIMATE_SQL = (
    "SELECT reltuples::bigint FROM pg_class WHERE oid = 'public.private_memories'::regclass"
)


def _tier_half_lives(config: DecayConfig | None = None) -> dict[str, float]:
    cfg = config or DecayConfig()
    return {tier.value: float(getattr(cfg, attr)) for tier, attr in _TIER_HALF_LIFE_ATTR.items()}


def _assert_transaction_local_tenant_setting(conn: Any) -> None:
    """Verify ``SET LOCAL app.project_id`` works *and* is discarded on rollback.

    The per-tenant scan below is only correct if two things hold on this
    connection: the setting takes effect (otherwise every tenant query runs with
    no identity and RLS returns an empty view — the exact silent zero this fix
    exists to kill, Ruling 15), and it is undone by ``rollback()`` (otherwise
    tenant N's identity leaks into tenant N+1 and into whatever runs after the
    loop).  Both are checked once per probe run rather than once per tenant: two
    round trips, not 2 x |tenants|.

    A connection in autocommit mode fails the first half loudly — ``SET LOCAL``
    outside a transaction is a no-op that Postgres only warns about, and a
    warning is invisible to a health probe.

    Raises:
        RuntimeError: if the setting does not take, or survives a rollback.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('app.project_id', %s, TRUE)", (_TENANT_SETTING_PROBE,))
        cur.execute("SELECT current_setting('app.project_id', TRUE)")
        applied = cur.fetchone()[0]
    if applied != _TENANT_SETTING_PROBE:
        msg = (
            "SET LOCAL app.project_id did not take effect on this connection "
            f"(read back {applied!r}); the per-tenant retention scan would see an "
            "empty RLS view and report a false zero. Is the connection in autocommit?"
        )
        raise RuntimeError(msg)
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute("SELECT current_setting('app.project_id', TRUE)")
        after = cur.fetchone()[0]
    conn.rollback()
    if after:
        msg = (
            f"app.project_id survived rollback as {after!r}; it is not "
            "transaction-local on this connection and would leak between tenants."
        )
        raise RuntimeError(msg)


def list_known_tenants(conn: Any) -> list[str]:
    """Return every ``project_id`` the runtime role can legitimately enumerate.

    Sourced from :data:`_TENANT_SOURCES_SQL`.  ``project_profiles`` is read under
    ``app.is_admin`` — the bypass policy migration 009 provides for registry
    bookkeeping — not under any bypass on ``private_memories``, which stays
    fail-closed.  The transaction is rolled back so neither setting outlives the
    call.
    """
    with conn.cursor() as cur:
        cur.execute("SET LOCAL app.is_admin = 'true'")
        cur.execute(_TENANT_SOURCES_SQL)
        tenants = sorted({str(r[0]) for r in cur.fetchall() if r[0]})
    conn.rollback()
    return tenants


def _table_row_estimate(conn: Any) -> int:
    """Catalog row estimate for ``private_memories`` (``pg_class.reltuples``).

    ``pg_catalog`` carries no RLS, so this is readable without a tenant identity
    — which is the point: it is the only figure about the whole table the runtime
    role can see, and comparing it to the rows the scan actually visited is what
    turns an incomplete tenant list from silent into visible.  Approximate by
    nature (maintained by ANALYZE), so it is reported, never used to fail a check.
    """
    with conn.cursor() as cur:
        cur.execute(_TABLE_ROW_ESTIMATE_SQL)
        row = cur.fetchone()
    conn.rollback()
    return int(row[0]) if row and row[0] is not None else 0


def _tenant_scan_sql(half_lives: dict[str, float]) -> tuple[Any, list[Any]]:
    """``(aggregate_sql, sample_sql, values_params)`` for one tenant's scan.

    ``project_id = %s`` is bound **in addition to** the ``SET LOCAL
    app.project_id`` that makes the rows visible.  The setting is what defeats
    RLS' fail-closed default; the predicate is what keeps the arithmetic right
    for a connection that bypasses RLS anyway (a superuser, or CI's fixture
    role).  Without it, every tenant iteration under such a role would count the
    whole table and the totals would come out multiplied by the tenant count.
    """
    values_clause = sql.SQL(", ").join(sql.SQL("(%s, %s::double precision)") for _ in half_lives)
    params: list[Any] = []
    for tier, half_life in half_lives.items():
        params.extend([tier, half_life])
    scoped = sql.SQL("""
        WITH tier_half_life(tier, half_life_days) AS (VALUES {values_clause}),
        scoped AS (
            SELECT pm.project_id, pm.agent_id, pm.key, pm.tier,
                   EXTRACT(EPOCH FROM (now() - COALESCE(pm.last_reinforced, pm.updated_at)))
                       / 86400.0 AS age_days,
                   t.half_life_days,
                   (t.tier IS NULL) AS unrecognised,
                   (
                     pm.status = 'active'
                     AND (
                       t.tier IS NULL
                       OR EXTRACT(EPOCH FROM (now() - COALESCE(pm.last_reinforced, pm.updated_at)))
                              / 86400.0 > 2 * t.half_life_days
                     )
                   ) AS violating
            FROM private_memories pm
            LEFT JOIN tier_half_life t ON t.tier = pm.tier
            WHERE pm.project_id = %s
        )
    """).format(values_clause=values_clause)
    aggregate_sql = scoped + sql.SQL("""
        SELECT count(*) AS rows_scanned,
               count(*) FILTER (WHERE violating) AS violating_total,
               count(*) FILTER (WHERE violating AND unrecognised) AS unrecognised_total
        FROM scoped
    """)
    sample_sql = scoped + sql.SQL("""
        SELECT project_id, agent_id, key, tier, age_days, half_life_days,
               CASE WHEN unrecognised THEN 'unrecognised_tier' ELSE 'overdue' END AS reason
        FROM scoped
        WHERE violating
        ORDER BY unrecognised DESC, age_days DESC
        LIMIT %s
    """)
    return (aggregate_sql, sample_sql), params


def check_no_overdue_active_rows(
    conn: Any, *, config: DecayConfig | None = None, project_ids: list[str] | None = None
) -> dict[str, Any]:
    """SLO 1: no ``status='active'`` row is older than 2x its tier half-life.

    **Scans tenant by tenant (Ruling 15).**  ``private_memories`` carries FORCED,
    fail-closed RLS with no admin bypass (migration 009), and the deployed
    service connects as ``tapps_runtime`` — a non-superuser with
    ``rolbypassrls=false``.  The single unscoped query this check used to run
    executed correctly against an *empty view of the table* and reported
    ``ok=True, violating_total=0`` no matter what the data held: measured live,
    0 against 6,047 for the same predicate under a bypassing role.  A green SLO
    that cannot see its own subject is worse than a red one.

    So the check now sets ``app.project_id`` for one tenant at a time, in its own
    transaction, and accumulates.  That is a *scan*, not a bypass: no policy is
    added, no role is elevated, and the connection never holds a view wider than
    one tenant at any instant.  The operator was offered a
    ``private_memories_admin_bypass`` policy and declined it — a blanket read
    path over every tenant's private memories is precisely what migration 009
    exists to prevent.

    Cost: O(tenants) round trips where there was one (~3,350 tenant ids and
    ~1.1 s live at the time of writing).  That is why
    :data:`tapps_brain.http.probe_cache._RETENTION_SLO_PROBE_CACHE_TTL` is longer
    than the generic probe TTL — retention drift is a day-scale quantity, so the
    probe is cached for a minute rather than re-scanned per health check.

    Args:
        conn: live Postgres connection.  Must **not** be in autocommit — see
            :func:`_assert_transaction_local_tenant_setting`.
        config: decay configuration supplying tier half-lives.
        project_ids: explicit tenant list; defaults to
            :func:`list_known_tenants`.  Injecting the list keeps the scan
            testable without reproducing the whole registry.

    Returns the usual bounded-sample-plus-true-total shape, extended with the
    scan's own honesty fields: ``tenants_scanned``, ``rows_scanned``,
    ``table_row_estimate`` and ``tenant_list_complete``.  ``tenant_list_complete``
    is ``False`` when the scan visited fewer rows than the catalog believes the
    table holds — i.e. some tenant's rows were never looked at.  It is reported,
    not enforced: ``reltuples`` is an estimate, and a health check must not go
    red on a stale statistic.  ``ok`` still derives from ``violating_total``.
    """
    half_lives = _tier_half_lives(config)
    _assert_transaction_local_tenant_setting(conn)
    tenants = list_known_tenants(conn) if project_ids is None else list(project_ids)
    (aggregate_sql, sample_sql), values_params = _tenant_scan_sql(half_lives)

    violating_total = 0
    unrecognised_total = 0
    rows_scanned = 0
    # (unrecognised_first, age_days) sort key kept alongside each sampled row so
    # the per-tenant samples can be merged into one globally-ordered sample.
    sampled: list[tuple[int, float, dict[str, Any]]] = []

    for project_id in tenants:
        with conn.cursor() as cur:
            # SET LOCAL, not SET: discarded by the rollback below, so tenant N's
            # identity cannot be in force while tenant N+1 is counted.
            cur.execute("SELECT set_config('app.project_id', %s, TRUE)", (project_id,))
            cur.execute(aggregate_sql, [*values_params, project_id])
            agg = cur.fetchone()
            tenant_rows = int(agg[0]) if agg else 0
            tenant_violating = int(agg[1]) if agg else 0
            tenant_unrecognised = int(agg[2]) if agg else 0
            rows_scanned += tenant_rows
            violating_total += tenant_violating
            unrecognised_total += tenant_unrecognised
            if tenant_violating:
                cur.execute(sample_sql, [*values_params, project_id, _MAX_SAMPLE])
                for r in cur.fetchall():
                    age_days = round(float(r[4]), 2)
                    sampled.append(
                        (
                            1 if r[6] == "unrecognised_tier" else 0,
                            age_days,
                            {
                                "project_id": r[0],
                                "agent_id": r[1],
                                "key": r[2],
                                "tier": r[3],
                                "age_days": age_days,
                                "half_life_days": None if r[5] is None else float(r[5]),
                                "reason": r[6],
                            },
                        )
                    )
        conn.rollback()

    # Same ordering the single-query version produced (unrecognised tiers first,
    # then oldest), applied across tenants and re-capped so the sample stays
    # bounded no matter how many tenants contributed.
    sampled.sort(key=lambda item: (item[0], item[1]), reverse=True)
    violations = [item[2] for item in sampled[:_MAX_SAMPLE]]

    table_row_estimate = _table_row_estimate(conn)
    tenant_list_complete = rows_scanned >= table_row_estimate if table_row_estimate else None
    if tenant_list_complete is False:
        logger.warning(
            "retention_slo.tenant_list_incomplete",
            rows_scanned=rows_scanned,
            table_row_estimate=table_row_estimate,
            tenants_scanned=len(tenants),
            hint=(
                "private_memories holds rows for tenants absent from every enumerable "
                "source table; those rows are outside SLO-1's view."
            ),
        )
    return {
        "ok": violating_total == 0,
        "violating_total": violating_total,
        "unrecognised_tier_total": unrecognised_total,
        "sample_truncated": violating_total > len(violations),
        "violations": violations,
        "tenants_scanned": len(tenants),
        "rows_scanned": rows_scanned,
        "table_row_estimate": table_row_estimate,
        "tenant_list_complete": tenant_list_complete,
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
