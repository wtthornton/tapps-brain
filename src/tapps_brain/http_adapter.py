"""FastAPI-based HTTP adapter for tapps-brain (EPIC-070 STORY-070.3/070.4).

Replaces the previous stdlib ``http.server.BaseHTTPRequestHandler``
implementation with a FastAPI ASGI application that:

* Preserves the **exact on-wire behavior** of the legacy data-plane and
  admin routes (paths, methods, status codes, JSON shapes).
* Mounts the MCP Streamable HTTP transport at ``/mcp`` via
  :meth:`FastMCP.streamable_http_app` (STORY-070.4).
* Applies two bearer tokens — ``TAPPS_BRAIN_AUTH_TOKEN`` for the data
  plane + ``/mcp`` and ``TAPPS_BRAIN_ADMIN_TOKEN`` for ``/admin/*`` —
  per the dual-token scheme from EPIC-069.
* Performs ``Origin``-header allow-listing on all bearer-authenticated
  routes to prevent DNS rebinding (TAP-627; previously ``/mcp`` only).
  Unauthenticated probe/scrape endpoints (``/``, ``/health``, ``/ready``,
  ``/metrics``) are explicitly exempt — see ``_ORIGIN_EXEMPT_PATHS``.
* Propagates W3C ``traceparent`` via an ASGI middleware that calls into
  :mod:`tapps_brain.otel_tracer`.

This module still exposes an :class:`HttpAdapter` class for backwards
compatibility with the Typer CLI (``tapps-brain serve``) and existing
tests — it wraps uvicorn instead of ``http.server.HTTPServer``.

The ASGI entry point is :data:`app`; run it with
``uvicorn tapps_brain.http_adapter:app`` or via the installed
``tapps-brain-http`` script.

**Split by concern (TAP-604):**

The original monolithic module has been refactored into a sub-package:

* :mod:`tapps_brain.http.settings`          - ``_Settings``, ``get_settings``
* :mod:`tapps_brain.http.probe_cache`       - ``_probe_db``, pool helpers
* :mod:`tapps_brain.http.metrics_collector` - Prometheus text rendering
* :mod:`tapps_brain.http.profile_resolver`  - singleton ``ProfileResolver``
* :mod:`tapps_brain.http.auth`              - bearer-token auth dependencies
* :mod:`tapps_brain.http.middleware`        - ASGI middleware classes

All public names are re-exported from this module for backward compat.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import platform
import sys
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from pydantic import ValidationError

try:
    from fastapi import Depends, FastAPI, HTTPException, Request, Response
    from fastapi.responses import JSONResponse, PlainTextResponse
    from starlette.middleware.base import BaseHTTPMiddleware
except ImportError as exc:  # pragma: no cover — http extra not installed
    raise ImportError(
        "tapps_brain.http_adapter requires the [http] extra.  "
        "Install it with:  uv sync --extra http  (or --extra all)."
    ) from exc

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from tapps_brain.store import MemoryStore

from tapps_brain.errors import (
    BrainDegradedError as _BrainDegradedError,
)
from tapps_brain.errors import (
    BrainRateLimitedError as _BrainRateLimitedError,
)
from tapps_brain.errors import (
    TaxonomyError as _TaxonomyError,
)

# auth dependencies
from tapps_brain.http.auth import (
    _extract_bearer,
    _metrics_request_authenticated,
    _per_tenant_auth_enabled,
    _verify_per_tenant_token,
    require_admin_auth,
    require_data_plane_auth,
)

# metrics counter state (re-exported so tests can mutate via ``_mod.X``)
from tapps_brain.http.metrics_collector import (
    _DISTINCT_AGENTS_PER_PROJECT,
    _LABELED_REQUEST_COUNTS,
    _LABELED_REQUEST_COUNTS_LOCK,
    _MAX_AGENT_ID_CARDINALITY,
    _collect_metrics,
    _emit_snapshot_metrics,
    _record_labeled_request,
    record_snapshot_build_duration,
    record_snapshot_cache_hit,
)

# middleware
from tapps_brain.http.middleware import (
    _ORIGIN_EXEMPT_PATHS,
    McpTenantMiddleware,
    OriginAllowlistMiddleware,
    OtelSpanMiddleware,
    RestProfileGateMiddleware,
    _mcp_auth_error_body,
    _peek_mcp_tool_name,
)

# probe cache
from tapps_brain.http.probe_cache import (
    _PROBE_CACHE,
    _PROBE_CACHE_TTL,
    _get_hive_pool_stats,
    _probe_db,
)

# profile resolver singleton
from tapps_brain.http.profile_resolver import (
    _PROFILE_RESOLVER,
    _PROFILE_RESOLVER_LOCK,
    _get_profile_resolver,
)

# ---------------------------------------------------------------------------
# Sub-package imports — split by concern (TAP-604)
# Re-exported at module level for backward compatibility with tests and callers
# that do ``from tapps_brain.http_adapter import <name>``.
# ---------------------------------------------------------------------------
# settings
from tapps_brain.http.settings import (
    _filter_snapshot_by_project,
    _service_version,
    _Settings,
    _settings,
    get_settings,
)
from tapps_brain.otel_tracer import SPAN_KIND_SERVER, extract_trace_context, start_span
from tapps_brain.project_registry import ProjectNotRegisteredError as _ProjectNotRegisteredError

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants / module-level state kept here for closure + test-patch access
# ---------------------------------------------------------------------------

_SERVICE_NAME = "tapps-brain"
_SNAPSHOT_TTL_SECONDS: float = 15.0
_PROCESS_START_TIME: float = time.time()
_BEARER_PREFIX = "bearer "

# Per-endpoint body-size ceilings (TAP-1940). Default for /v1/kg/* and most
# single-shot writes is 64 KB; /v1/experience is bumped to 256 KB so evidence
# payloads (stack trace + log slice + tool output) fit without consumer glue.
# /v1/experience:batch uses _EXPERIENCE_BATCH_MAX_BODY_BYTES below.
_KG_MAX_BODY_BYTES: int = 65_536
_EXPERIENCE_MAX_BODY_BYTES: int = 262_144
_EXPERIENCE_BATCH_MAX_BODY_BYTES: int = 1_048_576  # 1 MiB total for batch.
_EXPERIENCE_BATCH_MAX_ITEMS: int = 100

# STORY-070.12: bounded per-(project_id, agent_id) request counters for
# Prometheus export.  agent_id cardinality is capped at 100 distinct values
# per project; overflow is bucketed as "other".
_MAX_AGENT_ID_CARDINALITY = 100  # noqa: F811


# TAP-600: bounded project cardinality — evict least-recently-used projects
# when the project count exceeds this limit.  Default 10 000; override via
# TAPPS_BRAIN_MAX_PROJECT_CARDINALITY.  Zero or negative disables the cap
# (unbounded growth — use only in testing or single-tenant deployments).
def _parse_max_project_cardinality() -> int:
    raw = os.environ.get("TAPPS_BRAIN_MAX_PROJECT_CARDINALITY", "10000") or "10000"
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "http_adapter.max_project_cardinality.invalid",
            raw=raw,
            fallback=10000,
        )
        return 10000


_MAX_PROJECT_CARDINALITY: int = _parse_max_project_cardinality()
_LABELED_REQUEST_COUNTS: dict[tuple[str, str], int] = {}  # type: ignore[no-redef]  # noqa: F811
_LABELED_REQUEST_COUNTS_LOCK = threading.Lock()  # noqa: F811
# TAP-599: per-project set of seen agent_ids for O(1) cardinality checks.
# Maintained in lock-step with _LABELED_REQUEST_COUNTS inside the lock.
_DISTINCT_AGENTS_PER_PROJECT: dict[str, set[str]] = {}  # type: ignore[no-redef]  # noqa: F811
# TAP-600: LRU order tracker — OrderedDict preserves insertion/access order.
# Keys are project_ids; values are None.  The *first* key is the LRU project.
_PROJECT_LRU: OrderedDict[str, None] = OrderedDict()
# TAP-600: cumulative count of project evictions since process start.
_TENANT_LABELS_EVICTED_TOTAL: int = 0

# TAP-2866: per-(path, status) HTTP error counter.  Exported to Prometheus as
# tapps_brain_http_errors_total so a data-plane endpoint that is failing (e.g.
# /v1/experience 500ing) is observable even while /health reads green — the
# exact gap that hid the TAP-2865 incident for a 5-minute window.
_HTTP_ERROR_COUNTS: dict[tuple[str, str], int] = {}
_HTTP_ERROR_COUNTS_LOCK = threading.Lock()


def _record_http_error(path: str, status: int) -> None:
    """Increment the per-(path, status) HTTP error counter (TAP-2866)."""
    key = (path, str(status))
    with _HTTP_ERROR_COUNTS_LOCK:
        _HTTP_ERROR_COUNTS[key] = _HTTP_ERROR_COUNTS.get(key, 0) + 1


# STORY-073.2: process-wide ProfileResolver singleton.  Built once on first
# /mcp request; guarded by _PROFILE_RESOLVER_LOCK.
_PROFILE_RESOLVER: Any = None  # type: ignore[no-redef]  # noqa: F811
_PROFILE_RESOLVER_LOCK = threading.Lock()  # noqa: F811


def _record_labeled_request(project_id: str, agent_id: str) -> None:  # type: ignore[no-redef]  # noqa: F811
    """Increment the per-(project_id, agent_id) request counter (STORY-070.12).

    TAP-599: Uses a per-project set for O(1) membership/cardinality checks
    instead of an O(N) set-comprehension over the full _LABELED_REQUEST_COUNTS
    dict.  Both structures are updated inside the same lock so they stay in
    sync.

    TAP-600: Maintains a bounded LRU of active project_ids.  When the number
    of distinct projects exceeds ``_MAX_PROJECT_CARDINALITY`` the
    least-recently-used project is evicted from all in-memory counter
    structures and ``_TENANT_LABELS_EVICTED_TOTAL`` is incremented so
    operators can detect high project-churn via the Prometheus
    ``tapps_brain_tenant_labels_evicted_total`` counter.
    """
    global _TENANT_LABELS_EVICTED_TOTAL
    with _LABELED_REQUEST_COUNTS_LOCK:
        # --- TAP-600: LRU bookkeeping -----------------------------------
        # When _MAX_PROJECT_CARDINALITY <= 0 the LRU is disabled and
        # projects accumulate unboundedly (single-tenant / test use only).
        if _MAX_PROJECT_CARDINALITY > 0:
            if project_id in _PROJECT_LRU:
                # Move to "most recently used" position (end of OrderedDict).
                _PROJECT_LRU.move_to_end(project_id)
            else:
                # New project — evict LRU if at cap.
                if len(_PROJECT_LRU) >= _MAX_PROJECT_CARDINALITY:
                    lru_project, _ = _PROJECT_LRU.popitem(last=False)
                    # Remove all counter entries for the evicted project.
                    evict_keys = [k for k in _LABELED_REQUEST_COUNTS if k[0] == lru_project]
                    for k in evict_keys:
                        del _LABELED_REQUEST_COUNTS[k]
                    _DISTINCT_AGENTS_PER_PROJECT.pop(lru_project, None)
                    _TENANT_LABELS_EVICTED_TOTAL += 1
                _PROJECT_LRU[project_id] = None
        # --- agent cardinality cap (unchanged from TAP-599) -------------
        distinct = _DISTINCT_AGENTS_PER_PROJECT.setdefault(project_id, set())
        if agent_id not in distinct and len(distinct) >= _MAX_AGENT_ID_CARDINALITY:
            agent_id = "other"
        key = (project_id, agent_id)
        _LABELED_REQUEST_COUNTS[key] = _LABELED_REQUEST_COUNTS.get(key, 0) + 1
        # Note: when agent_id was remapped to "other" the add below can grow
        # the set to _MAX_AGENT_ID_CARDINALITY + 1.  This is intentional —
        # subsequent overflow agents still bucket to "other" via the
        # `agent_id not in distinct` check, which evaluates False for "other".
        distinct.add(agent_id)


def _get_profile_resolver() -> Any:  # type: ignore[no-redef]  # noqa: F811
    """Return the process-wide :class:`~tapps_brain.mcp_server.profile_resolver.ProfileResolver`.

    Built lazily on first call; subsequent calls return the cached singleton.
    Thread-safe via ``_PROFILE_RESOLVER_LOCK``.

    The resolver is initialised with:
    * The bundled :class:`~tapps_brain.mcp_server.profile_registry.ProfileRegistry`.
    * An optional agent-profile getter backed by ``TAPPS_BRAIN_HIVE_DSN`` or
      ``TAPPS_BRAIN_DATABASE_URL`` when a Postgres DSN is configured.
    * The ``TAPPS_BRAIN_DEFAULT_PROFILE`` env var (default ``"full"``).
    """
    global _PROFILE_RESOLVER
    if _PROFILE_RESOLVER is not None:
        return _PROFILE_RESOLVER
    with _PROFILE_RESOLVER_LOCK:
        if _PROFILE_RESOLVER is not None:
            return _PROFILE_RESOLVER
        from tapps_brain.mcp_server.profile_registry import ProfileRegistry
        from tapps_brain.mcp_server.profile_resolver import ProfileResolver

        registry = ProfileRegistry()

        # Build an agent-profile getter if a Postgres DSN is available.
        getter = None
        dsn = get_settings().dsn or os.environ.get("TAPPS_BRAIN_HIVE_DSN", "").strip()
        if dsn and (dsn.startswith("postgres://") or dsn.startswith("postgresql://")):
            try:
                from tapps_brain.postgres_connection import PostgresConnectionManager
                from tapps_brain.postgres_hive import PostgresAgentRegistry

                _cm = PostgresConnectionManager(dsn)
                _pg_agent_reg = PostgresAgentRegistry(_cm)

                def _pg_getter(project_id: str, agent_id: str) -> str | None:
                    row = _pg_agent_reg.get(agent_id)
                    if row is None:
                        return None
                    return str(row.get("profile") or "") or None

                getter = _pg_getter
            except Exception as exc:
                logger.warning(
                    "http_adapter.profile_resolver.agent_registry_unavailable",
                    error=str(exc),
                    detail=(
                        "Agent-registry lookup disabled for profile resolution. "
                        "Profile will fall back to header or server default."
                    ),
                )

        _PROFILE_RESOLVER = ProfileResolver(registry, agent_profile_getter=getter)
        return _PROFILE_RESOLVER


# ---------------------------------------------------------------------------
# OpenAPI spec — generated from FastAPI's route table and enriched with
# the dual auth schemes, tenant headers, error envelope, and the ASGI-mounted
# /mcp route by :mod:`tapps_brain.openapi_contract` (TAP-508).  The checked-in
# snapshot lives under ``docs/contracts/`` and is gated by CI.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Shared helpers (lifted verbatim from the legacy handler, behavior-identical)
# ---------------------------------------------------------------------------


def _service_version() -> str:  # type: ignore[no-redef]  # noqa: F811
    try:
        from importlib.metadata import version

        return version("tapps-brain")
    except Exception:
        return "unknown"


def _filter_snapshot_by_project(payload: dict[str, Any], project_id: str) -> dict[str, Any]:  # type: ignore[no-redef]  # noqa: F811
    """STORY-069.7: filter diagnostics/feedback to a single project_id."""
    filtered = dict(payload)
    for key in ("diagnostics_history", "feedback_events"):
        rows = filtered.get(key) or []
        filtered[key] = [
            row for row in rows if isinstance(row, dict) and row.get("project_id") == project_id
        ]
    return filtered


def _validate_uuid_field(value: Any, field_name: str) -> str:
    """TAP-2140: validate a request field is a syntactically valid UUID.

    Routes that bind values to Postgres ``UUID`` columns must validate
    at the request-model layer; otherwise psycopg raises
    ``InvalidTextRepresentation`` deep in the cursor, which surfaces as
    HTTP 500 with a raw traceback and leaks implementation detail.
    Raises ``HTTPException(422)`` with a FastAPI-style field-level
    error on failure; returns the canonical string form on success.
    """
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            status_code=422,
            detail={
                "error": "validation_error",
                "field": field_name,
                "detail": f"{field_name} must be a valid UUID",
            },
        ) from None


async def _parse_json_object_body(request: Request) -> dict[str, Any]:
    """Parse a JSON object request body with standard size and shape guards."""
    try:
        raw = await request.body()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail={"error": "bad_request", "detail": "Failed to read request body."},
        ) from None
    if not raw:
        raise HTTPException(
            status_code=400,
            detail={"error": "bad_request", "detail": "Empty request body."},
        )
    if len(raw) > 65_536:
        raise HTTPException(
            status_code=413,
            detail={"error": "payload_too_large", "detail": "Max 65536 bytes."},
        )
    try:
        body = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(
            status_code=400,
            detail={"error": "bad_request", "detail": "Request body must be valid JSON."},
        ) from None
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=400,
            detail={"error": "bad_request", "detail": "Request body must be a JSON object."},
        )
    return body


# TAP-552: cache _probe_db results for 2 s so that Docker healthcheck (every 10 s)
# and Prometheus scrape (every 15 s) don't each open a new standalone Postgres
# connection.  Key = DSN string; value = (expires_at, result_tuple).
_PROBE_CACHE: dict[str, tuple[float, tuple[bool, int | None, str]]] = {}  # type: ignore[no-redef]  # noqa: F811
_PROBE_CACHE_TTL: float = 2.0  # type: ignore[no-redef]  # noqa: F811

# TAP-2866: deep-probe cache for the experience write path (table + partitions).
_EXPERIENCE_PROBE_CACHE: dict[str, tuple[float, tuple[bool, str]]] = {}


def _probe_db(dsn: str | None) -> tuple[bool, int | None, str]:  # type: ignore[no-redef]  # noqa: F811
    if not dsn:
        return False, None, "no DSN configured (set TAPPS_BRAIN_DATABASE_URL)"
    now = time.monotonic()
    cached = _PROBE_CACHE.get(dsn)
    if cached is not None and now < cached[0]:
        return cached[1]
    try:
        from tapps_brain.postgres_migrations import get_hive_schema_status

        status_ = get_hive_schema_status(dsn)
        version = status_.current_version if status_.current_version else None
        pending = len(status_.pending_migrations)
        if pending > 0:
            result: tuple[bool, int | None, str] = (
                True,
                version,
                f"ready (migration_version={version}, pending={pending})",
            )
        else:
            result = (True, version, f"ready (migration_version={version})")
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


def _get_hive_pool_stats(store: Any) -> dict[str, Any] | None:  # type: ignore[no-redef]  # noqa: F811
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


def _collect_metrics(  # type: ignore[no-redef]  # noqa: F811
    dsn: str | None,
    store: Any = None,
    *,
    redact_tenant_labels: bool = False,
    process_start_time: float | None = None,
) -> str:
    """Render Prometheus exposition text.

    ``redact_tenant_labels`` (TAP-547): when ``True`` the per-tenant labels
    ``project_id`` and ``agent_id`` are dropped from
    ``tapps_brain_mcp_requests_total`` and ``tapps_brain_tool_calls_total``
    and the counters are aggregated across those dimensions.  This is the
    shape served to anonymous (or unauthenticated) scrapers so reachable-
    but-unprivileged callers cannot enumerate tenant/agent activity.
    """
    _start = process_start_time if process_start_time is not None else _PROCESS_START_TIME
    lines: list[str] = []

    def gauge(name: str, value: float, help_text: str = "") -> None:
        if help_text:
            lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name} {value}")

    gauge(
        "tapps_brain_process_start_time_seconds",
        _start,
        "Unix timestamp when tapps-brain HTTP adapter was started.",
    )
    gauge(
        "tapps_brain_process_uptime_seconds",
        time.time() - _start,
        "Seconds since tapps-brain HTTP adapter started.",
    )
    gauge(
        "tapps_brain_python_info",
        1.0,
        f"Python version info (version={sys.version_info.major}.{sys.version_info.minor}).",
    )

    is_ready, migration_version, _ = _probe_db(dsn)
    gauge(
        "tapps_brain_db_ready",
        1.0 if is_ready else 0.0,
        "1 if the configured Postgres database responded to a probe, 0 otherwise.",
    )
    if migration_version is not None:
        gauge(
            "tapps_brain_db_migration_version",
            float(migration_version),
            "Highest applied Hive schema migration version.",
        )

    # TAP-2866: deep write-path readiness — 1 when the experience_events table
    # and its partitions exist, 0 when the migration is missing.  Lets operators
    # alert on a broken POST /v1/experience path that tapps_brain_db_ready (a
    # generic SELECT 1) cannot see.
    experience_writable, _ = _probe_experience_schema(dsn)
    gauge(
        "tapps_brain_experience_writable",
        1.0 if experience_writable else 0.0,
        "1 if the experience_events write path (table + partitions) is present, 0 otherwise.",
    )

    # TAP-2866: per-(path, status) HTTP error counter.  No tenant labels, so it
    # is emitted in full regardless of redaction.  Only present series are
    # written; alert with `tapps_brain_http_errors_total{status=~"5.."} > 0`.
    with _HTTP_ERROR_COUNTS_LOCK:
        error_snapshot = dict(_HTTP_ERROR_COUNTS)
    lines.append(
        "# HELP tapps_brain_http_errors_total HTTP 4xx/5xx responses by path and status (TAP-2866)."
    )
    lines.append("# TYPE tapps_brain_http_errors_total counter")
    for (err_path, err_status), err_count in sorted(error_snapshot.items()):
        safe_path = err_path.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(
            f'tapps_brain_http_errors_total{{path="{safe_path}",status="{err_status}"}} {err_count}'
        )

    # TAP-600: snapshot both the eviction counter and the request counts inside
    # the same lock acquisition so they are consistent with each other.
    # Always emit tapps_brain_tenant_labels_evicted_total (even at zero) so
    # operators have a stable Prometheus series they can alert on.
    with _LABELED_REQUEST_COUNTS_LOCK:
        snapshot_evicted = _TENANT_LABELS_EVICTED_TOTAL
        snapshot_counts = dict(_LABELED_REQUEST_COUNTS)

    lines.append(
        "# HELP tapps_brain_tenant_labels_evicted_total "
        "Cumulative number of project_id entries evicted from the in-memory "
        "request-counter LRU cache (TAP-600). Non-zero indicates high "
        "project_id churn; consider increasing "
        "TAPPS_BRAIN_MAX_PROJECT_CARDINALITY."
    )
    lines.append("# TYPE tapps_brain_tenant_labels_evicted_total counter")
    lines.append(f"tapps_brain_tenant_labels_evicted_total {snapshot_evicted}")

    # STORY-070.12: per-(project_id, agent_id) request counters.
    # TAP-547: drop labels entirely when redacting — we still emit the
    # counter so scrapers have a total-volume signal, just not per-tenant.
    if snapshot_counts:
        lines.append(
            "# HELP tapps_brain_mcp_requests_total "
            "Total MCP requests, labelled by project_id and agent_id."
        )
        lines.append("# TYPE tapps_brain_mcp_requests_total counter")
        if redact_tenant_labels:
            total = sum(snapshot_counts.values())
            lines.append(f"tapps_brain_mcp_requests_total {total}")
        else:
            for (pid, aid), count in sorted(snapshot_counts.items()):
                safe_pid = pid.replace('"', '\\"')
                safe_aid = aid.replace('"', '\\"')
                lines.append(
                    f'tapps_brain_mcp_requests_total{{project_id="{safe_pid}",'
                    f'agent_id="{safe_aid}"}} {count}'
                )

    # STORY-070.12: per-(project_id, agent_id, tool, status) tool call counters.
    # TAP-547: when redacting, aggregate over (project_id, agent_id) but
    # keep (tool, status) — those are not tenant-identifying and remain
    # useful for ops / alerting on anonymous scrapes.
    # suppress(Exception): any import or runtime error must not crash /metrics.
    with suppress(Exception):  # pragma: no cover
        from tapps_brain.otel_tracer import get_tool_call_counts_snapshot

        tool_counts = get_tool_call_counts_snapshot()
        if tool_counts:
            lines.append(
                "# HELP tapps_brain_tool_calls_total "
                "Total MCP tool invocations labelled by project_id, agent_id, tool, and status."
            )
            lines.append("# TYPE tapps_brain_tool_calls_total counter")
            if redact_tenant_labels:
                aggregated: dict[tuple[str, str], int] = {}
                for (_pid, _aid, tool, status), count in tool_counts.items():
                    key = (tool, status)
                    aggregated[key] = aggregated.get(key, 0) + count
                for (tool, status), count in sorted(aggregated.items()):
                    safe_tool = tool.replace('"', '\\"')
                    safe_status = status.replace('"', '\\"')
                    lines.append(
                        f'tapps_brain_tool_calls_total{{tool="{safe_tool}",'
                        f'status="{safe_status}"}} {count}'
                    )
            else:
                for (pid, aid, tool, status), count in sorted(tool_counts.items()):
                    safe_pid = pid.replace('"', '\\"')
                    safe_aid = aid.replace('"', '\\"')
                    safe_tool = tool.replace('"', '\\"')
                    safe_status = status.replace('"', '\\"')
                    lines.append(
                        f'tapps_brain_tool_calls_total{{project_id="{safe_pid}",'
                        f'agent_id="{safe_aid}",tool="{safe_tool}",'
                        f'status="{safe_status}"}} {count}'
                    )

    # TAP-549: in-memory session-state cardinality gauge.  Alertable
    # signal for the "client rotates session_id every call" failure mode
    # — the gauge should stay well below _SESSION_STATE_HARD_CAP (10_000)
    # on a healthy adapter; sustained growth means the sweep / eviction
    # isn't keeping up.  Always emit (even when None/0) so dashboards
    # have a stable series.
    if store is not None and hasattr(store, "active_session_count"):
        with suppress(Exception):
            # Best-effort gauge — a broken store must never crash /metrics.
            gauge(
                "tapps_brain_store_active_sessions",
                float(store.active_session_count()),
                "Distinct session_ids tracked in MemoryStore in-memory "
                "implicit-feedback helper dicts.",
            )

    # STORY-066.7: live pool stats from the hive connection manager.
    _pool_stats = _get_hive_pool_stats(store)
    if _pool_stats:
        gauge(
            "tapps_brain_pool_size",
            float(_pool_stats.get("pool_size", 0)),
            "Current number of open connections in the Hive pool.",
        )
        gauge(
            "tapps_brain_pool_available",
            float(_pool_stats.get("pool_available", 0)),
            "Number of idle connections available in the Hive pool.",
        )
        gauge(
            "tapps_brain_pool_saturation",
            float(_pool_stats.get("pool_saturation", 0.0)),
            "Fraction of Hive pool max_size currently in use (0.0-1.0).",
        )
        # TAP-729: expose whether live pool stats were successfully read.
        # 0.0 means the pool is not open or get_stats() raised — operators
        # can alert on this to detect observability gaps.
        gauge(
            "tapps_brain_pool_stats_available",
            1.0 if _pool_stats.get("pool_stats_available") else 0.0,
            "1 if pool stats were successfully read from psycopg_pool; "
            "0 if the pool is not open or get_stats() raised.",
        )

    # TAP-655: per-project counter for missing HNSW indexes detected at startup.
    # Non-zero means migration 002 was not applied on that project's DB.
    # TAP-547: drop project_id label when redacting to prevent tenant enumeration.
    # suppress(Exception): any import or runtime error must not crash /metrics.
    with suppress(Exception):  # pragma: no cover
        from tapps_brain.postgres_private import get_missing_index_counts_snapshot

        missing_idx_counts = get_missing_index_counts_snapshot()
        if missing_idx_counts:
            lines.append(
                "# HELP tapps_brain_private_missing_indexes_total "
                "Number of startup checks that found idx_priv_embedding_hnsw absent "
                "(migration 002 not applied). Non-zero means vector recall falls back "
                "to a sequential scan."
            )
            lines.append("# TYPE tapps_brain_private_missing_indexes_total counter")
            if redact_tenant_labels:
                total = sum(missing_idx_counts.values())
                lines.append(f"tapps_brain_private_missing_indexes_total {total}")
            else:
                for project_id, count in sorted(missing_idx_counts.items()):
                    safe_pid = project_id.replace('"', '\\"')
                    lines.append(
                        f'tapps_brain_private_missing_indexes_total{{project_id="{safe_pid}"}} {count}'
                    )

    # STORY-073.4: profile-filter metrics (cardinality bounded by profile count × tool count).
    # suppress(Exception): any import or runtime error must not crash /metrics.
    with suppress(Exception):  # pragma: no cover
        from tapps_brain.mcp_server.tool_filter import get_profile_filter_metrics_snapshot

        _filter_snap = get_profile_filter_metrics_snapshot()

        # mcp_tools_list_total{profile}
        list_total = _filter_snap.get("list_total", {})
        if list_total:
            lines.append(
                "# HELP tapps_brain_mcp_tools_list_total Total tools/list calls per MCP profile."
            )
            lines.append("# TYPE tapps_brain_mcp_tools_list_total counter")
            for _profile, _count in sorted(list_total.items()):
                _sp = _profile.replace('"', '\\"')
                lines.append(f'tapps_brain_mcp_tools_list_total{{profile="{_sp}"}} {_count}')

        # mcp_tools_list_visible_tools{profile} — gauge
        list_visible = _filter_snap.get("list_visible", {})
        if list_visible:
            lines.append(
                "# HELP tapps_brain_mcp_tools_list_visible_tools "
                "Last observed visible tool count per MCP profile after filtering."
            )
            lines.append("# TYPE tapps_brain_mcp_tools_list_visible_tools gauge")
            for _profile, _vis in sorted(list_visible.items()):
                _sp = _profile.replace('"', '\\"')
                lines.append(f'tapps_brain_mcp_tools_list_visible_tools{{profile="{_sp}"}} {_vis}')

        # mcp_tools_call_total{profile, tool, outcome}
        call_total = _filter_snap.get("call_total", {})
        if call_total:
            lines.append(
                "# HELP tapps_brain_mcp_tools_call_total "
                "Total tools/call attempts, labelled by profile, tool, and outcome."
            )
            lines.append("# TYPE tapps_brain_mcp_tools_call_total counter")
            for (_profile, _tool, _outcome), _count in sorted(call_total.items()):
                _sp = _profile.replace('"', '\\"')
                _st = _tool.replace('"', '\\"')
                _so = _outcome.replace('"', '\\"')
                lines.append(
                    f'tapps_brain_mcp_tools_call_total{{profile="{_sp}",'
                    f'tool="{_st}",outcome="{_so}"}} {_count}'
                )

    # STORY-073.4: profile resolver resolution-source + cache metrics.
    # suppress(Exception): any runtime error must not crash /metrics.
    with suppress(Exception):  # pragma: no cover
        _resolver = _PROFILE_RESOLVER
        if _resolver is not None:
            _res_stats = _resolver.resolution_stats()
            if _res_stats:
                lines.append(
                    "# HELP tapps_brain_mcp_profile_resolution_source_total "
                    "Profile resolution source per MCP request."
                )
                lines.append("# TYPE tapps_brain_mcp_profile_resolution_source_total counter")
                for _src, _count in sorted(_res_stats.items()):
                    _ss = _src.replace('"', '\\"')
                    lines.append(
                        f'tapps_brain_mcp_profile_resolution_source_total{{source="{_ss}"}} {_count}'
                    )

            _cache = _resolver.cache_stats()
            # Only emit if at least one cache event has occurred.
            if _cache.get("hits", 0) + _cache.get("misses", 0) + _cache.get("invalidated", 0) > 0:
                lines.append(
                    "# HELP tapps_brain_mcp_profile_cache_events_total "
                    "Profile resolver cache events (hit/miss/invalidated)."
                )
                lines.append("# TYPE tapps_brain_mcp_profile_cache_events_total counter")
                # Map result label → cache_stats() key; extend here when new event types land.
                _result_to_key = {"hit": "hits", "miss": "misses", "invalidated": "invalidated"}
                for _result, _key in _result_to_key.items():
                    _count = _cache.get(_key, 0)
                    if _count:
                        lines.append(
                            f'tapps_brain_mcp_profile_cache_events_total{{result="{_result}"}} {_count}'
                        )

    _emit_snapshot_metrics(lines)

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Settings resolved from environment
# ---------------------------------------------------------------------------


class _Settings:  # type: ignore[no-redef]  # noqa: F811
    """Process-wide configuration resolved from env at app startup."""

    def __init__(self) -> None:
        self.dsn = self._resolve_dsn()
        self.auth_token = self._resolve_auth_token()
        self.admin_token = self._resolve_admin_token()
        # TAP-547: optional bearer token gating /metrics.  When set, the
        # endpoint serves the full per-(project_id, agent_id) counter
        # surface only to callers presenting the correct token; anonymous
        # callers receive a redacted (tenant-label-stripped) body.  When
        # unset, we still serve the redacted body so anonymous scrapes
        # can't enumerate tenants.
        self.metrics_token = self._resolve_metrics_token()
        self.allowed_origins = self._resolve_allowed_origins()
        self.version = _service_version()
        # Optional store injected by the CLI entry point / tests.
        self.store: MemoryStore | None = None
        # Snapshot cache
        self.snapshot_lock = threading.Lock()
        self.snapshot_cache: Any = None
        self.snapshot_cache_at: float = 0.0
        # TAP-548: process-wide ``IdempotencyStore`` singleton, built in
        # the FastAPI lifespan startup hook when
        # ``TAPPS_BRAIN_IDEMPOTENCY=1`` and a DSN is configured, and
        # closed on shutdown.  Re-using one store reuses one
        # ``PostgresConnectionManager`` pool instead of opening a fresh
        # psycopg connection per write — the previous per-request
        # construction bypassed the hardened pool and raced
        # ``max_connections`` under load.
        self.idempotency_store: Any = None
        # EPIC-072: async-native write path. Populated in lifespan startup
        # when a Postgres DSN and store are available (TAP-1117 graduated
        # this from the TAPPS_BRAIN_ASYNC_NATIVE opt-in flag).  None when no
        # async backend can be built (e.g. no DSN configured, test injection).
        self.async_store: Any = None

    @staticmethod
    def _resolve_dsn() -> str | None:
        dsn = (
            os.environ.get("TAPPS_BRAIN_DATABASE_URL")
            or os.environ.get("TAPPS_BRAIN_HIVE_DSN")
            or ""
        ).strip()
        return dsn or None

    @staticmethod
    def _read_secret(env_name: str, file_env_name: str) -> str | None:
        tok = os.environ.get(env_name, "").strip()
        if tok:
            return tok
        file_ = os.environ.get(file_env_name, "").strip()
        if file_:
            try:
                return Path(file_).read_text().strip() or None
            except OSError:
                return None
        return None

    @classmethod
    def _resolve_auth_token(cls) -> str | None:
        # STORY-070.3: accept either new (TAPPS_BRAIN_AUTH_TOKEN) or legacy
        # (TAPPS_BRAIN_HTTP_AUTH_TOKEN) name for the data-plane token.
        return cls._read_secret(
            "TAPPS_BRAIN_AUTH_TOKEN", "TAPPS_BRAIN_AUTH_TOKEN_FILE"
        ) or cls._read_secret("TAPPS_BRAIN_HTTP_AUTH_TOKEN", "TAPPS_BRAIN_HTTP_AUTH_TOKEN_FILE")

    @classmethod
    def _resolve_admin_token(cls) -> str | None:
        return cls._read_secret("TAPPS_BRAIN_ADMIN_TOKEN", "TAPPS_BRAIN_ADMIN_TOKEN_FILE")

    @classmethod
    def _resolve_metrics_token(cls) -> str | None:
        return cls._read_secret("TAPPS_BRAIN_METRICS_TOKEN", "TAPPS_BRAIN_METRICS_TOKEN_FILE")

    @staticmethod
    def _resolve_allowed_origins() -> list[str]:
        raw = (os.environ.get("TAPPS_BRAIN_ALLOWED_ORIGINS") or "").strip()
        if not raw:
            return []
        return [o.strip() for o in raw.split(",") if o.strip()]


_settings = _Settings()  # noqa: F811


def get_settings() -> _Settings:  # type: ignore[no-redef]  # noqa: F811
    return _settings


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class OtelSpanMiddleware(BaseHTTPMiddleware):  # type: ignore[no-redef]  # noqa: F811
    """Wrap each request in an OTel server span with W3C traceparent extraction."""

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        carrier: dict[str, str] = {}
        tp = request.headers.get("traceparent")
        if tp:
            carrier["traceparent"] = tp
        ts = request.headers.get("tracestate")
        if ts:
            carrier["tracestate"] = ts
        trace_ctx = extract_trace_context(carrier) if carrier else None
        method = request.method
        path = request.url.path
        project_id = request.headers.get("x-project-id", "")
        # STORY-070.7: tag spans with per-call agent identity so observability
        # can filter by tenant × agent without inspecting headers downstream.
        agent_id_header = request.headers.get("x-tapps-agent") or request.headers.get(
            "x-agent-id", ""
        )
        with start_span(
            f"{method} {path}",
            {
                "http.method": method,
                "http.route": path,
                "tapps.project_id": project_id,
                "tapps.agent_id": agent_id_header,
            },
            kind=SPAN_KIND_SERVER,
            context=trace_ctx,
        ):
            return await call_next(request)  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def _build_mcp_server() -> Any:
    """Import and build a default FastMCP instance for the ASGI mount.

    TAP-546: the unified HTTP adapter (``:8080``) is authenticated by the
    data-plane token only (``require_data_plane_auth`` /
    ``McpTenantMiddleware``).  Enabling operator tools here would let any
    data-plane caller invoke ``maintenance_gc``, ``memory_export`` etc.,
    collapsing the admin/data-plane trust boundary.  Operator tools are
    only served from the separate operator MCP transport on ``:8090``,
    which enforces ``TAPPS_BRAIN_ADMIN_TOKEN``.

    If ``TAPPS_BRAIN_OPERATOR_TOOLS=1`` is set in the HTTP adapter
    environment we log a warning and force the flag off — this is a
    documented, deliberate "fail closed" on the unified mount rather
    than an oversight.
    """
    # Emit the TAP-546 warning BEFORE importing ``tapps_brain.mcp_server``
    # below — that module reconfigures structlog globally to a CRITICAL
    # filter on import, which would silence this warning if emitted after.
    if os.environ.get("TAPPS_BRAIN_OPERATOR_TOOLS", "") == "1":
        logger.warning(
            "http_adapter.operator_tools_ignored",
            detail=(
                "TAPPS_BRAIN_OPERATOR_TOOLS=1 is set but will be ignored on "
                "the unified HTTP adapter (:8080): that mount is protected "
                "by the data-plane token only.  Operator tools are served "
                "from the operator MCP transport on :8090 "
                "(TAPPS_BRAIN_ADMIN_TOKEN). See TAP-546."
            ),
        )

    from tapps_brain.mcp_server import create_server

    project_dir = Path(os.environ.get("TAPPS_BRAIN_SERVE_ROOT", "/var/lib/tapps-brain"))
    try:
        project_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Fall back to cwd in non-container environments / tests.
        project_dir = Path.cwd()
    agent_id = os.environ.get("TAPPS_BRAIN_AGENT_ID", "http-adapter") or "http-adapter"
    return create_server(
        project_dir,
        enable_hive=True,
        agent_id=agent_id,
        enable_operator_tools=False,
    )


def create_app(
    *,
    store: MemoryStore | None = None,
    mcp_server: Any | None = None,
) -> FastAPI:
    """Build the FastAPI application.

    Parameters
    ----------
    store:
        Optional ``MemoryStore`` to expose ``/snapshot`` against.
    mcp_server:
        Pre-built FastMCP instance.  When omitted, one is created on startup.
    """
    cfg = get_settings()
    if store is not None:
        cfg.store = store

    from tapps_brain.http.settings import is_strict_mode

    if is_strict_mode():
        if not cfg.auth_token:
            raise RuntimeError(
                "TAPPS_BRAIN_STRICT=1 requires TAPPS_BRAIN_AUTH_TOKEN "
                "(or TAPPS_BRAIN_AUTH_TOKEN_FILE) to be set."
            )
        if not cfg.allowed_origins:
            raise RuntimeError(
                "TAPPS_BRAIN_STRICT=1 requires TAPPS_BRAIN_ALLOWED_ORIGINS "
                "to be set to a comma-separated list of permitted browser origins."
            )
    elif not cfg.allowed_origins:
        logger.warning(
            "http_adapter.allowed_origins_empty",
            detail=(
                "TAPPS_BRAIN_ALLOWED_ORIGINS is empty — all Origin headers "
                "are accepted.  Set this to a comma-separated list for "
                "production deployments (DNS-rebinding protection)."
            ),
        )

    # TAP-547: warn if /metrics has no bearer gate.  The endpoint still
    # serves tenant-label-redacted counters without a token, but operators
    # should set ``TAPPS_BRAIN_METRICS_TOKEN`` so Prometheus scrapers can
    # fetch the full per-tenant surface.  ``getattr`` so test fixtures
    # that hand-roll ``_Settings.__new__(_Settings)`` without the new
    # attribute keep working; missing attr → treat as unset.
    if not getattr(cfg, "metrics_token", None):
        logger.warning(
            "http_adapter.metrics_unauthenticated",
            detail=(
                "TAPPS_BRAIN_METRICS_TOKEN is unset — /metrics serves a "
                "tenant-label-redacted body to any caller that can reach "
                ":8080.  Set TAPPS_BRAIN_METRICS_TOKEN (or "
                "TAPPS_BRAIN_METRICS_TOKEN_FILE) so Prometheus scrapers "
                "can present 'Authorization: Bearer <token>' and receive "
                "the full per-(project_id, agent_id) counter surface. "
                "See TAP-547."
            ),
        )

    # Defer MCP server build so stdio-only environments can import this
    # module without paying for it.
    mcp_holder: dict[str, Any] = {"mcp": mcp_server}

    # TAP-1843: in-memory snapshot of the tool catalog, built once in lifespan.
    # Keyed by "payload" so the lifespan closure can update it without a
    # nonlocal declaration (mutable container pattern, same as mcp_holder).
    # TAP-1971: also holds "generated_at" (ISO-8601 string of build time) and
    # "etag:<key>" cache entries so per-request ETag computation is O(1).
    _tools_snapshot_holder: dict[str, Any] = {"payload": b'{"tools":[]}'}

    def _get_mcp_asgi_sub(mcp: Any) -> Any:
        """Return the Streamable HTTP ASGI sub-app from a FastMCP instance.

        TAP-509: pin FastMCP's internal route to ``/`` so when the sub-app
        is mounted at ``/mcp`` by FastAPI, the public endpoint is a single
        ``/mcp`` (not ``/mcp/mcp``).  ``streamable_http_path`` defaults to
        ``/mcp``; we override to ``/`` before building the sub-app.
        """
        settings = getattr(mcp, "settings", None)
        if settings is not None and hasattr(settings, "streamable_http_path"):
            settings.streamable_http_path = "/"
        for attr in ("streamable_http_app", "streamable_http"):
            fn = getattr(mcp, attr, None)
            if callable(fn):
                try:
                    sub = fn()
                except TypeError:
                    sub = fn
                if sub is not None:
                    return sub
        return None

    @asynccontextmanager
    async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # TAP-548: build the process-wide ``IdempotencyStore`` once at
        # adapter startup when idempotency is enabled and we have a DSN.
        # Previously each write route instantiated (and immediately tore
        # down) a fresh ``IdempotencyStore`` — each ctor spins a new
        # ``PostgresConnectionManager`` pool, so under ~20 concurrent
        # agents the unified adapter was bursting dozens of raw psycopg
        # connections against ``max_connections`` instead of reusing the
        # one hardened pool TAP-514 landed.
        # ``getattr`` so test helpers that hand-roll
        # ``_Settings.__new__(_Settings)`` without the new attribute keep
        # working; missing attr → treat as "no singleton yet".
        if getattr(cfg, "idempotency_store", None) is None and cfg.dsn:
            from tapps_brain.idempotency import (
                IdempotencyStore,
                is_idempotency_enabled,
            )

            if is_idempotency_enabled():
                try:
                    cfg.idempotency_store = IdempotencyStore(cfg.dsn)
                except Exception as exc:
                    logger.warning(
                        "http_adapter.idempotency_store_init_failed",
                        error=str(exc),
                    )
                    cfg.idempotency_store = None

        # TAP-1117: build async-native store whenever a Postgres DSN and a
        # MemoryStore are available.  Previously gated on
        # TAPPS_BRAIN_ASYNC_NATIVE=1; the flag was graduated to default and
        # removed in EPIC-072 STORY-072.7.
        if getattr(cfg, "async_store", None) is None and cfg.store is not None:
            try:
                from tapps_brain.aio import AsyncMemoryStore
                from tapps_brain.backends import create_async_private_backend

                _dsn = (cfg.dsn or "").strip()
                if _dsn.startswith(("postgres://", "postgresql://")):
                    _project_id = getattr(cfg.store, "_project_id", None) or ""
                    _agent_id = getattr(cfg.store, "_agent_id", None) or ""
                    if (
                        isinstance(_project_id, str)
                        and isinstance(_agent_id, str)
                        and _project_id
                        and _agent_id
                    ):
                        _ab = create_async_private_backend(
                            _dsn, project_id=_project_id, agent_id=_agent_id
                        )
                        cfg.async_store = AsyncMemoryStore(cfg.store, async_backend=_ab)
                        logger.info("http_adapter.async_native_store_ready")
            except Exception as exc:
                logger.warning("http_adapter.async_store_init_failed", error=str(exc))
                cfg.async_store = None

        # STORY-078.3: eager-load embedding weights during startup when required
        # so concurrent MCP tool calls do not block /healthz on cold model load.
        if os.environ.get("TAPPS_BRAIN_EMBEDDING_REQUIRED", "0") == "1":
            try:
                from tapps_brain.embeddings import (
                    embedding_startup_status,
                    get_embedding_provider,
                )

                _emb_provider = get_embedding_provider()
                logger.info(
                    "http_adapter.embedding_warmup",
                    **embedding_startup_status(_emb_provider),
                )
            except Exception as exc:
                logger.warning("http_adapter.embedding_warmup_failed", error=str(exc))

        mcp = mcp_holder["mcp"]
        if mcp is None:
            try:
                mcp = _build_mcp_server()
                mcp_holder["mcp"] = mcp
            except Exception as exc:
                logger.error("http_adapter.mcp_build_failed", error=str(exc))
                mcp = None

        session_cm = None
        if mcp is not None:
            # ORDER MATTERS: streamable_http_app() must be called BEFORE
            # accessing session_manager.  FastMCP raises RuntimeError on
            # early session_manager access (lazy init guard) — calling
            # streamable_http_app() first creates the session_manager so
            # sm.run() can start its task_group.  Without this ordering,
            # every /mcp request crashes with
            # "Task group is not initialized. Make sure to use run()."
            if "asgi_sub" not in mcp_holder:
                asgi_sub = _get_mcp_asgi_sub(mcp)
                if asgi_sub is not None:
                    _app.mount("/mcp", asgi_sub)
                    mcp_holder["asgi_sub"] = asgi_sub
                else:
                    logger.warning(
                        "http_adapter.mcp_mount_skipped",
                        detail="FastMCP did not expose a Streamable HTTP ASGI app.",
                    )

            sm = getattr(mcp, "session_manager", None)
            if sm is not None and hasattr(sm, "run"):
                try:
                    session_cm = sm.run()
                    await session_cm.__aenter__()
                except Exception as exc:
                    logger.error("http_adapter.session_manager_start_failed", error=str(exc))
                    session_cm = None

            # TAP-1843: build the static tools snapshot once at startup so
            # GET /v1/tools/list never hits the MCP registry on the hot path.
            # TAP-1929: also build a per-tool index so the endpoint can filter
            # by the caller's profile.
            #
            # The ``by_name_json`` lookup MUST include every registered tool so
            # per-profile filtering (and the REST-route drift check below) can
            # resolve deferred tools like ``brain_forget`` — TAP-1985 filtered
            # them out of ``list_tools()`` to keep the eager tools/list payload
            # within the 8-tool budget. The eager-only payload (returned when no
            # ``X-Brain-Profile`` header is set) is still built from the
            # filtered view to preserve the daily-driver wire contract.
            _tools_by_name: dict[str, dict[str, Any]] = {}
            try:
                _eager_tools = mcp._tool_manager.list_tools()
                _unfiltered_view = getattr(mcp._tool_manager, "_unfiltered_list_tools", None)
                _all_tools = _unfiltered_view() if _unfiltered_view else _eager_tools
                _tools_by_name = {
                    t.name: {
                        "name": t.name,
                        "description": t.description,
                        "inputSchema": t.parameters,
                    }
                    for t in _all_tools
                }
                _eager_payload = {
                    "tools": [
                        {
                            "name": t.name,
                            "description": t.description,
                            "inputSchema": t.parameters,
                        }
                        for t in _eager_tools
                    ]
                }
                _tools_snapshot_holder["payload"] = json.dumps(
                    _eager_payload, separators=(",", ":")
                ).encode()
                _tools_snapshot_holder["by_name_json"] = json.dumps(
                    _tools_by_name, separators=(",", ":")
                ).encode()
                # TAP-1971: tag the snapshot build time so `/v1/tools/list`
                # can expose `X-Catalog-Generated-At` without recomputing it
                # per request.  ISO-8601 UTC with second precision.
                _tools_snapshot_holder["generated_at"] = time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                )
                # Drop any cached per-profile ETags so the next request
                # recomputes against the fresh payload.
                for _k in [k for k in _tools_snapshot_holder if str(k).startswith("etag:")]:
                    _tools_snapshot_holder.pop(_k, None)
                logger.info(
                    "http_adapter.tools_snapshot_built",
                    count=len(_all_tools),
                    eager_count=len(_eager_tools),
                )
            except Exception as exc:
                logger.warning(
                    "http_adapter.tools_snapshot_build_failed",
                    error=str(exc),
                )

            # TAP-1929: REST route drift detection — MUST fail startup hard
            # when REST_ROUTE_TO_TOOL diverges from the live tool catalog.
            # Hoisted out of the snapshot-build try block so a drift ValueError
            # is not silently downgraded to a warning. Skipped only when the
            # tool catalog itself could not be built (already logged above).
            if _tools_by_name:
                from tapps_brain.http.rest_profile_gate import (
                    validate_rest_route_map,
                )

                try:
                    validate_rest_route_map(frozenset(_tools_by_name))
                except ValueError:
                    logger.error("http_adapter.rest_profile_gate_drift")
                    raise
        try:
            yield
        finally:
            if session_cm is not None:
                try:
                    await session_cm.__aexit__(None, None, None)
                except Exception:
                    logger.debug("http_adapter.session_manager_stop_failed", exc_info=True)
            # TAP-548: release the pooled Postgres connections the
            # ``IdempotencyStore`` singleton is holding.  Set back to
            # ``None`` so a subsequent lifespan run (e.g. a second
            # ``TestClient`` context on the same app) rebuilds it.
            if getattr(cfg, "idempotency_store", None) is not None:
                try:
                    cfg.idempotency_store.close()
                except Exception:
                    logger.debug(
                        "http_adapter.idempotency_store_close_failed",
                        exc_info=True,
                    )
                cfg.idempotency_store = None
            if getattr(cfg, "async_store", None) is not None:
                try:
                    await cfg.async_store.close()
                except Exception:
                    logger.debug("http_adapter.async_store_close_failed", exc_info=True)
                cfg.async_store = None

    app = FastAPI(
        title="tapps-brain HTTP API",
        version=cfg.version,
        docs_url=None,
        redoc_url=None,
        openapi_url="/openapi.json",
        lifespan=_lifespan,
    )

    # TAP-508: enrich the auto-generated spec with the dual auth schemes,
    # tenant headers, error envelope, and the ASGI-mounted /mcp route.
    # Cached on first call by FastAPI via app.openapi_schema.
    from tapps_brain.openapi_contract import build_openapi_spec as _build_openapi

    def _custom_openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            app.openapi_schema = _build_openapi(app)
        return app.openapi_schema

    app.openapi = _custom_openapi  # type: ignore[method-assign]

    # When a pre-built MCP server is provided, mount its ASGI sub-app eagerly
    # so the /mcp route exists even without a lifespan run (e.g. httpx
    # ASGITransport in tests).  Lifespan still starts the session_manager for
    # full streaming support in production.
    if mcp_server is not None:
        asgi_sub = _get_mcp_asgi_sub(mcp_server)
        if asgi_sub is not None:
            app.mount("/mcp", asgi_sub)
            mcp_holder["asgi_sub"] = asgi_sub

    # Register middlewares.  add_middleware order is reversed: last-added = outermost
    # = first to process requests.  Origin allowlist must run before MCP tenant auth
    # so a bad Origin returns 403 before the auth check can return 401/403 (TAP-627).
    # RestProfileGateMiddleware (TAP-1929) runs after MCP tenant middleware so /mcp
    # routes are unaffected and before the route handlers so /v1/* denials avoid the
    # body parse / DB hop entirely.
    app.add_middleware(OtelSpanMiddleware)
    app.add_middleware(McpTenantMiddleware)
    app.add_middleware(RestProfileGateMiddleware)
    app.add_middleware(OriginAllowlistMiddleware)

    # -------- ops routes --------

    @app.get("/", include_in_schema=False)
    async def _root() -> JSONResponse:
        return JSONResponse(
            status_code=200,
            content={"status": "ok", "service": _SERVICE_NAME, "version": cfg.version},
        )

    @app.get("/health")
    async def _health() -> JSONResponse:
        return JSONResponse(
            status_code=200,
            content={"status": "ok", "service": _SERVICE_NAME, "version": cfg.version},
        )

    @app.get("/healthz")
    async def _healthz(request: Request) -> JSONResponse:
        """Phased readiness probe used by the Docker healthcheck (TAP-1970).

        Returns a JSON body exposing each phase of readiness so consumers can
        tell ``DB unreachable`` apart from ``MCP cold-starting`` apart from
        ``drain queue flooded`` without scraping ``/metrics``.

        HTTP semantics are unchanged for Docker / load-balancers — ``200``
        when all phases are green, ``503`` otherwise. ``curl -f /healthz``
        still flips accordingly.

        Body shape (strict superset of the 3.18.0 ``{status, detail}`` shape)::

            {
              "ok":            bool,                 # db_ok AND mcp_ok
              "db_ok":         bool,                 # Postgres reachable + migrated
              "mcp_ok":        bool,                 # MCP ASGI sub-app mounted
              "queue_depth":   int,                  # write + read in-flight ops
              "circuit_state": "closed"|"degraded"|"open"|"half_open",
              "brain_version": str,                  # cfg.version
            }

        Unauthenticated and Origin-exempt so load-balancers and
        ``docker compose ps`` can reach it freely.
        """
        db_ok, _migration_version, _detail = _probe_db(cfg.dsn)
        mcp_ok = mcp_holder.get("asgi_sub") is not None

        queue_depth = 0
        _async_store = getattr(cfg, "async_store", None)
        if _async_store is not None:
            try:
                queue_depth = int(_async_store.write_queue_depth) + int(
                    _async_store.read_queue_depth
                )
            except (AttributeError, TypeError, ValueError):
                queue_depth = 0

        circuit_state = "closed"
        _store = getattr(cfg, "store", None)
        _breaker = getattr(_store, "_circuit_breaker", None) if _store is not None else None
        if _breaker is not None:
            try:
                circuit_state = str(_breaker.state)
            except (AttributeError, TypeError):
                circuit_state = "closed"

        # TAP-2866: opt-in deep probe of the experience write path.  Off by
        # default so the 10 s Docker healthcheck stays a cheap SELECT 1; pass
        # ?deep=1 (or true/yes) to additionally verify experience_events is
        # present + partitioned, so a broken core write path can't read green.
        deep = request.query_params.get("deep", "").strip().lower() in ("1", "true", "yes")
        content: dict[str, Any] = {
            "ok": db_ok and mcp_ok,
            "db_ok": db_ok,
            "mcp_ok": mcp_ok,
            "queue_depth": queue_depth,
            "circuit_state": circuit_state,
            "brain_version": cfg.version,
        }
        if deep:
            experience_ok, experience_detail = _probe_experience_schema(cfg.dsn)
            content["experience_writable"] = experience_ok
            content["experience_detail"] = experience_detail
            content["ok"] = content["ok"] and experience_ok
        return JSONResponse(status_code=200 if content["ok"] else 503, content=content)

    @app.get("/ready")
    async def _ready() -> JSONResponse:
        is_ready, migration_version, message = _probe_db(cfg.dsn)
        body: dict[str, Any] = {
            "status": "ready" if is_ready else "degraded",
            "migration_version": migration_version,
            "detail": message,
        }
        _pool_stats = _get_hive_pool_stats(cfg.store)
        if _pool_stats:
            body["pool"] = {
                "min": _pool_stats.get("pool_min"),
                "max": _pool_stats.get("pool_max"),
                "size": _pool_stats.get("pool_size"),
                "available": _pool_stats.get("pool_available"),
                "saturation": _pool_stats.get("pool_saturation"),
            }
        return JSONResponse(status_code=200 if is_ready else 503, content=body)

    @app.get("/metrics")
    async def _metrics(request: Request) -> PlainTextResponse:
        # TAP-547: serve full (project_id, agent_id)-labelled counters only
        # to holders of TAPPS_BRAIN_METRICS_TOKEN; anonymous scrapes get a
        # redacted, aggregate-only body.  Raises 401/403 if the token is
        # configured but the bearer is missing/wrong.
        authenticated = _metrics_request_authenticated(request, cfg)
        return PlainTextResponse(
            content=_collect_metrics(
                cfg.dsn,
                store=cfg.store,
                process_start_time=_PROCESS_START_TIME,
                redact_tenant_labels=not authenticated,
            ),
            status_code=200,
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    def _tools_list_response(
        content: bytes,
        cache_slot: str,
        request: Request,
    ) -> Response:
        """Build the cache-validated response for ``/v1/tools/list`` (TAP-1971).

        Adds the ``ETag``, ``Cache-Control``, ``X-Brain-Version`` and
        ``X-Catalog-Generated-At`` headers; honours ``If-None-Match`` by
        returning ``304 Not Modified`` (with the same headers, empty body)
        when the weak validator matches.  Per-slot ETags are memoised on
        :data:`_tools_snapshot_holder` so the SHA-256 runs at most once per
        snapshot-build per slot.
        """
        etag_key = f"etag:{cache_slot}"
        etag = _tools_snapshot_holder.get(etag_key)
        if not isinstance(etag, str):
            etag = f'W/"{hashlib.sha256(content).hexdigest()[:16]}"'
            _tools_snapshot_holder[etag_key] = etag
        generated_at = _tools_snapshot_holder.get("generated_at") or time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
        )
        base_headers = {
            "Cache-Control": "public, max-age=300",
            "ETag": etag,
            "X-Brain-Version": cfg.version,
            "X-Catalog-Generated-At": str(generated_at),
        }
        # If-None-Match — exact match (weak or strong) → 304 + headers, no body.
        client_etag = (request.headers.get("if-none-match") or "").strip()
        if client_etag and client_etag == etag:
            return Response(status_code=304, headers=base_headers)
        return Response(content=content, media_type="application/json", headers=base_headers)

    @app.get("/v1/tools/list")
    async def _v1_tools_list(request: Request) -> Response:
        """Return the static tool-catalog snapshot built at container startup.

        TAP-1843: zero-overhead alternative to the MCP ``tools/list`` call for
        clients that only need to enumerate tool names (load-balancers, monitoring
        probes, Ralph's cache-locality optimizer).  The payload is identical to
        the ``result.tools`` array from a MCP ``tools/list`` response — same
        per-tool ``name``, ``description``, and ``inputSchema`` fields, minus
        the JSON-RPC envelope.  Built once in the lifespan hook; never read
        live from the registry on the request path.

        TAP-1929: when the caller supplies ``X-Brain-Profile``, the response
        is filtered to the tools allowed by that profile so consumers can
        enumerate exactly what they are permitted to call.  Per-profile
        filtered snapshots are memoised in :data:`_tools_snapshot_holder`
        so the filter runs once per profile, not per request.

        TAP-1971: responses carry ``ETag`` (weak validator over the JSON
        payload), ``Cache-Control: public, max-age=300``, ``X-Brain-Version``,
        and ``X-Catalog-Generated-At`` (snapshot-build timestamp).
        ``If-None-Match`` is honoured for 304 short-circuits.

        Unauthenticated, Origin-exempt, and publicly cacheable (no secrets
        beyond the tool surface that bearer auth already protects).
        """
        header_profile = (request.headers.get("x-brain-profile") or "").strip()
        if not header_profile:
            return _tools_list_response(_tools_snapshot_holder["payload"], "unfiltered", request)

        cache_key = f"by_profile:{header_profile}"
        cached = _tools_snapshot_holder.get(cache_key)
        if cached is not None:
            return _tools_list_response(cached, cache_key, request)

        from tapps_brain.http.profile_resolver import _get_profile_resolver
        from tapps_brain.mcp_server.profile_registry import UnknownProfileError

        try:
            allowed = _get_profile_resolver()._registry.get(header_profile)
        except UnknownProfileError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "bad_request",
                    "detail": f"Unknown MCP profile {header_profile!r}.",
                    "available": exc.available,
                },
            )

        by_name_payload = _tools_snapshot_holder.get("by_name_json")
        if by_name_payload is None:
            # Snapshot not built (lifespan failed / running in tests without
            # the lifespan hook). Fall back to the unfiltered payload.
            return _tools_list_response(_tools_snapshot_holder["payload"], "unfiltered", request)

        try:
            by_name = json.loads(by_name_payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            by_name = {}
        filtered = {"tools": [by_name[name] for name in allowed if name in by_name]}
        encoded = json.dumps(filtered, separators=(",", ":")).encode()
        _tools_snapshot_holder[cache_key] = encoded
        return _tools_list_response(encoded, cache_key, request)

    @app.get("/info", dependencies=[Depends(require_data_plane_auth)])
    async def _info() -> JSONResponse:
        from tapps_brain.openapi_contract import _bundled_schema_version

        body = {
            "service": _SERVICE_NAME,
            "version": cfg.version,
            "schema_version": _bundled_schema_version(),
            "build": os.environ.get("TAPPS_BRAIN_BUILD", "unknown"),
            "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "platform": platform.system(),
            "uptime_seconds": round(time.time() - _PROCESS_START_TIME, 3),
            "auth_enabled": cfg.auth_token is not None,
            "dsn_configured": cfg.dsn is not None,
        }
        return JSONResponse(status_code=200, content=body)

    # STORY-078.1: serialize cold snapshot builds so only one thread pool
    # worker runs ``build_visual_snapshot`` at a time.  Closure-scoped like
    # TAP-629 idempotency guards — each ``create_app()`` / TestClient gets a
    # fresh lock.
    _snapshot_build_lock = asyncio.Lock()

    @app.get("/snapshot", dependencies=[Depends(require_data_plane_auth)])
    async def _snapshot(request: Request) -> Response:
        if cfg.store is None:
            return JSONResponse(
                status_code=503,
                content={"error": "no store configured"},
                headers={"Access-Control-Allow-Origin": "*"},
            )
        project_filter = request.query_params.get("project")
        project_filter = project_filter.strip() if project_filter else None
        project_filter = project_filter or None

        def _cached_snapshot(now: float) -> Any | None:
            if (
                cfg.snapshot_cache is not None
                and (now - cfg.snapshot_cache_at) < _SNAPSHOT_TTL_SECONDS
            ):
                return cfg.snapshot_cache
            return None

        # Thread-safety: ``build_visual_snapshot`` is read-only against the
        # store; ``cfg.snapshot_lock`` covers TTL cache read/write only (never
        # held across ``await asyncio.to_thread(...)``).
        snapshot: Any = None
        with cfg.snapshot_lock:
            snapshot = _cached_snapshot(time.time())

        if snapshot is not None:
            record_snapshot_cache_hit()
        else:
            build_started = time.monotonic()
            async with _snapshot_build_lock:
                with cfg.snapshot_lock:
                    snapshot = _cached_snapshot(time.time())

                if snapshot is not None:
                    record_snapshot_cache_hit()
                else:
                    from tapps_brain.visual_snapshot import build_visual_snapshot

                    built = await asyncio.to_thread(
                        build_visual_snapshot, cfg.store, privacy="standard"
                    )
                    build_duration = time.monotonic() - build_started
                    with cfg.snapshot_lock:
                        cached = _cached_snapshot(time.time())
                        if cached is not None:
                            snapshot = cached
                            record_snapshot_cache_hit()
                        else:
                            cfg.snapshot_cache = built
                            cfg.snapshot_cache_at = time.time()
                            snapshot = built
                            record_snapshot_build_duration(build_duration)

        payload = snapshot.model_dump(mode="json")
        if project_filter is not None:
            payload = _filter_snapshot_by_project(payload, project_filter)
        return JSONResponse(
            status_code=200,
            content=payload,
            headers={"Access-Control-Allow-Origin": "*"},
        )

    # -------- memory data-plane routes (EPIC-070 STORY-070.5) --------

    def _get_store_or_503() -> Any:
        """Return cfg.store or raise 503 when no store is configured."""
        if cfg.store is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "store_unavailable",
                    "detail": "No MemoryStore is configured for this adapter instance.",
                },
            )
        return cfg.store

    def _get_tenant_store_or_503(project_id: str, agent_id: str) -> Any:
        """Resolve a tenant-scoped :class:`MemoryStore` for REST data-plane routes (ADR-010).

        ``cfg.store`` is the process-default store (often built at ``serve`` startup
        for one project).  Per-request ``X-Project-Id`` must select the matching
        Postgres scope via the same :func:`_get_store_for_project` helper the MCP
        :class:`_StoreProxy` uses — passing ``project_id`` only into
        ``memory_service`` while reusing the default store silently breaks tenant
        isolation (tapps-mcp #6).
        """
        from tapps_brain.mcp_server.context import _get_store_for_project
        from tapps_brain.project_registry import ProjectNotRegisteredError

        base = _get_store_or_503()
        default_store = getattr(base, "_default_store", base)
        server_agent = (
            getattr(default_store, "_agent_id", None)
            or getattr(default_store, "agent_id", None)
            or "http-adapter"
        )
        eff_agent = (agent_id or "").strip() or "unknown"
        call_agent = eff_agent if eff_agent != server_agent else None

        try:
            return _get_store_for_project(
                project_id,
                default_store=default_store,
                enable_hive=True,
                agent_id=str(server_agent),
                call_agent_id=call_agent,
            )
        except ProjectNotRegisteredError as exc:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "project_not_registered",
                    "detail": f"Project {exc.project_id!r} is not registered.",
                    "project_id": exc.project_id,
                },
            ) from exc

    def _async_store_covers_tenant(project_id: str, agent_id: str) -> bool:
        """True when the startup async store matches the request tenant."""
        if _get_async_store_or_none() is None or cfg.store is None:
            return False
        base = getattr(cfg.store, "_default_store", cfg.store)
        store_pid = getattr(base, "_project_id", None) or ""
        store_aid = getattr(base, "_agent_id", None) or ""
        eff_agent = (agent_id or "").strip() or "unknown"
        agent_ok = eff_agent in {store_aid, "unknown"} or not store_aid
        return bool(store_pid) and store_pid == project_id and agent_ok

    def _get_async_store_or_none() -> Any:
        """Return the async-native store when wired at startup, else None."""
        return getattr(cfg, "async_store", None)

    # ------------------------------------------------------------------
    # TAP-629: per-key asyncio guards for idempotency serialization.
    #
    # Before TAP-629, two concurrent requests with the same idempotency
    # key both saw a cache miss, both ran the handler body (Postgres
    # writes, Hive propagation, metrics), and then raced to save().
    # ON CONFLICT DO NOTHING only deduplicated the stored response — not
    # the handler execution itself.
    #
    # Fix: acquire an asyncio.Lock keyed by (project_id, ikey) BEFORE
    # the cache check.  The second (and later) coroutines yield control
    # at ``await lock.acquire()`` rather than blocking the event loop.
    # When the first request completes and releases the lock, the waiting
    # coroutines wake up, re-check the cache, and short-circuit on the
    # stored response — the handler never executes twice.
    #
    # The dict is closure-scoped (not module-level) so each create_app()
    # call — and therefore each TestClient context — starts with an empty
    # table, preventing state leakage across test cases.
    # ------------------------------------------------------------------
    _idem_guards: dict[str, asyncio.Lock] = {}

    def _idem_guard_key(pid: str, ikey: str) -> str:
        return f"{pid}\x00{ikey}"

    def _ensure_idem_guard(pid: str, ikey: str) -> asyncio.Lock:
        """Return (creating if absent) the asyncio.Lock for ``(pid, ikey)``.

        Uses ``dict.setdefault`` so the check-and-insert is a single atomic
        dict operation on CPython, avoiding the two-step check-then-assign
        race if this function is ever called from an executor thread or if a
        future refactor introduces a yield point between the two lines.
        """
        gk = _idem_guard_key(pid, ikey)
        return _idem_guards.setdefault(gk, asyncio.Lock())

    def _drop_idem_guard(pid: str, ikey: str) -> None:
        """Remove the guard for ``(pid, ikey)`` when no coroutine is waiting."""
        gk = _idem_guard_key(pid, ikey)
        lk = _idem_guards.get(gk)
        if lk is not None and not lk.locked():
            _idem_guards.pop(gk, None)

    def _get_ikey_and_istore(request: Request) -> tuple[str | None, Any]:
        """Extract idempotency key + singleton store, or (None, None).

        Returns (None, None) when idempotency is disabled, the header is
        absent, or the ``IdempotencyStore`` singleton was not built at
        startup (lifespan failure / feature flag off).

        TAP-548: reads the process-wide singleton built once in the
        lifespan startup hook.
        """
        from tapps_brain.idempotency import is_idempotency_enabled

        if not is_idempotency_enabled():
            return None, None
        ikey = (request.headers.get("x-idempotency-key") or "").strip() or None
        if not ikey:
            return ikey, None
        istore = getattr(cfg, "idempotency_store", None)
        return ikey, istore

    def _idempotency_save(project_id: str, ikey: str, status: int, body: dict[str, Any]) -> None:
        """Persist idempotency key → response when enabled.

        TAP-548: writes through the process-wide
        ``cfg.idempotency_store`` singleton; silent no-op when absent so
        boot-time failures don't bubble up into write-path 500s.
        """
        from tapps_brain.idempotency import is_idempotency_enabled

        if not is_idempotency_enabled():
            return
        istore = getattr(cfg, "idempotency_store", None)
        if istore is None:
            return
        istore.save(project_id, ikey, status, body)

    @app.post("/v1/remember", dependencies=[Depends(require_data_plane_auth)])
    async def _v1_remember(request: Request) -> JSONResponse:
        """Save a memory entry.

        Accepts ``X-Idempotency-Key`` (UUID) when ``TAPPS_BRAIN_IDEMPOTENCY=1``.
        A duplicate key within 24 h replays the original response.

        Request headers:
          - ``X-Project-Id`` (required): project identifier.
          - ``X-Agent-Id`` (optional, default ``"unknown"``): agent identifier.
          - ``X-Idempotency-Key`` (optional): idempotency UUID.

        Request body (JSON):
          ``{ "key": str, "value": str, "tier"?: str, "source"?: str,
              "tags"?: list[str], "scope"?: str, "confidence"?: float,
              "agent_scope"?: str, "group"?: str }``
        """
        project_id = (request.headers.get("x-project-id") or "").strip()
        if not project_id:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "X-Project-Id header is required."},
            )
        agent_id = (request.headers.get("x-agent-id") or "").strip() or "unknown"
        store = _get_tenant_store_or_503(project_id, agent_id)

        # TAP-629: acquire per-key guard BEFORE the cache check so that
        # concurrent duplicates yield at ``await guard.acquire()`` rather
        # than racing through check → execute → save.  The second (and
        # later) coroutines wake up after the first stores its result,
        # see the cached body, and return without re-running the handler.
        ikey, istore = _get_ikey_and_istore(request)
        guard: asyncio.Lock | None = None
        if ikey and istore is not None:
            guard = _ensure_idem_guard(project_id, ikey)
            await guard.acquire()

        try:
            # Cache check — inside the guard so we observe the result
            # stored by whichever concurrent duplicate ran first.
            if ikey and istore is not None:
                _cached = await asyncio.to_thread(istore.check, project_id, ikey)
                if _cached is not None:
                    _status, _body = _cached
                    return JSONResponse(
                        status_code=_status,
                        content=_body,
                        headers={"Idempotency-Replayed": "true"},
                    )

            try:
                raw = await request.body()
            except Exception:
                logger.exception("http_adapter.read_body_failed")
                raise HTTPException(
                    status_code=400,
                    detail={"error": "bad_request", "detail": "Failed to read request body."},
                )
            if not raw:
                raise HTTPException(
                    status_code=400,
                    detail={"error": "bad_request", "detail": "Empty request body."},
                )
            if len(raw) > 65_536:
                raise HTTPException(
                    status_code=413,
                    detail={"error": "payload_too_large", "detail": "Max 65536 bytes."},
                )
            try:
                body = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                logger.exception("http_adapter.invalid_json")
                raise HTTPException(
                    status_code=400,
                    detail={"error": "bad_request", "detail": "Request body must be valid JSON."},
                )
            if not isinstance(body, dict):
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "bad_request",
                        "detail": "Request body must be a JSON object.",
                    },
                )

            mem_key = (body.get("key") or "").strip()
            mem_value = body.get("value") or ""
            if not mem_key or not mem_value:
                raise HTTPException(
                    status_code=400,
                    detail={"error": "bad_request", "detail": "key and value are required."},
                )

            from tapps_brain.services import memory_service as _ms

            _async_store = _get_async_store_or_none()
            if _async_store is not None and _async_store_covers_tenant(project_id, agent_id):
                # TAP-826: use async-native path — DB write goes through
                # AsyncPostgresPrivateBackend without blocking a thread.
                result = await _ms.async_memory_save(
                    _async_store,
                    project_id,
                    agent_id,
                    key=mem_key,
                    value=mem_value,
                    tier=body.get("tier", "pattern"),
                    source=body.get("source", "agent"),
                    tags=body.get("tags"),
                    scope=body.get("scope", "project"),
                    confidence=float(body.get("confidence", -1.0)),
                    agent_scope=body.get("agent_scope", "private"),
                    group=body.get("group"),
                )
            else:
                # TAP-1099: offload sync DB call to a worker thread so the
                # FastAPI event loop keeps serving concurrent requests instead
                # of blocking on a single in-flight psycopg round-trip.
                result = await asyncio.to_thread(
                    _ms.memory_save,
                    store,
                    project_id,
                    agent_id,
                    key=mem_key,
                    value=mem_value,
                    tier=body.get("tier", "pattern"),
                    source=body.get("source", "agent"),
                    tags=body.get("tags"),
                    scope=body.get("scope", "project"),
                    confidence=float(body.get("confidence", -1.0)),
                    agent_scope=body.get("agent_scope", "private"),
                    group=body.get("group"),
                )
            if isinstance(result, dict) and "error" in result:
                status_code = 400
            else:
                status_code = 200

            # Persist idempotency result inside the guard so that waiting
            # duplicates see the stored response when they re-check.
            if ikey and istore is not None:
                await asyncio.to_thread(istore.save, project_id, ikey, status_code, result)

            return JSONResponse(status_code=status_code, content=result)

        finally:
            # Release the per-key guard so any waiting duplicates can wake
            # up, re-check the cache, and return the stored response.
            if guard is not None:
                guard.release()
                if ikey:
                    _drop_idem_guard(project_id, ikey)

    @app.post("/v1/reinforce", dependencies=[Depends(require_data_plane_auth)])
    async def _v1_reinforce(request: Request) -> JSONResponse:
        """Reinforce an existing memory entry.

        Accepts ``X-Idempotency-Key`` (UUID) when ``TAPPS_BRAIN_IDEMPOTENCY=1``.
        A duplicate key within 24 h replays the original response.

        Request headers:
          - ``X-Project-Id`` (required): project identifier.
          - ``X-Agent-Id`` (optional, default ``"unknown"``): agent identifier.
          - ``X-Idempotency-Key`` (optional): idempotency UUID.

        Request body (JSON):
          ``{ "key": str, "confidence_boost"?: float }``
        """
        project_id = (request.headers.get("x-project-id") or "").strip()
        if not project_id:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "X-Project-Id header is required."},
            )
        agent_id = (request.headers.get("x-agent-id") or "").strip() or "unknown"
        store = _get_tenant_store_or_503(project_id, agent_id)

        # TAP-629: acquire per-key guard before cache check (see _v1_remember).
        ikey, istore = _get_ikey_and_istore(request)
        guard: asyncio.Lock | None = None
        if ikey and istore is not None:
            guard = _ensure_idem_guard(project_id, ikey)
            await guard.acquire()

        try:
            # Cache check inside the guard.
            if ikey and istore is not None:
                _cached = await asyncio.to_thread(istore.check, project_id, ikey)
                if _cached is not None:
                    _status, _body = _cached
                    return JSONResponse(
                        status_code=_status,
                        content=_body,
                        headers={"Idempotency-Replayed": "true"},
                    )

            try:
                raw = await request.body()
            except Exception:
                logger.exception("http_adapter.read_body_failed")
                raise HTTPException(
                    status_code=400,
                    detail={"error": "bad_request", "detail": "Failed to read request body."},
                )
            if not raw:
                raise HTTPException(
                    status_code=400,
                    detail={"error": "bad_request", "detail": "Empty request body."},
                )
            if len(raw) > 65_536:
                raise HTTPException(
                    status_code=413,
                    detail={"error": "payload_too_large", "detail": "Max 65536 bytes."},
                )
            try:
                body = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                logger.exception("http_adapter.invalid_json")
                raise HTTPException(
                    status_code=400,
                    detail={"error": "bad_request", "detail": "Request body must be valid JSON."},
                )
            if not isinstance(body, dict):
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "bad_request",
                        "detail": "Request body must be a JSON object.",
                    },
                )

            mem_key = (body.get("key") or "").strip()
            if not mem_key:
                raise HTTPException(
                    status_code=400,
                    detail={"error": "bad_request", "detail": "key is required."},
                )

            from tapps_brain.services import memory_service as _ms

            _async_store = _get_async_store_or_none()
            if _async_store is not None and _async_store_covers_tenant(project_id, agent_id):
                # TAP-1566: async-native reinforce — DB write goes through
                # AsyncPostgresPrivateBackend without blocking a thread.
                result = await _ms.async_memory_reinforce(
                    _async_store,
                    project_id,
                    agent_id,
                    key=mem_key,
                    confidence_boost=float(body.get("confidence_boost", 0.0)),
                )
            else:
                # TAP-1099: offload sync DB call to a worker thread.
                result = await asyncio.to_thread(
                    _ms.memory_reinforce,
                    store,
                    project_id,
                    agent_id,
                    key=mem_key,
                    confidence_boost=float(body.get("confidence_boost", 0.0)),
                )
            if isinstance(result, dict) and "error" in result:
                status_code = 400
            else:
                status_code = 200

            if ikey and istore is not None:
                await asyncio.to_thread(istore.save, project_id, ikey, status_code, result)

            return JSONResponse(status_code=status_code, content=result)

        finally:
            if guard is not None:
                guard.release()
                if ikey:
                    _drop_idem_guard(project_id, ikey)

    # -------- bulk data-plane routes (STORY-070.6) --------

    @app.post("/v1/remember:batch", dependencies=[Depends(require_data_plane_auth)])
    async def _v1_remember_batch(request: Request) -> JSONResponse:
        """Save multiple memory entries in one request (max configurable via TAPPS_BRAIN_MAX_BATCH_SIZE).

        Request headers:
          - ``X-Project-Id`` (required): project identifier.
          - ``X-Agent-Id`` (optional, default ``"unknown"``): agent identifier.

        Request body (JSON):
          ``{ "entries": [{"key": str, "value": str, ...}, ...] }``

        Response:
          ``{ "results": [...], "saved_count": int, "error_count": int }``
        """
        project_id = (request.headers.get("x-project-id") or "").strip()
        if not project_id:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "X-Project-Id header is required."},
            )
        agent_id = (request.headers.get("x-agent-id") or "").strip() or "unknown"
        store = _get_tenant_store_or_503(project_id, agent_id)

        try:
            raw = await request.body()
        except Exception:
            logger.exception("http_adapter.read_body_failed")
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Failed to read request body."},
            )
        if not raw:
            raise HTTPException(
                status_code=400, detail={"error": "bad_request", "detail": "Empty request body."}
            )
        if len(raw) > 10 * 1_048_576:  # 10 MiB
            raise HTTPException(
                status_code=413,
                detail={"error": "payload_too_large", "detail": "Max 10 MiB for batch requests."},
            )
        try:
            body = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.exception("http_adapter.invalid_json")
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Request body must be valid JSON."},
            )
        if not isinstance(body, dict):
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Request body must be a JSON object."},
            )

        entries = body.get("entries")
        if not isinstance(entries, list):
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "entries must be a JSON array."},
            )

        from tapps_brain.services import memory_service as _ms

        # TAP-1099: offload batch DB work to a worker thread.
        result = await asyncio.to_thread(
            _ms.memory_save_many, store, project_id, agent_id, entries=entries
        )
        status_code = 400 if "error" in result else 200
        return JSONResponse(status_code=status_code, content=result)

    @app.post("/v1/recall:batch", dependencies=[Depends(require_data_plane_auth)])
    async def _v1_recall_batch(request: Request) -> JSONResponse:
        """Recall against multiple queries in one request (max configurable via TAPPS_BRAIN_MAX_BATCH_SIZE).

        Request headers:
          - ``X-Project-Id`` (required): project identifier.
          - ``X-Agent-Id`` (optional, default ``"unknown"``): agent identifier.

        Request body (JSON):
          ``{ "queries": [str | {"message": str, "group"?: str}, ...] }``

        Response:
          ``{ "results": [...], "query_count": int }``
        """
        project_id = (request.headers.get("x-project-id") or "").strip()
        if not project_id:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "X-Project-Id header is required."},
            )
        agent_id = (request.headers.get("x-agent-id") or "").strip() or "unknown"
        store = _get_tenant_store_or_503(project_id, agent_id)

        try:
            raw = await request.body()
        except Exception:
            logger.exception("http_adapter.read_body_failed")
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Failed to read request body."},
            )
        if not raw:
            raise HTTPException(
                status_code=400, detail={"error": "bad_request", "detail": "Empty request body."}
            )
        if len(raw) > 10 * 1_048_576:
            raise HTTPException(
                status_code=413,
                detail={"error": "payload_too_large", "detail": "Max 10 MiB for batch requests."},
            )
        try:
            body = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.exception("http_adapter.invalid_json")
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Request body must be valid JSON."},
            )
        if not isinstance(body, dict):
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Request body must be a JSON object."},
            )

        queries = body.get("queries")
        if not isinstance(queries, list):
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "queries must be a JSON array."},
            )

        from tapps_brain.services import memory_service as _ms

        # TAP-1099: offload batch DB work to a worker thread.
        result = await asyncio.to_thread(
            _ms.memory_recall_many, store, project_id, agent_id, queries=queries
        )
        status_code = 400 if "error" in result else 200
        return JSONResponse(status_code=status_code, content=result)

    @app.post("/v1/reinforce:batch", dependencies=[Depends(require_data_plane_auth)])
    async def _v1_reinforce_batch(request: Request) -> JSONResponse:
        """Reinforce multiple memory entries in one request (max configurable via TAPPS_BRAIN_MAX_BATCH_SIZE).

        Request headers:
          - ``X-Project-Id`` (required): project identifier.
          - ``X-Agent-Id`` (optional, default ``"unknown"``): agent identifier.

        Request body (JSON):
          ``{ "entries": [{"key": str, "confidence_boost"?: float}, ...] }``

        Response:
          ``{ "results": [...], "reinforced_count": int, "error_count": int }``
        """
        project_id = (request.headers.get("x-project-id") or "").strip()
        if not project_id:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "X-Project-Id header is required."},
            )
        agent_id = (request.headers.get("x-agent-id") or "").strip() or "unknown"
        store = _get_tenant_store_or_503(project_id, agent_id)

        try:
            raw = await request.body()
        except Exception:
            logger.exception("http_adapter.read_body_failed")
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Failed to read request body."},
            )
        if not raw:
            raise HTTPException(
                status_code=400, detail={"error": "bad_request", "detail": "Empty request body."}
            )
        if len(raw) > 10 * 1_048_576:
            raise HTTPException(
                status_code=413,
                detail={"error": "payload_too_large", "detail": "Max 10 MiB for batch requests."},
            )
        try:
            body = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.exception("http_adapter.invalid_json")
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Request body must be valid JSON."},
            )
        if not isinstance(body, dict):
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Request body must be a JSON object."},
            )

        entries = body.get("entries")
        if not isinstance(entries, list):
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "entries must be a JSON array."},
            )

        from tapps_brain.services import memory_service as _ms

        _async_store = _get_async_store_or_none()
        if _async_store is not None and _async_store_covers_tenant(project_id, agent_id):
            # TAP-1566: async-native batch reinforce — each per-item write
            # goes through AsyncPostgresPrivateBackend.
            result = await _ms.async_memory_reinforce_many(
                _async_store, project_id, agent_id, entries=entries
            )
        else:
            # TAP-1099: offload batch DB work to a worker thread.
            result = await asyncio.to_thread(
                _ms.memory_reinforce_many, store, project_id, agent_id, entries=entries
            )
        status_code = 400 if "error" in result else 200
        return JSONResponse(status_code=status_code, content=result)

    # -------- agent-brain data-plane routes (TAP-993; AgentForge HTTP-only) --------

    @app.post("/v1/recall", dependencies=[Depends(require_data_plane_auth)])
    async def _v1_recall(request: Request) -> JSONResponse:
        """Recall memories matching a query.

        REST counterpart of the ``brain_recall`` MCP tool — same retrieval path,
        same filters. Read-only; no idempotency.

        Request headers:
          - ``X-Project-Id`` (required): project identifier.
          - ``X-Agent-Id`` (optional, default ``"unknown"``): agent identifier.

        Request body (JSON):
          ``{ "query": str, "max_results"?: int=5, "include_stale"?: bool=false,
              "filter_tier"?: str, "filter_tags"?: [str],
              "filter_tags_any"?: [str], "filter_memory_class"?: str }``

        Response: ``{ "results": [...], "query": str }``
        """
        project_id = (request.headers.get("x-project-id") or "").strip()
        if not project_id:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "X-Project-Id header is required."},
            )
        agent_id = (request.headers.get("x-agent-id") or "").strip() or "unknown"
        store = _get_tenant_store_or_503(project_id, agent_id)

        try:
            raw = await request.body()
        except Exception:
            logger.exception("http_adapter.read_body_failed")
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Failed to read request body."},
            )
        if not raw:
            raise HTTPException(
                status_code=400, detail={"error": "bad_request", "detail": "Empty request body."}
            )
        if len(raw) > 65_536:
            raise HTTPException(
                status_code=413,
                detail={"error": "payload_too_large", "detail": "Max 65536 bytes."},
            )
        try:
            body = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.exception("http_adapter.invalid_json")
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Request body must be valid JSON."},
            )
        if not isinstance(body, dict):
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Request body must be a JSON object."},
            )

        query = (body.get("query") or "").strip()
        if not query:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "query is required."},
            )

        max_results = int(body.get("max_results", 5))
        max_results = max(max_results, 1)

        from tapps_brain.services import memory_service as _ms

        # TAP-1099: offload sync recall (BM25 + vector + decay + Hive merge) to
        # a worker thread so the FastAPI event loop keeps serving concurrent
        # requests instead of blocking on a single in-flight DB round-trip.
        results = await asyncio.to_thread(
            _ms.brain_recall,
            store,
            project_id,
            agent_id,
            query=query,
            max_results=max_results,
            include_stale=bool(body.get("include_stale", False)),
            filter_tier=body.get("filter_tier"),
            filter_tags=body.get("filter_tags"),
            filter_tags_any=body.get("filter_tags_any"),
            filter_memory_class=body.get("filter_memory_class"),
        )
        return JSONResponse(status_code=200, content={"results": results, "query": query})

    @app.post("/v1/forget", dependencies=[Depends(require_data_plane_auth)])
    async def _v1_forget(request: Request) -> JSONResponse:
        """Archive a memory by key.

        REST counterpart of the ``brain_forget`` MCP tool. The entry is
        archived (status flip), not permanently deleted.

        Accepts ``X-Idempotency-Key`` (UUID) when ``TAPPS_BRAIN_IDEMPOTENCY=1``.
        A duplicate key within 24 h replays the original response.

        Request headers:
          - ``X-Project-Id`` (required): project identifier.
          - ``X-Agent-Id`` (optional, default ``"unknown"``): agent identifier.
          - ``X-Idempotency-Key`` (optional): idempotency UUID.

        Request body (JSON): ``{ "key": str }``

        Response: ``{ "forgotten": bool, "key": str, "reason"?: str }``
        """
        project_id = (request.headers.get("x-project-id") or "").strip()
        if not project_id:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "X-Project-Id header is required."},
            )
        agent_id = (request.headers.get("x-agent-id") or "").strip() or "unknown"
        store = _get_tenant_store_or_503(project_id, agent_id)

        ikey, istore = _get_ikey_and_istore(request)
        guard: asyncio.Lock | None = None
        if ikey and istore is not None:
            guard = _ensure_idem_guard(project_id, ikey)
            await guard.acquire()

        try:
            if ikey and istore is not None:
                _cached = await asyncio.to_thread(istore.check, project_id, ikey)
                if _cached is not None:
                    _status, _body = _cached
                    return JSONResponse(
                        status_code=_status,
                        content=_body,
                        headers={"Idempotency-Replayed": "true"},
                    )

            try:
                raw = await request.body()
            except Exception:
                logger.exception("http_adapter.read_body_failed")
                raise HTTPException(
                    status_code=400,
                    detail={"error": "bad_request", "detail": "Failed to read request body."},
                )
            if not raw:
                raise HTTPException(
                    status_code=400,
                    detail={"error": "bad_request", "detail": "Empty request body."},
                )
            if len(raw) > 65_536:
                raise HTTPException(
                    status_code=413,
                    detail={"error": "payload_too_large", "detail": "Max 65536 bytes."},
                )
            try:
                body = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                logger.exception("http_adapter.invalid_json")
                raise HTTPException(
                    status_code=400,
                    detail={"error": "bad_request", "detail": "Request body must be valid JSON."},
                )
            if not isinstance(body, dict):
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "bad_request",
                        "detail": "Request body must be a JSON object.",
                    },
                )

            mem_key = (body.get("key") or "").strip()
            if not mem_key:
                raise HTTPException(
                    status_code=400,
                    detail={"error": "bad_request", "detail": "key is required."},
                )

            from tapps_brain.services import memory_service as _ms

            _async_store = _get_async_store_or_none()
            if _async_store is not None and _async_store_covers_tenant(project_id, agent_id):
                result = await _ms.async_brain_forget(
                    _async_store, project_id, agent_id, key=mem_key
                )
            else:
                # TAP-1099: offload sync DB call to a worker thread.
                result = await asyncio.to_thread(
                    _ms.brain_forget, store, project_id, agent_id, key=mem_key
                )
            status_code = 200

            if ikey and istore is not None:
                await asyncio.to_thread(istore.save, project_id, ikey, status_code, result)

            return JSONResponse(status_code=status_code, content=result)

        finally:
            if guard is not None:
                guard.release()
                if ikey:
                    _drop_idem_guard(project_id, ikey)

    @app.post("/v1/learn_success", dependencies=[Depends(require_data_plane_auth)])
    async def _v1_learn_success(request: Request) -> JSONResponse:
        """Record a successful task outcome.

        REST counterpart of the ``brain_learn_success`` MCP tool.

        Accepts ``X-Idempotency-Key`` (UUID) when ``TAPPS_BRAIN_IDEMPOTENCY=1``.

        Request headers:
          - ``X-Project-Id`` (required): project identifier.
          - ``X-Agent-Id`` (optional, default ``"unknown"``): agent identifier.
          - ``X-Idempotency-Key`` (optional): idempotency UUID.

        Request body (JSON): ``{ "task_description": str, "task_id"?: str }``

        Response: ``{ "learned": true, "key": str }``
        """
        project_id = (request.headers.get("x-project-id") or "").strip()
        if not project_id:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "X-Project-Id header is required."},
            )
        agent_id = (request.headers.get("x-agent-id") or "").strip() or "unknown"
        store = _get_tenant_store_or_503(project_id, agent_id)

        ikey, istore = _get_ikey_and_istore(request)
        guard: asyncio.Lock | None = None
        if ikey and istore is not None:
            guard = _ensure_idem_guard(project_id, ikey)
            await guard.acquire()

        try:
            if ikey and istore is not None:
                _cached = await asyncio.to_thread(istore.check, project_id, ikey)
                if _cached is not None:
                    _status, _body = _cached
                    return JSONResponse(
                        status_code=_status,
                        content=_body,
                        headers={"Idempotency-Replayed": "true"},
                    )

            try:
                raw = await request.body()
            except Exception:
                logger.exception("http_adapter.read_body_failed")
                raise HTTPException(
                    status_code=400,
                    detail={"error": "bad_request", "detail": "Failed to read request body."},
                )
            if not raw:
                raise HTTPException(
                    status_code=400,
                    detail={"error": "bad_request", "detail": "Empty request body."},
                )
            if len(raw) > 65_536:
                raise HTTPException(
                    status_code=413,
                    detail={"error": "payload_too_large", "detail": "Max 65536 bytes."},
                )
            try:
                body = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                logger.exception("http_adapter.invalid_json")
                raise HTTPException(
                    status_code=400,
                    detail={"error": "bad_request", "detail": "Request body must be valid JSON."},
                )
            if not isinstance(body, dict):
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "bad_request",
                        "detail": "Request body must be a JSON object.",
                    },
                )

            task_description = (body.get("task_description") or "").strip()
            if not task_description:
                raise HTTPException(
                    status_code=400,
                    detail={"error": "bad_request", "detail": "task_description is required."},
                )

            from tapps_brain.services import memory_service as _ms

            _async_store = _get_async_store_or_none()
            if _async_store is not None and _async_store_covers_tenant(project_id, agent_id):
                result = await _ms.async_brain_learn_success(
                    _async_store,
                    project_id,
                    agent_id,
                    task_description=task_description,
                    task_id=str(body.get("task_id") or ""),
                )
            else:
                # TAP-1099: offload sync DB call to a worker thread.
                result = await asyncio.to_thread(
                    _ms.brain_learn_success,
                    store,
                    project_id,
                    agent_id,
                    task_description=task_description,
                    task_id=str(body.get("task_id") or ""),
                )
            status_code = 200

            if ikey and istore is not None:
                await asyncio.to_thread(istore.save, project_id, ikey, status_code, result)

            return JSONResponse(status_code=status_code, content=result)

        finally:
            if guard is not None:
                guard.release()
                if ikey:
                    _drop_idem_guard(project_id, ikey)

    @app.post("/v1/learn_failure", dependencies=[Depends(require_data_plane_auth)])
    async def _v1_learn_failure(request: Request) -> JSONResponse:
        """Record a failed task outcome.

        REST counterpart of the ``brain_learn_failure`` MCP tool.

        Accepts ``X-Idempotency-Key`` (UUID) when ``TAPPS_BRAIN_IDEMPOTENCY=1``.

        Request headers:
          - ``X-Project-Id`` (required): project identifier.
          - ``X-Agent-Id`` (optional, default ``"unknown"``): agent identifier.
          - ``X-Idempotency-Key`` (optional): idempotency UUID.

        Request body (JSON):
          ``{ "description": str, "task_id"?: str, "error"?: str }``

        Response: ``{ "learned": true, "key": str }``
        """
        project_id = (request.headers.get("x-project-id") or "").strip()
        if not project_id:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "X-Project-Id header is required."},
            )
        agent_id = (request.headers.get("x-agent-id") or "").strip() or "unknown"
        store = _get_tenant_store_or_503(project_id, agent_id)

        ikey, istore = _get_ikey_and_istore(request)
        guard: asyncio.Lock | None = None
        if ikey and istore is not None:
            guard = _ensure_idem_guard(project_id, ikey)
            await guard.acquire()

        try:
            if ikey and istore is not None:
                _cached = await asyncio.to_thread(istore.check, project_id, ikey)
                if _cached is not None:
                    _status, _body = _cached
                    return JSONResponse(
                        status_code=_status,
                        content=_body,
                        headers={"Idempotency-Replayed": "true"},
                    )

            try:
                raw = await request.body()
            except Exception:
                logger.exception("http_adapter.read_body_failed")
                raise HTTPException(
                    status_code=400,
                    detail={"error": "bad_request", "detail": "Failed to read request body."},
                )
            if not raw:
                raise HTTPException(
                    status_code=400,
                    detail={"error": "bad_request", "detail": "Empty request body."},
                )
            if len(raw) > 65_536:
                raise HTTPException(
                    status_code=413,
                    detail={"error": "payload_too_large", "detail": "Max 65536 bytes."},
                )
            try:
                body = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                logger.exception("http_adapter.invalid_json")
                raise HTTPException(
                    status_code=400,
                    detail={"error": "bad_request", "detail": "Request body must be valid JSON."},
                )
            if not isinstance(body, dict):
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "bad_request",
                        "detail": "Request body must be a JSON object.",
                    },
                )

            description = (body.get("description") or "").strip()
            if not description:
                raise HTTPException(
                    status_code=400,
                    detail={"error": "bad_request", "detail": "description is required."},
                )

            from tapps_brain.services import memory_service as _ms

            _async_store = _get_async_store_or_none()
            if _async_store is not None and _async_store_covers_tenant(project_id, agent_id):
                result = await _ms.async_brain_learn_failure(
                    _async_store,
                    project_id,
                    agent_id,
                    description=description,
                    task_id=str(body.get("task_id") or ""),
                    error=str(body.get("error") or ""),
                )
            else:
                # TAP-1099: offload sync DB call to a worker thread.
                result = await asyncio.to_thread(
                    _ms.brain_learn_failure,
                    store,
                    project_id,
                    agent_id,
                    description=description,
                    task_id=str(body.get("task_id") or ""),
                    error=str(body.get("error") or ""),
                )
            status_code = 200

            if ikey and istore is not None:
                await asyncio.to_thread(istore.save, project_id, ikey, status_code, result)

            return JSONResponse(status_code=status_code, content=result)

        finally:
            if guard is not None:
                guard.release()
                if ikey:
                    _drop_idem_guard(project_id, ikey)

    # -------- KG + experience routes (EPIC-076 STORY-076.5) --------

    def _get_kg_cm_or_503() -> Any:
        """Return a process-level connection manager for KG/experience routes.

        Raises HTTP 503 when ``TAPPS_BRAIN_DATABASE_URL`` is not configured.
        """
        from tapps_brain.services import kg_service as _kg_svc

        cm = _kg_svc._get_or_create_cm()
        if cm is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "db_unavailable",
                    "detail": "TAPPS_BRAIN_DATABASE_URL is not configured.",
                },
            )
        return cm

    def _kg_brain_id() -> str:
        from tapps_brain.services import kg_service as _kg_svc

        return _kg_svc._DEFAULT_BRAIN_ID

    @app.post("/v1/experience", dependencies=[Depends(require_data_plane_auth)])
    async def _v1_experience(request: Request) -> JSONResponse:
        """Record an experience event with optional KG side-effects.

        REST counterpart of the ``brain_record_event`` MCP tool.  All writes
        (event row + optional memory + entity + edge + evidence) happen in one
        Postgres transaction.

        Request headers:
          - ``X-Project-Id`` (required): project identifier.
          - ``X-Agent-Id`` (optional, default ``"unknown"``): agent identifier.

        Request body (JSON):
          ``{ "event_type": str, "subject_key"?: str, "utility_score"?: float,
              "payload"?: dict, "entities"?: [EntitySpec], "edges"?: [EdgeSpec],
              "evidence"?: [EvidenceSpec], "memory_key"?: str,
              "memory_value"?: str, "memory_tier"?: str,
              "session_id"?: str, "workflow_run_id"?: str }``

        Response: ``{ "event_id": str, "memory_key": str|null,
        "entity_ids": [str], "edge_ids": [str], "evidence_ids": [str] }``
        """
        project_id = (request.headers.get("x-project-id") or "").strip()
        if not project_id:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "X-Project-Id header is required."},
            )
        agent_id = (request.headers.get("x-agent-id") or "").strip() or "unknown"

        try:
            raw = await request.body()
        except Exception:
            logger.exception("http_adapter.kg.read_body_failed")
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Failed to read request body."},
            )
        if not raw:
            raise HTTPException(
                status_code=400, detail={"error": "bad_request", "detail": "Empty request body."}
            )
        # TAP-1940: /v1/experience uses a higher 256 KB ceiling than the 64 KB
        # default applied to /v1/kg/* endpoints, so evidence payloads (stack
        # traces + log slices + tool output) fit without consumer-side glue.
        if len(raw) > _EXPERIENCE_MAX_BODY_BYTES:
            raise HTTPException(
                status_code=413,
                detail={
                    "error": "payload_too_large",
                    "detail": f"Max {_EXPERIENCE_MAX_BODY_BYTES} bytes.",
                },
            )
        try:
            body = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Request body must be valid JSON."},
            )
        if not isinstance(body, dict):
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Request body must be a JSON object."},
            )

        event_type = (body.get("event_type") or "").strip()
        if not event_type:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "event_type is required."},
            )

        cm = _get_kg_cm_or_503()
        from tapps_brain.services import kg_service as _kg_svc

        result = await asyncio.to_thread(
            _kg_svc.record_event,
            cm,
            project_id,
            _kg_brain_id(),
            agent_id,
            event_type=event_type,
            subject_key=body.get("subject_key") or None,
            utility_score=float(body.get("utility_score", 0.0)),
            payload=body.get("payload") or {},
            entities=list(body.get("entities") or []),
            edges=list(body.get("edges") or []),
            evidence=list(body.get("evidence") or []),
            memory_key=body.get("memory_key") or None,
            memory_value=body.get("memory_value") or None,
            memory_tier=str(body.get("memory_tier") or "pattern"),
            session_id=body.get("session_id") or None,
            workflow_run_id=body.get("workflow_run_id") or None,
        )
        # TAP-2727: data-plane writes return 200 (matches /v1/remember,
        # /v1/learn_*, /v1/reinforce and the documented OpenAPI contract).
        return JSONResponse(status_code=200, content=result)

    @app.post("/v1/experience:batch", dependencies=[Depends(require_data_plane_auth)])
    async def _v1_experience_batch(request: Request) -> JSONResponse:
        """Atomically record a batch of experience events (TAP-1934).

        REST counterpart of :func:`tapps_brain.services.kg_service.record_events_batch`.
        Patterned after :func:`/v1/reinforce:batch` from v3.17.0.  All events
        write in **one Postgres transaction**: any failure rolls back the
        entire batch — no partial commits.

        Request headers:
          - ``X-Project-Id`` (required): project identifier.
          - ``X-Agent-Id`` (optional, default ``"unknown"``): agent identifier.

        Request body (JSON):
          ``{ "events": [event1, event2, ...] }`` where each event matches the
          single-event ``/v1/experience`` schema.

        Limits:
          - Total batch body capped at 1 MiB.
          - Up to 100 events per request (overflow → 400).

        Response:
          ``{ "results": [...], "count": int }`` with one result per event in
          input order.
        """
        project_id = (request.headers.get("x-project-id") or "").strip()
        if not project_id:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "X-Project-Id header is required."},
            )
        agent_id = (request.headers.get("x-agent-id") or "").strip() or "unknown"

        try:
            raw = await request.body()
        except Exception:
            logger.exception("http_adapter.kg.batch.read_body_failed")
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Failed to read request body."},
            )
        if not raw:
            raise HTTPException(
                status_code=400, detail={"error": "bad_request", "detail": "Empty request body."}
            )
        if len(raw) > _EXPERIENCE_BATCH_MAX_BODY_BYTES:
            raise HTTPException(
                status_code=413,
                detail={
                    "error": "payload_too_large",
                    "detail": f"Max {_EXPERIENCE_BATCH_MAX_BODY_BYTES} bytes for batch requests.",
                },
            )
        try:
            body = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Request body must be valid JSON."},
            )
        if not isinstance(body, dict):
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Request body must be a JSON object."},
            )

        events = body.get("events")
        if not isinstance(events, list):
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "events must be a JSON array."},
            )
        if len(events) > _EXPERIENCE_BATCH_MAX_ITEMS:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "bad_request",
                    "detail": f"Max {_EXPERIENCE_BATCH_MAX_ITEMS} events per batch.",
                },
            )

        cm = _get_kg_cm_or_503()
        from tapps_brain.services import kg_service as _kg_svc

        result = await asyncio.to_thread(
            _kg_svc.record_events_batch,
            cm,
            project_id,
            _kg_brain_id(),
            agent_id,
            events=events,
        )
        if isinstance(result, dict) and result.get("error"):
            raise HTTPException(status_code=400, detail=result)
        # TAP-2727: data-plane writes return 200 (matches the single-event
        # /v1/experience endpoint and the documented OpenAPI contract).
        return JSONResponse(status_code=200, content=result)

    @app.post("/v1/experience:query", dependencies=[Depends(require_data_plane_auth)])
    async def _v1_experience_query(request: Request) -> JSONResponse:
        """Query experience events with full payload round-trip.

        REST counterpart of the ``brain_query_events`` MCP tool (TAP-3157).
        Returns stored ``experience_events.payload`` JSONB for metrics and
        dashboard consumers.

        Request headers:
          - ``X-Project-Id`` (required): project identifier.

        Request body (JSON):
          ``{ "event_type": str, "since"?: str, "until"?: str,
              "entity_id"?: str, "limit"?: int }``

        Response: ``{ "events": [{event_id, event_type, payload, ts,
        agent_id, session_id?}], "count": int }``
        """
        project_id = (request.headers.get("x-project-id") or "").strip()
        if not project_id:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "X-Project-Id header is required."},
            )

        try:
            raw = await request.body()
        except Exception:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Failed to read request body."},
            )
        if not raw:
            raise HTTPException(
                status_code=400, detail={"error": "bad_request", "detail": "Empty request body."}
            )
        if len(raw) > 65_536:
            raise HTTPException(
                status_code=413,
                detail={"error": "payload_too_large", "detail": "Max 65536 bytes."},
            )
        try:
            body = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Request body must be valid JSON."},
            )
        if not isinstance(body, dict):
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Request body must be a JSON object."},
            )

        event_type = (body.get("event_type") or "").strip()
        if not event_type:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "event_type is required."},
            )

        cm = _get_kg_cm_or_503()
        from tapps_brain.services import kg_service as _kg_svc

        result = await asyncio.to_thread(
            _kg_svc.query_events,
            cm,
            project_id,
            event_type=event_type,
            since=body.get("since") or None,
            until=body.get("until") or None,
            entity_id=body.get("entity_id") or None,
            limit=int(body.get("limit", _kg_svc._QUERY_EVENTS_DEFAULT_LIMIT)),
        )
        if isinstance(result, dict) and result.get("error"):
            status = 400 if result.get("error") == "bad_request" else 503
            raise HTTPException(status_code=status, detail=result)
        return JSONResponse(status_code=200, content=result)

    @app.get("/v1/skill")
    async def _v1_skill() -> JSONResponse:
        """Return the version-pinned tapps-brain agent skill body (TAP-2981)."""
        from tapps_brain.skill_content import load_tapps_brain_skill

        return JSONResponse(status_code=200, content=load_tapps_brain_skill())

    @app.post("/v1/profile/data:set", dependencies=[Depends(require_data_plane_auth)])
    async def _v1_profile_data_set(request: Request) -> JSONResponse:
        """Store profile-scoped learned KV (REST counterpart of ``brain_profile_set``)."""
        project_id = (request.headers.get("x-project-id") or "").strip()
        if not project_id:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "X-Project-Id header is required."},
            )
        body = await _parse_json_object_body(request)
        profile_name = (body.get("profile") or "").strip()
        data_key = (body.get("key") or "").strip()
        value_json = body.get("value_json")
        if not profile_name:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "profile is required."},
            )
        if not data_key:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "key is required."},
            )
        if not isinstance(value_json, dict):
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "value_json must be a JSON object."},
            )

        cm = _get_kg_cm_or_503()
        from tapps_brain.services import profile_data_service as _profile_svc

        result = await asyncio.to_thread(
            _profile_svc.profile_data_set,
            cm,
            project_id,
            profile_name=profile_name,
            data_key=data_key,
            value_json=value_json,
        )
        if isinstance(result, dict) and result.get("error"):
            raise HTTPException(status_code=400, detail=result)
        return JSONResponse(status_code=200, content=result)

    @app.post("/v1/profile/data:get", dependencies=[Depends(require_data_plane_auth)])
    async def _v1_profile_data_get(request: Request) -> JSONResponse:
        """Read profile-scoped learned KV (REST counterpart of ``brain_profile_get``)."""
        project_id = (request.headers.get("x-project-id") or "").strip()
        if not project_id:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "X-Project-Id header is required."},
            )
        body = await _parse_json_object_body(request)
        profile_name = (body.get("profile") or "").strip()
        data_key = (body.get("key") or "").strip()
        if not profile_name:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "profile is required."},
            )
        if not data_key:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "key is required."},
            )

        cm = _get_kg_cm_or_503()
        from tapps_brain.services import profile_data_service as _profile_svc

        result = await asyncio.to_thread(
            _profile_svc.profile_data_get,
            cm,
            project_id,
            profile_name=profile_name,
            data_key=data_key,
        )
        if isinstance(result, dict) and result.get("error"):
            raise HTTPException(status_code=400, detail=result)
        return JSONResponse(status_code=200, content=result)

    @app.post("/v1/kg/neighbors", dependencies=[Depends(require_data_plane_auth)])
    async def _v1_kg_neighbors(request: Request) -> JSONResponse:
        """Return the neighbourhood graph around one or more KG entities.

        REST counterpart of the ``brain_get_neighbors`` MCP tool.

        Request headers:
          - ``X-Project-Id`` (required): project identifier.
          - ``X-Agent-Id`` (optional): agent identifier.

        Request body (JSON):
          ``{ "entity_ids"?: [str], "entity_refs"?: [{entity_type, canonical_name}],
              "hops"?: int=1, "limit"?: int=20, "predicate_filter"?: str }``

        Response: ``{ "neighbors": [{...}], "entity_ids": [str] }``
        """
        project_id = (request.headers.get("x-project-id") or "").strip()
        if not project_id:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "X-Project-Id header is required."},
            )

        try:
            raw = await request.body()
        except Exception:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Failed to read request body."},
            )
        if not raw:
            raise HTTPException(
                status_code=400, detail={"error": "bad_request", "detail": "Empty request body."}
            )
        if len(raw) > 65_536:
            raise HTTPException(
                status_code=413,
                detail={"error": "payload_too_large", "detail": "Max 65536 bytes."},
            )
        try:
            body = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Request body must be valid JSON."},
            )
        if not isinstance(body, dict):
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Request body must be a JSON object."},
            )

        entity_ids = body.get("entity_ids") or []
        entity_refs = body.get("entity_refs") or []
        if entity_ids and not isinstance(entity_ids, list):
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "entity_ids must be a list."},
            )
        if entity_refs and not isinstance(entity_refs, list):
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "entity_refs must be a list."},
            )

        validated_entity_ids = (
            [_validate_uuid_field(e, f"entity_ids[{i}]") for i, e in enumerate(entity_ids) if e]
            if isinstance(entity_ids, list)
            else []
        )

        cm = _get_kg_cm_or_503()
        from tapps_brain.services import kg_service as _kg_svc

        collected = await asyncio.to_thread(
            _kg_svc.collect_neighbor_entity_ids,
            cm,
            project_id,
            _kg_brain_id(),
            entity_ids=validated_entity_ids,
            entity_refs=[r for r in entity_refs if isinstance(r, dict)] if entity_refs else [],
        )
        if isinstance(collected, dict) and collected.get("error"):
            raise HTTPException(status_code=400, detail=collected)
        validated_entity_ids = collected.get("entity_ids", [])

        result = await asyncio.to_thread(
            _kg_svc.get_neighbors,
            cm,
            project_id,
            _kg_brain_id(),
            entity_ids=validated_entity_ids,
            hops=max(1, min(int(body.get("hops", 1)), 2)),
            limit=max(1, min(int(body.get("limit", 20)), 200)),
            predicate_filter=str(body.get("predicate_filter") or "") or None,
        )
        return JSONResponse(status_code=200, content=result)

    @app.post("/v1/kg/explain", dependencies=[Depends(require_data_plane_auth)])
    async def _v1_kg_explain(request: Request) -> JSONResponse:
        """Find the shortest path between two KG entities.

        REST counterpart of the ``brain_explain_connection`` MCP tool.

        Request headers:
          - ``X-Project-Id`` (required): project identifier.
          - ``X-Agent-Id`` (optional): agent identifier.

        Request body (JSON):
          ``{ "subject_id": str, "object_id": str, "max_hops"?: int=3 }``

        Response: ``{ "found": bool, "hops": int|null, "path": [...],
        "subject_id": str, "object_id": str }``
        """
        project_id = (request.headers.get("x-project-id") or "").strip()
        if not project_id:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "X-Project-Id header is required."},
            )

        try:
            raw = await request.body()
        except Exception:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Failed to read request body."},
            )
        if not raw:
            raise HTTPException(
                status_code=400, detail={"error": "bad_request", "detail": "Empty request body."}
            )
        if len(raw) > 65_536:
            raise HTTPException(
                status_code=413,
                detail={"error": "payload_too_large", "detail": "Max 65536 bytes."},
            )
        try:
            body = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Request body must be valid JSON."},
            )
        if not isinstance(body, dict):
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Request body must be a JSON object."},
            )

        subject_id = (body.get("subject_id") or "").strip()
        object_id = (body.get("object_id") or "").strip()
        if not subject_id or not object_id:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "subject_id and object_id are required."},
            )

        # TAP-2140: validate UUID-bound fields before they reach psycopg.
        subject_id = _validate_uuid_field(subject_id, "subject_id")
        object_id = _validate_uuid_field(object_id, "object_id")

        cm = _get_kg_cm_or_503()
        from tapps_brain.services import kg_service as _kg_svc

        # TAP-1933: clamp against the configured ceiling, not the hard-coded 3.
        ceiling = _kg_svc.explain_max_hops_ceiling()
        result = await asyncio.to_thread(
            _kg_svc.explain_connection,
            cm,
            project_id,
            _kg_brain_id(),
            subject_id=subject_id,
            object_id=object_id,
            max_hops=max(1, min(int(body.get("max_hops", 3)), ceiling)),
        )
        return JSONResponse(status_code=200, content=result)

    @app.post("/v1/kg/feedback", dependencies=[Depends(require_data_plane_auth)])
    async def _v1_kg_feedback(request: Request) -> JSONResponse:
        """Record edge-level feedback to update KG edge confidence.

        REST counterpart of the ``brain_record_feedback`` MCP tool.

        Request headers:
          - ``X-Project-Id`` (required): project identifier.
          - ``X-Agent-Id`` (optional, default ``"unknown"``): agent identifier.

        Request body (JSON):
          ``{ "edge_id": str, "feedback_type": "edge_helpful"|"edge_misleading",
              "session_id"?: str }``

        Response: ``{ "recorded": true, "edge_id": str, "feedback_type": str }``
        """
        project_id = (request.headers.get("x-project-id") or "").strip()
        if not project_id:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "X-Project-Id header is required."},
            )
        agent_id = (request.headers.get("x-agent-id") or "").strip() or "unknown"
        store = _get_tenant_store_or_503(project_id, agent_id)

        try:
            raw = await request.body()
        except Exception:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Failed to read request body."},
            )
        if not raw:
            raise HTTPException(
                status_code=400, detail={"error": "bad_request", "detail": "Empty request body."}
            )
        if len(raw) > 65_536:
            raise HTTPException(
                status_code=413,
                detail={"error": "payload_too_large", "detail": "Max 65536 bytes."},
            )
        try:
            body = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Request body must be valid JSON."},
            )
        if not isinstance(body, dict):
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Request body must be a JSON object."},
            )

        edge_id = (body.get("edge_id") or "").strip()
        feedback_type = (body.get("feedback_type") or "").strip()
        if not edge_id or not feedback_type:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "bad_request",
                    "detail": "edge_id and feedback_type are required.",
                },
            )

        # TAP-2140: validate UUID-bound fields before they reach psycopg.
        edge_id = _validate_uuid_field(edge_id, "edge_id")

        # TAP-1930: utility_score is optional; when supplied, kg_service
        # validates the [-1, 1] range and surfaces 400 on overflow.
        us_raw = body.get("utility_score")
        utility_score: float | None
        if us_raw is None:
            utility_score = None
        else:
            try:
                utility_score = float(us_raw)
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "bad_request",
                        "detail": "utility_score must be a number.",
                    },
                )

        from tapps_brain.services import kg_service as _kg_svc

        result = await asyncio.to_thread(
            _kg_svc.record_kg_feedback,
            store,
            project_id,
            agent_id,
            edge_id=edge_id,
            feedback_type=feedback_type,
            session_id=str(body.get("session_id") or ""),
            utility_score=utility_score,
        )

        # Surface validation errors as 400 rather than 200
        if isinstance(result, dict) and result.get("error") == "bad_request":
            raise HTTPException(status_code=400, detail=result)

        return JSONResponse(status_code=200, content=result)

    @app.post("/v1/kg/resolve_entity", dependencies=[Depends(require_data_plane_auth)])
    async def _v1_kg_resolve_entity(request: Request) -> JSONResponse:
        """Resolve or create a KG entity by (entity_type, canonical_name).

        REST counterpart of the ``brain_resolve_entity`` MCP tool.  Turns a
        human-readable ``(entity_type, canonical_name)`` pair into the UUID
        required by ``/v1/experience`` edge specs.  Two calls with the same
        inputs always return the same UUID — safe to call concurrently.

        Request headers:
          - ``X-Project-Id`` (required): project identifier.
          - ``X-Agent-Id`` (optional): agent identifier (informational).

        Request body (JSON):
          ``{ "entity_type": str, "canonical_name": str }``

        Response: ``{ "entity_id": str, "entity_type": str,
        "canonical_name": str, "created": bool, "confidence": float,
        "reason": str }``

        Introduced in TAP-2725.
        """
        project_id = (request.headers.get("x-project-id") or "").strip()
        if not project_id:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "X-Project-Id header is required."},
            )

        try:
            raw = await request.body()
        except Exception:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Failed to read request body."},
            )
        if not raw:
            raise HTTPException(
                status_code=400, detail={"error": "bad_request", "detail": "Empty request body."}
            )
        if len(raw) > 65_536:
            raise HTTPException(
                status_code=413,
                detail={"error": "payload_too_large", "detail": "Max 65536 bytes."},
            )
        try:
            body = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Request body must be valid JSON."},
            )
        if not isinstance(body, dict):
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Request body must be a JSON object."},
            )

        entity_type = str(body.get("entity_type") or "").strip()
        canonical_name = str(body.get("canonical_name") or "").strip()
        if not entity_type:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "entity_type is required."},
            )
        if not canonical_name:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "canonical_name is required."},
            )

        cm = _get_kg_cm_or_503()
        from tapps_brain.services import kg_service as _kg_svc

        result = await asyncio.to_thread(
            _kg_svc.resolve_entity,
            cm,
            project_id,
            _kg_brain_id(),
            entity_type=entity_type,
            canonical_name=canonical_name,
        )
        return JSONResponse(status_code=200, content=result)

    @app.post("/v1/kg/resolve_entities", dependencies=[Depends(require_data_plane_auth)])
    async def _v1_kg_resolve_entities(request: Request) -> JSONResponse:
        """Batch-resolve KG entity refs to UUIDs (TAP-3249 / EPIC-078).

        REST counterpart for ``kg_service.resolve_entity_refs``.  Accepts an
        ``entity_refs`` array with ``entity_type``/``canonical_name`` or
        ``type``/``id`` shorthand and returns ``entity_ids`` (input order) plus
        per-ref ``results`` metadata.  Use this instead of piggybacking key
        resolution on ``POST /v1/kg/neighbors``.

        Request body::

            { "entity_refs": [
                {"entity_type": "module", "canonical_name": "retrieval"},
                {"type": "agent", "id": "ralph"}
            ]}

        Response::

            { "entity_ids": ["uuid-1", "uuid-2"],
              "results": [
                {"entity_id": "uuid-1", "entity_type": "module",
                 "canonical_name": "retrieval", "created": false},
                ...
              ]}
        """
        project_id = (request.headers.get("x-project-id") or "").strip()
        if not project_id:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "X-Project-Id header is required."},
            )

        try:
            raw = await request.body()
        except Exception:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Failed to read request body."},
            )
        if not raw:
            raise HTTPException(
                status_code=400, detail={"error": "bad_request", "detail": "Empty request body."}
            )
        if len(raw) > 65_536:
            raise HTTPException(
                status_code=413,
                detail={"error": "payload_too_large", "detail": "Max 65536 bytes."},
            )
        try:
            body = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Request body must be valid JSON."},
            )
        if not isinstance(body, dict):
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Request body must be a JSON object."},
            )

        entity_refs = body.get("entity_refs")
        if not isinstance(entity_refs, list):
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "bad_request",
                    "detail": "entity_refs must be a JSON array.",
                },
            )

        cm = _get_kg_cm_or_503()
        from tapps_brain.services import kg_service as _kg_svc

        result = await asyncio.to_thread(
            _kg_svc.resolve_entity_refs,
            cm,
            project_id,
            _kg_brain_id(),
            refs=entity_refs,
        )
        if isinstance(result, dict) and result.get("error") == "bad_request":
            raise HTTPException(status_code=400, detail=result)
        return JSONResponse(status_code=200, content=result)

    # -------- admin-plane routes (EPIC-069) --------

    def _open_registry() -> tuple[Any, Any]:
        if not cfg.dsn:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "db_unavailable",
                    "detail": "TAPPS_BRAIN_DATABASE_URL is not configured.",
                },
            )
        from tapps_brain.postgres_connection import PostgresConnectionManager
        from tapps_brain.project_registry import ProjectRegistry

        cm = PostgresConnectionManager(cfg.dsn)
        return ProjectRegistry(cm), cm

    @app.get("/admin/projects", dependencies=[Depends(require_admin_auth)])
    async def _admin_projects_list() -> JSONResponse:
        registry, cm = _open_registry()
        try:
            rows = registry.list_all()
        finally:
            cm.close()
        return JSONResponse(
            status_code=200,
            content={
                "projects": [
                    {
                        "project_id": r.project_id,
                        "profile_name": r.profile.name,
                        "approved": r.approved,
                        "source": r.source,
                        "notes": r.notes,
                    }
                    for r in rows
                ]
            },
        )

    @app.post("/admin/projects", dependencies=[Depends(require_admin_auth)])
    async def _admin_projects_register(request: Request) -> JSONResponse:
        try:
            raw = await request.body()
        except Exception:
            logger.exception("http_adapter.read_body_failed")
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Failed to read request body."},
            )
        if not raw:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Empty request body."},
            )
        if len(raw) > 65_536:
            raise HTTPException(
                status_code=413,
                detail={"error": "payload_too_large", "detail": "Max 65536 bytes."},
            )
        try:
            body = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.exception("http_adapter.invalid_json")
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Request body must be valid JSON."},
            )
        if not isinstance(body, dict):
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Request body must be a JSON object."},
            )

        project_id = (body.get("project_id") or "").strip()
        profile_json = body.get("profile")
        approved = bool(body.get("approved", True))
        source = body.get("source") or "admin"
        notes = body.get("notes") or ""

        if not project_id or not isinstance(profile_json, dict):
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "bad_request",
                    "detail": "project_id and profile (JSON object) are required.",
                },
            )
        try:
            from tapps_brain.profile import MemoryProfile
            from tapps_brain.project_resolver import validate_project_id

            validate_project_id(project_id)
            profile = MemoryProfile.model_validate(profile_json)
        except Exception:
            logger.exception("http_adapter.profile_validation_failed")
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Invalid profile or project_id."},
            )

        registry, cm = _open_registry()
        try:
            try:
                record = registry.register(
                    project_id,
                    profile,
                    source=source,
                    approved=approved,
                    notes=notes,
                )
            except ValueError:
                logger.exception("http_adapter.project_register_failed")
                raise HTTPException(
                    status_code=400,
                    detail={"error": "bad_request", "detail": "Project registration failed."},
                )
        finally:
            cm.close()
        return JSONResponse(
            status_code=201,
            content={
                "project_id": record.project_id,
                "profile_name": record.profile.name,
                "approved": record.approved,
                "source": record.source,
            },
        )

    @app.get("/admin/projects/{project_id}", dependencies=[Depends(require_admin_auth)])
    async def _admin_project_show(project_id: str) -> JSONResponse:
        registry, cm = _open_registry()
        try:
            record = registry.get(project_id)
        finally:
            cm.close()
        if record is None:
            return JSONResponse(
                status_code=404,
                content={"error": "not_found", "project_id": project_id},
            )
        return JSONResponse(
            status_code=200,
            content={
                "project_id": record.project_id,
                "profile": record.profile.model_dump(mode="json"),
                "approved": record.approved,
                "source": record.source,
                "notes": record.notes,
            },
        )

    @app.post("/admin/projects/{project_id}/approve", dependencies=[Depends(require_admin_auth)])
    async def _admin_project_approve(project_id: str) -> JSONResponse:
        registry, cm = _open_registry()
        try:
            updated = registry.approve(project_id)
        finally:
            cm.close()
        if not updated:
            return JSONResponse(
                status_code=404,
                content={"error": "not_found", "project_id": project_id},
            )
        return JSONResponse(
            status_code=200,
            content={"project_id": project_id, "approved": True},
        )

    @app.delete("/admin/projects/{project_id}", dependencies=[Depends(require_admin_auth)])
    async def _admin_project_delete(project_id: str) -> JSONResponse:
        registry, cm = _open_registry()
        try:
            deleted = registry.delete(project_id)
        finally:
            cm.close()
        if not deleted:
            return JSONResponse(
                status_code=404,
                content={"error": "not_found", "project_id": project_id},
            )
        return JSONResponse(
            status_code=200,
            content={"project_id": project_id, "deleted": True},
        )

    # ---- per-tenant token routes (STORY-070.8) ----

    @app.post(
        "/admin/projects/{project_id}/rotate-token", dependencies=[Depends(require_admin_auth)]
    )
    async def _admin_project_rotate_token(project_id: str) -> JSONResponse:
        """Issue/replace the per-tenant bearer token for *project_id*.

        Returns the **plaintext token once** — store it immediately.
        """
        registry, cm = _open_registry()
        try:
            try:
                plaintext = registry.rotate_token(project_id)
            except LookupError:
                return JSONResponse(
                    status_code=404,
                    content={"error": "not_found", "project_id": project_id},
                )
            except ImportError:
                logger.exception("http_adapter.rotate_token_missing_library")
                raise HTTPException(
                    status_code=503,
                    detail={
                        "error": "feature_unavailable",
                        "detail": "Token hashing library is not available; contact operator.",
                    },
                )
        finally:
            cm.close()
        return JSONResponse(
            status_code=201,
            content={
                "project_id": project_id,
                "token": plaintext,
                "warning": "Store this token — it will not be shown again.",
            },
        )

    @app.delete("/admin/projects/{project_id}/token", dependencies=[Depends(require_admin_auth)])
    async def _admin_project_revoke_token(project_id: str) -> JSONResponse:
        """Revoke (clear) the per-tenant token for *project_id*."""
        registry, cm = _open_registry()
        try:
            revoked = registry.revoke_token(project_id)
        finally:
            cm.close()
        if not revoked:
            return JSONResponse(
                status_code=404,
                content={"error": "not_found", "project_id": project_id},
            )
        return JSONResponse(
            status_code=200,
            content={"project_id": project_id, "token_revoked": True},
        )

    # Preserve legacy wire contract: when HTTPException.detail is a dict,
    # return it unwrapped (not nested under ``{"detail": ...}``).
    @app.exception_handler(HTTPException)
    async def _http_exc_handler(_request: Request, exc: HTTPException) -> JSONResponse:
        body = exc.detail if isinstance(exc.detail, dict) else {"detail": exc.detail}
        return JSONResponse(status_code=exc.status_code, content=body)

    # STORY-069.4: map ProjectNotRegisteredError → structured 403 so admin
    # routes that touch the registry report the same envelope as the
    # legacy handler.  Shape preserved for backward compat.
    @app.exception_handler(_ProjectNotRegisteredError)
    async def _pne_handler(_request: Request, exc: _ProjectNotRegisteredError) -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content={
                "error": "project_not_registered",
                "message": str(exc),
                "project_id": exc.project_id,
            },
        )

    # STORY-070.4: map taxonomy exceptions to structured responses.
    # BrainDegradedError and BrainRateLimitedError set Retry-After header.
    @app.exception_handler(_BrainDegradedError)
    async def _brain_degraded_handler(_request: Request, exc: _BrainDegradedError) -> JSONResponse:
        retry_after: int = exc.details.get("retry_after", 30)
        return JSONResponse(
            status_code=503,
            content=exc.http_body(retry_after=retry_after),
            headers={"Retry-After": str(retry_after)},
        )

    @app.exception_handler(_BrainRateLimitedError)
    async def _brain_rate_limited_handler(
        _request: Request, exc: _BrainRateLimitedError
    ) -> JSONResponse:
        retry_after = exc.details.get("retry_after", 60)
        return JSONResponse(
            status_code=429,
            content=exc.http_body(retry_after=retry_after),
            headers={"Retry-After": str(retry_after)},
        )

    @app.exception_handler(_TaxonomyError)
    async def _taxonomy_handler(_request: Request, exc: _TaxonomyError) -> JSONResponse:
        """Catch-all for all remaining TaxonomyError subclasses."""
        return JSONResponse(
            status_code=exc.http_status,
            content=exc.http_body(),
        )

    @app.exception_handler(ValidationError)
    async def _validation_exc_handler(_request: Request, exc: ValidationError) -> JSONResponse:
        """TAP-2865: client request-payload validation failures return a typed
        422 instead of being masked as a generic 500 by the catch-all below.

        A ``pydantic.ValidationError`` raised while deserialising a request
        body on these data-plane endpoints (e.g. ``record_event`` coercing an
        ``edges`` entry that is missing the required ``subject_entity_id`` /
        ``object_entity_id`` UUIDs) is a *client* error, not a server fault.
        Before this handler it propagated to the ``Exception`` catch-all
        (TAP-2727) and returned ``{"error":"internal_error"}`` with HTTP 500,
        hiding the real cause from the caller and forcing operators to read
        container logs.

        Mirrors the envelope of :func:`_validate_uuid_field` (TAP-2140) so all
        request-validation 422s share one shape: ``error`` / ``field`` /
        ``detail`` plus a full ``errors`` list.  ``include_input`` and
        ``include_url`` are disabled so the response never echoes the caller's
        payload or external pydantic doc URLs.
        """
        raw = exc.errors(include_url=False, include_input=False)
        errors = [
            {
                "field": ".".join(str(part) for part in err.get("loc", ())),
                "msg": err.get("msg", ""),
                "type": err.get("type", ""),
            }
            for err in raw
        ]
        logger.warning("http_adapter.request_validation_error", errors=errors)
        _record_http_error(_request.url.path, 422)
        return JSONResponse(
            status_code=422,
            content={
                "error": "validation_error",
                "field": errors[0]["field"] if errors else None,
                "detail": "Request payload failed validation.",
                "errors": errors,
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled_exc_handler(_request: Request, exc: Exception) -> JSONResponse:
        """Sanitized 500 for any unhandled service-layer error (TAP-2727).

        Without this, an unhandled error (e.g. a dropped psycopg connection
        surfacing as a raw exception) would leak Postgres implementation
        details into the response body.  Return a generic structured envelope
        instead and log the traceback server-side.  More specific handlers
        registered above (HTTPException, TaxonomyError, …) still take
        precedence via exception-type MRO.
        """
        logger.exception("http_adapter.unhandled_exception")
        _record_http_error(_request.url.path, 500)
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "detail": "Internal server error."},
        )

    # /mcp mount is installed by the lifespan handler above once the
    # FastMCP session manager is running.

    return app


# Module-level ASGI app for ``uvicorn tapps_brain.http_adapter:app``.
app: FastAPI = create_app()


# ---------------------------------------------------------------------------
# Legacy adapter wrapper (Typer CLI compatibility)
# ---------------------------------------------------------------------------


class HttpAdapter:
    """Thin wrapper around uvicorn so existing callers keep working.

    The FastAPI app is built once at import time; this class just owns
    a uvicorn server + background thread.
    """

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8080,
        dsn: str | None = None,
        auth_token: str | None = None,
        admin_token: str | None = None,
        store: MemoryStore | None = None,
    ) -> None:
        cfg = get_settings()
        if dsn is not None:
            cfg.dsn = dsn
        if auth_token is not None:
            cfg.auth_token = auth_token
        if admin_token is not None:
            cfg.admin_token = admin_token
        if store is not None:
            cfg.store = store
        self._host = host
        self._port = port
        self._thread: threading.Thread | None = None
        self._server: Any = None  # uvicorn.Server

    def start(self) -> None:
        if self._server is not None:
            return
        import uvicorn

        config = uvicorn.Config(
            app,
            host=self._host,
            port=self._port,
            log_level="warning",
            lifespan="on",
        )
        self._server = uvicorn.Server(config)

        def _run() -> None:
            self._server.run()

        self._thread = threading.Thread(target=_run, daemon=True, name="tapps-brain-http")
        self._thread.start()
        # Wait briefly for uvicorn to finish startup so callers that probe
        # ``address`` / issue immediate requests don't race the bind().
        deadline = time.time() + 5.0
        while time.time() < deadline and not getattr(self._server, "started", False):
            time.sleep(0.05)
        logger.info(
            "http_adapter.started", host=self._host, port=self._port, platform=platform.system()
        )

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._server = None
        self._thread = None
        logger.info("http_adapter.stopped")

    def __enter__(self) -> HttpAdapter:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()

    @property
    def address(self) -> tuple[str, int]:
        return (self._host, self._port)

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


# ---------------------------------------------------------------------------
# CLI entry point — ``tapps-brain-http``
# ---------------------------------------------------------------------------


def main() -> None:
    """Run uvicorn programmatically for ``tapps-brain-http``."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="tapps-brain-http",
        description="Run the tapps-brain HTTP+MCP adapter (FastAPI + uvicorn).",
    )
    parser.add_argument("--host", default=os.environ.get("TAPPS_BRAIN_HTTP_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("TAPPS_BRAIN_HTTP_PORT", "8080"))
    )
    parser.add_argument("--log-level", default=os.environ.get("TAPPS_BRAIN_LOG_LEVEL", "info"))
    args = parser.parse_args()

    logging.basicConfig(level=args.log_level.upper())

    # Security: warn when binding to all interfaces without auth configured.
    # Mirror _Settings._resolve_auth_token so _FILE variants (Docker Secrets)
    # are also recognised as "auth configured".
    # Note: tapps-brain-http has no --mcp-host; only --host is checked here.
    _auth_configured = bool(
        os.environ.get("TAPPS_BRAIN_AUTH_TOKEN")
        or os.environ.get("TAPPS_BRAIN_AUTH_TOKEN_FILE")
        or os.environ.get("TAPPS_BRAIN_HTTP_AUTH_TOKEN")
        or os.environ.get("TAPPS_BRAIN_HTTP_AUTH_TOKEN_FILE")
        or os.environ.get("TAPPS_BRAIN_PER_TENANT_AUTH") == "1"
    )
    if args.host == "0.0.0.0" and not _auth_configured:  # nosec B104 - intentional opt-in bind
        logger.warning(
            "http_adapter.bind_all_interfaces_unauthenticated",
            host=args.host,
            port=args.port,
            advice=(
                "Set TAPPS_BRAIN_AUTH_TOKEN (or TAPPS_BRAIN_AUTH_TOKEN_FILE) "
                "or TAPPS_BRAIN_PER_TENANT_AUTH=1 when binding to 0.0.0.0, "
                "or restrict to 127.0.0.1."
            ),
        )

    import uvicorn

    uvicorn.run(
        "tapps_brain.http_adapter:app",
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        lifespan="on",
    )


__all__ = [
    "_ORIGIN_EXEMPT_PATHS",
    "HttpAdapter",
    "McpTenantMiddleware",
    "OriginAllowlistMiddleware",
    "_extract_bearer",
    "_mcp_auth_error_body",
    "_metrics_request_authenticated",
    "_peek_mcp_tool_name",
    "_per_tenant_auth_enabled",
    "_verify_per_tenant_token",
    "app",
    "create_app",
    "get_settings",
    "hmac",
    "main",
    "require_admin_auth",
    "require_data_plane_auth",
]
