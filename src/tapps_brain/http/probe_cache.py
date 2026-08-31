"""Database probe and TTL-caching helpers (TAP-604).

Extracted from ``tapps_brain.http_adapter``.

The ``_probe_db`` function probes the configured Postgres DSN and caches
the result for ``_PROBE_CACHE_TTL`` seconds so that Docker healthcheck and
Prometheus scrape hits do not each open a new standalone Postgres connection
(TAP-552).
"""

from __future__ import annotations

import time
from typing import Any

# TAP-552: cache _probe_db results for 2 s so that Docker healthcheck (every 10 s)
# and Prometheus scrape (every 15 s) don't each open a new standalone Postgres
# connection.  Key = DSN string; value = (expires_at, result_tuple).
_PROBE_CACHE: dict[str, tuple[float, tuple[bool, int | None, str]]] = {}
_PROBE_CACHE_TTL: float = 2.0


def _probe_db(dsn: str | None) -> tuple[bool, int | None, str]:
    """Probe *dsn* and return ``(is_ready, migration_version, message)``."""
    if not dsn:
        return False, None, "no DSN configured (set TAPPS_BRAIN_DATABASE_URL)"
    now = time.monotonic()
    cached = _PROBE_CACHE.get(dsn)
    if cached is not None and now < cached[0]:
        return cached[1]
    try:
        from tapps_brain.postgres_migrations import (
            get_federation_schema_status,
            get_hive_schema_status,
            get_private_schema_status,
        )

        hive_status = get_hive_schema_status(dsn)
        private_status = get_private_schema_status(dsn)
        federation_status = get_federation_schema_status(dsn)
        # Top-level migration_version aligns with /info.schema_version (private
        # max). Hive/federation versions stay in the detail string.
        hive_ver = hive_status.current_version
        priv_ver = private_status.current_version
        fed_ver = federation_status.current_version
        version = priv_ver or None
        pending = (
            len(hive_status.pending_migrations)
            + len(private_status.pending_migrations)
            + len(federation_status.pending_migrations)
        )
        if pending > 0:
            result: tuple[bool, int | None, str] = (
                False,
                version,
                (
                    f"not ready (hive_migration={hive_ver}, private_migration={priv_ver}, "
                    f"federation_migration={fed_ver}, pending={pending})"
                ),
            )
        else:
            result = (
                True,
                version,
                (
                    f"ready (hive_migration={hive_ver}, private_migration={priv_ver}, "
                    f"federation_migration={fed_ver})"
                ),
            )
    except Exception as exc:
        err_str = str(exc)
        try:
            from urllib.parse import urlparse

            parsed = urlparse(dsn)
            if parsed.hostname:
                err_str = err_str.replace(parsed.hostname, "[host]")
            if parsed.port:
                err_str = err_str.replace(str(parsed.port), "[port]")
            if parsed.username:
                err_str = err_str.replace(parsed.username, "[user]")
            if parsed.password:
                err_str = err_str.replace(parsed.password, "[pass]")
        except Exception:
            err_str = "database unreachable"
        result = (False, None, f"db_error: {err_str}")
    _PROBE_CACHE[dsn] = (time.monotonic() + _PROBE_CACHE_TTL, result)
    return result


# TAP-2866: deep-probe cache for the experience write path (table + partitions).
_EXPERIENCE_PROBE_CACHE: dict[str, tuple[float, tuple[bool, str]]] = {}


