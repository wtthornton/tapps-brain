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
        version = hive_status.current_version if hive_status.current_version else None
        pending = (
            len(hive_status.pending_migrations)
            + len(private_status.pending_migrations)
            + len(federation_status.pending_migrations)
        )
        priv_ver = private_status.current_version
        fed_ver = federation_status.current_version
        if pending > 0:
            result: tuple[bool, int | None, str] = (
                False,
                version,
                (
                    f"not ready (hive_migration={version}, private_migration={priv_ver}, "
                    f"federation_migration={fed_ver}, pending={pending})"
                ),
            )
        else:
            result = (
                True,
                version,
                (
                    f"ready (hive_migration={version}, private_migration={priv_ver}, "
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