def _probe_experience_schema(dsn: str | None) -> tuple[bool, str]:
    """TAP-2866: deep readiness probe for the experience-event write path.

    ``/health`` and the default ``/healthz`` only check generic DB reachability
    (``SELECT 1`` + migration version), so a missing ``experience_events``
    migration — the core ``POST /v1/experience`` write path — would read green
    while every write failed.  This probe confirms the partitioned
    ``experience_events`` table exists and has at least one partition (so inserts
    land somewhere), making a broken / un-migrated write path observable via
    ``/healthz?deep=1`` and the ``tapps_brain_experience_writable`` gauge.

    Result is cached for ``_PROBE_CACHE_TTL`` seconds (same as :func:`_probe_db`)
    so a Prometheus scrape + load-balancer probe don't each open a connection.
    Returns ``(writable, detail)``; never raises.
    """
    if not dsn:
        return False, "no DSN configured"
    now = time.monotonic()
    cached = _EXPERIENCE_PROBE_CACHE.get(dsn)
    if cached is not None and now < cached[0]:
        return cached[1]
    result: tuple[bool, str]
    try:
        import psycopg

        with psycopg.connect(dsn, connect_timeout=5) as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.experience_events')")
            row = cur.fetchone()
            if not row or row[0] is None:
                result = (False, "experience_events table missing (migration not applied)")
            else:
                cur.execute(
                    "SELECT count(*) FROM pg_catalog.pg_inherits "
                    "WHERE inhparent = 'public.experience_events'::regclass"
                )
                prow = cur.fetchone()
                partitions = int(prow[0]) if prow and prow[0] is not None else 0
                if partitions == 0:
                    result = (False, "experience_events has no partitions")
                else:
                    result = (True, f"ready ({partitions} partitions)")
    except Exception:
        result = (False, "experience_events probe failed")
    _EXPERIENCE_PROBE_CACHE[dsn] = (time.monotonic() + _PROBE_CACHE_TTL, result)
    return result


#: TAP-6698 (Ruling 15): SLO 1 now scans ``private_memories`` one tenant at a
#: time — ``private_memories`` has FORCED, fail-closed RLS and the runtime role
#: does not bypass it, so a single unscoped query saw an empty table.  That
#: turns one query into O(tenants): ~3,350 tenant ids and ~1.1 s measured live.
#: Re-running that behind the 2 s generic probe TTL would put a second of
#: Postgres work under every ``/healthz?deep=1`` (including the Docker health
#: check).  Retention drift is a day-scale quantity, so the retention verdict is
#: cached for a minute instead — the probe stays honest and ``/healthz`` stays
#: fast.  Deliberately a separate constant: the other probes are cheap and their
#: freshness matters more.
_RETENTION_SLO_PROBE_CACHE_TTL: float = 60.0

_RETENTION_SLO_PROBE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def _probe_retention_slos(dsn: str | None) -> dict[str, Any]:
    """TAP-6698: deep readiness probe for the five retention SLOs (KB-3.6).

    Same shape as :func:`_probe_experience_schema` — this is the
    ``retention_ok`` field on ``/healthz?deep=1``, backed by the same checks
    ``tests/test_retention_slo.py`` runs as pytest nodes (never two
    definitions of "retention is healthy" to drift apart). Never raises.

    Cached for :data:`_RETENTION_SLO_PROBE_CACHE_TTL` (longer than the generic
    probe TTL) because SLO 1's per-tenant RLS scan is O(tenants) — see that
    constant.
    """
    if not dsn:
        return {"retention_ok": False, "checks": {}, "detail": "no DSN configured"}
    now = time.monotonic()
    cached = _RETENTION_SLO_PROBE_CACHE.get(dsn)
    if cached is not None and now < cached[0]:
        return cached[1]
    try:
        import os

        import psycopg

        from tapps_brain.services.retention_slo import evaluate_retention_slos

        retention_env = os.environ.get("TAPPS_BRAIN_EVENTS_RETENTION_MONTHS", "")
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            result = evaluate_retention_slos(conn, retention_env=retention_env)
    except Exception as exc:
        result = {"retention_ok": False, "checks": {}, "detail": f"retention probe failed: {exc}"}
    _RETENTION_SLO_PROBE_CACHE[dsn] = (
        time.monotonic() + _RETENTION_SLO_PROBE_CACHE_TTL,
        result,
    )
    return result


def _get_hive_pool_stats(store: Any) -> dict[str, Any] | None:  # noqa: ANN401
    """Return pool stats dict from a store's hive connection manager, or None."""
    if store is None:
        return None
    try:
        hive = getattr(store, "_hive_store", None)
        cm = getattr(hive, "_cm", None)
        if cm is not None and hasattr(cm, "get_pool_stats"):
            stats: dict[str, Any] = cm.get_pool_stats()
            return stats
    except (AttributeError, TypeError):
        pass  # hive connection manager unavailable or pool_stats not exposed
    return None
