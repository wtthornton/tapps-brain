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
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import platform
import sys
import threading
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

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
from tapps_brain.otel_tracer import SPAN_KIND_SERVER, extract_trace_context, start_span
from tapps_brain.project_registry import ProjectNotRegisteredError as _ProjectNotRegisteredError

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants / module state
# ---------------------------------------------------------------------------

_SERVICE_NAME = "tapps-brain"
_SNAPSHOT_TTL_SECONDS: float = 15.0
_PROCESS_START_TIME: float = time.time()
_BEARER_PREFIX = "bearer "

# STORY-070.12: bounded per-(project_id, agent_id) request counters for
# Prometheus export.  agent_id cardinality is capped at 100 distinct values
# per project; overflow is bucketed as "other".
_MAX_AGENT_ID_CARDINALITY = 100
_LABELED_REQUEST_COUNTS: dict[tuple[str, str], int] = {}
_LABELED_REQUEST_COUNTS_LOCK = threading.Lock()
# TAP-599: per-project set of seen agent_ids for O(1) cardinality checks.
# Maintained in lock-step with _LABELED_REQUEST_COUNTS inside the lock.
_DISTINCT_AGENTS_PER_PROJECT: dict[str, set[str]] = {}

# STORY-073.2: process-wide ProfileResolver singleton.  Built once on first
# /mcp request; guarded by _PROFILE_RESOLVER_LOCK.
_PROFILE_RESOLVER: Any = None
_PROFILE_RESOLVER_LOCK = threading.Lock()


def _record_labeled_request(project_id: str, agent_id: str) -> None:
    """Increment the per-(project_id, agent_id) request counter (STORY-070.12).

    TAP-599: Uses a per-project set for O(1) membership/cardinality checks
    instead of an O(N) set-comprehension over the full _LABELED_REQUEST_COUNTS
    dict.  Both structures are updated inside the same lock so they stay in
    sync.
    """
    with _LABELED_REQUEST_COUNTS_LOCK:
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


def _get_profile_resolver() -> Any:
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


def _service_version() -> str:
    try:
        from importlib.metadata import version

        return version("tapps-brain")
    except Exception:
        return "unknown"


def _filter_snapshot_by_project(payload: dict[str, Any], project_id: str) -> dict[str, Any]:
    """STORY-069.7: filter diagnostics/feedback to a single project_id."""
    filtered = dict(payload)
    for key in ("diagnostics_history", "feedback_events"):
        rows = filtered.get(key) or []
        filtered[key] = [
            row for row in rows if isinstance(row, dict) and row.get("project_id") == project_id
        ]
    return filtered


# TAP-552: cache _probe_db results for 2 s so that Docker healthcheck (every 10 s)
# and Prometheus scrape (every 15 s) don't each open a new standalone Postgres
# connection.  Key = DSN string; value = (expires_at, result_tuple).
_PROBE_CACHE: dict[str, tuple[float, tuple[bool, int | None, str]]] = {}
_PROBE_CACHE_TTL: float = 2.0


def _probe_db(dsn: str | None) -> tuple[bool, int | None, str]:
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


def _get_hive_pool_stats(store: Any) -> dict[str, Any] | None:
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


def _collect_metrics(
    dsn: str | None,
    store: Any = None,
    *,
    redact_tenant_labels: bool = False,
) -> str:
    """Render Prometheus exposition text.

    ``redact_tenant_labels`` (TAP-547): when ``True`` the per-tenant labels
    ``project_id`` and ``agent_id`` are dropped from
    ``tapps_brain_mcp_requests_total`` and ``tapps_brain_tool_calls_total``
    and the counters are aggregated across those dimensions.  This is the
    shape served to anonymous (or unauthenticated) scrapers so reachable-
    but-unprivileged callers cannot enumerate tenant/agent activity.
    """
    lines: list[str] = []

    def gauge(name: str, value: float, help_text: str = "") -> None:
        if help_text:
            lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name} {value}")

    gauge(
        "tapps_brain_process_start_time_seconds",
        _PROCESS_START_TIME,
        "Unix timestamp when tapps-brain HTTP adapter was started.",
    )
    gauge(
        "tapps_brain_process_uptime_seconds",
        time.time() - _PROCESS_START_TIME,
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

    # STORY-070.12: per-(project_id, agent_id) request counters.
    # TAP-547: drop labels entirely when redacting — we still emit the
    # counter so scrapers have a total-volume signal, just not per-tenant.
    with _LABELED_REQUEST_COUNTS_LOCK:
        snapshot_counts = dict(_LABELED_REQUEST_COUNTS)
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

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Settings resolved from environment
# ---------------------------------------------------------------------------


class _Settings:
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


_settings = _Settings()


def get_settings() -> _Settings:
    return _settings


# ---------------------------------------------------------------------------
# Auth dependencies
# ---------------------------------------------------------------------------


def _extract_bearer(request: Request) -> str | None:
    header = request.headers.get("authorization") or ""
    if not header:
        return None
    if not header.lower().startswith(_BEARER_PREFIX):
        return ""
    return header[len(_BEARER_PREFIX) :].strip()


def _per_tenant_auth_enabled() -> bool:
    """Return ``True`` when ``TAPPS_BRAIN_PER_TENANT_AUTH=1`` is set."""
    return os.environ.get("TAPPS_BRAIN_PER_TENANT_AUTH", "") == "1"


def _verify_per_tenant_token(project_id: str, token: str, dsn: str) -> bool | None:
    """Check *token* against the project's stored argon2id hash.

    Returns:
        ``True``  — token verified against per-tenant hash.
        ``False`` — project has a token but *token* doesn't match.
        ``None``  — project has no per-tenant token; caller falls back to
                    the global ``TAPPS_BRAIN_AUTH_TOKEN`` check.
    """
    from tapps_brain.postgres_connection import PostgresConnectionManager
    from tapps_brain.project_registry import ProjectRegistry

    cm = PostgresConnectionManager(dsn)
    try:
        return ProjectRegistry(cm).verify_token(project_id, token)
    finally:
        cm.close()


def require_data_plane_auth(request: Request) -> None:
    """Dependency: data-plane bearer-token check.

    When ``TAPPS_BRAIN_PER_TENANT_AUTH=1``:
      * ``X-Project-Id`` header is **required** — 400 when missing or empty.
      * If no DSN is configured alongside the flag, fails closed with 500
        (misconfiguration) rather than falling through to the global token.
      * Verifies the bearer token against the project's argon2id hash in
        ``project_profiles.hashed_token``.
      * If the project has **no** per-tenant token configured, falls back to
        the global ``TAPPS_BRAIN_AUTH_TOKEN`` check so deployments that have
        not yet issued per-tenant tokens continue to work unchanged.
      * The global token is NOT accepted as a substitute when
        ``X-Project-Id`` is absent — that would defeat per-tenant isolation
        (TAP-626).

    When the flag is unset (default), behaves exactly as before: checks
    the global ``TAPPS_BRAIN_AUTH_TOKEN`` only.

    When the global token is also unset, requests pass through
    (not-for-production).
    """
    cfg = get_settings()
    tok = _extract_bearer(request)

    # ---- per-tenant path (STORY-070.8) ----
    if _per_tenant_auth_enabled():
        # TAP-626: flag on but no DSN is a server misconfiguration — fail closed
        # rather than silently falling through to the global-token check (which
        # would reproduce the supertoken bypass this fix is meant to close).
        if not cfg.dsn:
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "server_misconfiguration",
                    "detail": (
                        "TAPPS_BRAIN_PER_TENANT_AUTH is enabled but no database DSN is configured."
                    ),
                },
            )
        project_id = (request.headers.get("x-project-id") or "").strip()
        # TAP-626: reject instead of falling through to the global-token check.
        # Allowing the global token when X-Project-Id is absent makes it a
        # supertoken that bypasses per-tenant isolation entirely.
        if not project_id:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "bad_request",
                    "detail": ("X-Project-Id header is required when per-tenant auth is enabled."),
                },
            )
        # project_id is now guaranteed non-empty (rejected above if empty)
        if tok is None:
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "unauthorized",
                    "detail": "Authorization header required (Bearer token).",
                },
            )
        if tok == "":
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "unauthorized",
                    "detail": "Malformed Authorization header — expected 'Bearer <token>'.",
                },
            )
        result = _verify_per_tenant_token(project_id, tok, cfg.dsn)
        if result is True:
            return  # authenticated by per-tenant token
        if result is False:
            # Project has a token — wrong credential → 403
            raise HTTPException(
                status_code=403,
                detail={"error": "forbidden", "detail": "Invalid token."},
            )
        # result is None → project has no per-tenant token, fall through to global check

    # ---- global token fallback ----
    if not cfg.auth_token:
        return
    if tok is None:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "unauthorized",
                "detail": "Authorization header required (Bearer token).",
            },
        )
    if tok == "":
        raise HTTPException(
            status_code=401,
            detail={
                "error": "unauthorized",
                "detail": "Malformed Authorization header — expected 'Bearer <token>'.",
            },
        )
    # TAP-544: constant-time comparison to avoid byte-by-byte timing recovery.
    if not hmac.compare_digest(tok.encode("utf-8"), cfg.auth_token.encode("utf-8")):
        raise HTTPException(
            status_code=403,
            detail={"error": "forbidden", "detail": "Invalid token."},
        )


def _metrics_request_authenticated(request: Request, cfg: _Settings) -> bool:
    """TAP-547: gate for the Prometheus ``/metrics`` endpoint.

    Return value semantics:

    * ``True``  — caller presented a valid ``TAPPS_BRAIN_METRICS_TOKEN``
      bearer; serve the full per-(project_id, agent_id) label surface.
    * ``False`` — no metrics token is configured on the server.  The
      endpoint still responds 200 but with tenant labels stripped (see
      ``_collect_metrics(redact_tenant_labels=True)``) so reachable-but-
      unprivileged callers cannot enumerate tenants.

    Raises ``HTTPException`` with:

    * 401 when a token IS configured and the bearer header is missing or
      malformed.
    * 403 when a token IS configured and the bearer does not match.
    """
    token = getattr(cfg, "metrics_token", None)
    if not token:
        return False
    tok = _extract_bearer(request)
    if tok is None or tok == "":
        raise HTTPException(
            status_code=401,
            detail={
                "error": "unauthorized",
                "detail": "Bearer token required for /metrics.",
            },
        )
    # TAP-544-style constant-time comparison: the metrics token grants
    # cross-tenant label visibility, so we avoid byte-by-byte timing
    # recovery here too.
    if not hmac.compare_digest(tok.encode("utf-8"), token.encode("utf-8")):
        raise HTTPException(
            status_code=403,
            detail={"error": "forbidden", "detail": "Invalid metrics token."},
        )
    return True


def require_admin_auth(request: Request) -> None:
    """Dependency: ``TAPPS_BRAIN_ADMIN_TOKEN`` check for ``/admin/*``.

    When the admin token is unset, the route returns 503 — admin without a
    token would bypass the trust model (EPIC-069).
    """
    cfg = get_settings()
    if not cfg.admin_token:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "admin_disabled",
                "detail": "Admin routes require TAPPS_BRAIN_ADMIN_TOKEN to be set.",
            },
        )
    tok = _extract_bearer(request)
    if tok is None or tok == "":
        raise HTTPException(
            status_code=401,
            detail={"error": "unauthorized", "detail": "Bearer token required for admin routes."},
        )
    # TAP-544: constant-time comparison protects TAPPS_BRAIN_ADMIN_TOKEN from
    # statistical timing recovery — admin routes grant cross-tenant power.
    if not hmac.compare_digest(tok.encode("utf-8"), cfg.admin_token.encode("utf-8")):
        raise HTTPException(
            status_code=403,
            detail={"error": "forbidden", "detail": "Invalid admin token."},
        )


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class OtelSpanMiddleware(BaseHTTPMiddleware):
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


# Paths that are intentionally unauthenticated and Origin-agnostic (TAP-627).
# These are probe / scrape endpoints that must remain reachable from any origin
# (load-balancer health checks, Prometheus scrapers, etc.) and do not accept
# bearer tokens that a DNS-rebinding attacker could steal.
_ORIGIN_EXEMPT_PATHS: frozenset[str] = frozenset({"/", "/health", "/ready", "/metrics"})


class OriginAllowlistMiddleware(BaseHTTPMiddleware):
    """DNS-rebinding guard applied to every bearer-authenticated route (TAP-627).

    When ``TAPPS_BRAIN_ALLOWED_ORIGINS`` is set, any browser-originated request
    (``Origin`` header present) whose origin is not in the allowlist receives a
    ``403 Forbidden`` response before the handler is reached.

    Intentionally exempt (unauthenticated probe / scrape endpoints that are
    Origin-agnostic by design):

    * ``/`` — root liveness check
    * ``/health`` — liveness probe
    * ``/ready`` — readiness probe
    * ``/metrics`` — Prometheus scrape endpoint

    Previously only ``/mcp`` was guarded (STORY-070.3/4).  TAP-627 extends
    protection to all bearer-authenticated routes (``/v1/*``, ``/admin/*``,
    ``/mcp``, ``/info``, etc.) so that DNS-rebinding attacks against REST
    endpoints are also blocked.
    """

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        cfg = get_settings()
        if cfg.allowed_origins and request.url.path not in _ORIGIN_EXEMPT_PATHS:
            origin = request.headers.get("origin", "")
            if origin and origin not in cfg.allowed_origins:
                return JSONResponse(
                    status_code=403,
                    content={"error": "forbidden", "detail": f"Origin '{origin}' not allowed."},
                )
        return await call_next(request)  # type: ignore[no-any-return]


class McpTenantMiddleware(BaseHTTPMiddleware):
    """Enforce the MCP wire envelope for ``/mcp``:

    * ``X-Project-Id`` required (400 on miss).
    * ``X-Agent-Id`` optional (defaults to ``"unknown"``).
    * ``Authorization: Bearer <TAPPS_BRAIN_AUTH_TOKEN>``.
    * Sets contextvars consumed by :mod:`tapps_brain.mcp_server`.
    """

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        if not request.url.path.startswith("/mcp"):
            return await call_next(request)  # type: ignore[no-any-return]

        cfg = get_settings()

        # Auth
        if cfg.auth_token:
            tok = _extract_bearer(request)
            if tok is None or tok == "":
                return JSONResponse(
                    status_code=401,
                    content={"error": "unauthorized", "detail": "Bearer token required for /mcp."},
                )
            # TAP-544: constant-time comparison for the /mcp bearer-token check.
            if not hmac.compare_digest(tok.encode("utf-8"), cfg.auth_token.encode("utf-8")):
                return JSONResponse(
                    status_code=403,
                    content={"error": "forbidden", "detail": "Invalid token."},
                )

        # Tenant headers
        project_id = (request.headers.get("x-project-id") or "").strip()
        if not project_id:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "bad_request",
                    "detail": "X-Project-Id header is required for /mcp requests.",
                },
            )
        agent_id = (request.headers.get("x-agent-id") or "").strip() or "unknown"
        # STORY-070.7: ``X-Tapps-Agent`` is the canonical per-call identity
        # header; it takes precedence over the legacy ``X-Agent-Id`` so a
        # pooled MCP connection can multiplex many agents without reconnect.
        tapps_agent = (request.headers.get("x-tapps-agent") or "").strip()
        if tapps_agent:
            agent_id = tapps_agent
        scope = (request.headers.get("x-tapps-scope") or "").strip() or None
        group = (request.headers.get("x-tapps-group") or "").strip() or None

        # STORY-073.2: Per-request profile resolution.
        # Precedence: X-Brain-Profile header → agent_registry → server default.
        # Validate the header value against the profile registry before trusting it.
        from tapps_brain.mcp_server.profile_registry import UnknownProfileError

        header_profile = (request.headers.get("x-brain-profile") or "").strip() or None
        resolver = _get_profile_resolver()
        if header_profile is not None:
            try:
                resolver.validate_profile_name(header_profile)
            except UnknownProfileError as exc:
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": "bad_request",
                        "detail": f"Unknown MCP profile {header_profile!r}.",
                        "available": exc.available,
                    },
                )
        resolved_profile = resolver.resolve(
            project_id=project_id,
            agent_id=agent_id,
            header_profile=header_profile,
        )

        # Bridge into tapps_brain.mcp_server contextvars so the service layer
        # sees the per-request tenant regardless of whether the JSON-RPC
        # envelope also carries ``_meta.project_id``.
        from tapps_brain import mcp_server as _mcp_mod

        token_pid = _mcp_mod.REQUEST_PROJECT_ID.set(project_id)
        token_agent = _mcp_mod.REQUEST_AGENT_ID.set(agent_id)
        token_scope = _mcp_mod.REQUEST_SCOPE.set(scope)
        token_group = _mcp_mod.REQUEST_GROUP.set(group)
        token_profile = _mcp_mod.REQUEST_PROFILE.set(resolved_profile)
        # Also mirror into request.state for handlers / observability.
        request.state.project_id = project_id
        request.state.agent_id = agent_id
        request.state.scope = scope
        request.state.group = group
        request.state.brain_profile = resolved_profile
        # STORY-070.12: track per-(project_id, agent_id) request counts.
        _record_labeled_request(project_id, agent_id)
        try:
            return await call_next(request)  # type: ignore[no-any-return]
        finally:
            _mcp_mod.REQUEST_PROJECT_ID.reset(token_pid)
            _mcp_mod.REQUEST_AGENT_ID.reset(token_agent)
            _mcp_mod.REQUEST_SCOPE.reset(token_scope)
            _mcp_mod.REQUEST_GROUP.reset(token_group)
            _mcp_mod.REQUEST_PROFILE.reset(token_profile)


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

    # Warn at startup if origin allow-list is empty (allow-all).
    if not cfg.allowed_origins:
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

    # Register middlewares.  Order matters — starlette runs them
    # outside-in for the request path, inside-out for the response.
    app.add_middleware(OtelSpanMiddleware)
    app.add_middleware(OriginAllowlistMiddleware)
    app.add_middleware(McpTenantMiddleware)

    # -------- data-plane routes --------

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
                redact_tenant_labels=not authenticated,
            ),
            status_code=200,
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

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

        with cfg.snapshot_lock:
            now = time.time()
            cache_hit = (
                cfg.snapshot_cache is not None
                and (now - cfg.snapshot_cache_at) < _SNAPSHOT_TTL_SECONDS
            )
            if cache_hit:
                snapshot = cfg.snapshot_cache
            else:
                from tapps_brain.visual_snapshot import build_visual_snapshot

                snapshot = build_visual_snapshot(cfg.store, privacy="standard")
                cfg.snapshot_cache = snapshot
                cfg.snapshot_cache_at = now

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
        store = _get_store_or_503()

        project_id = (request.headers.get("x-project-id") or "").strip()
        if not project_id:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "X-Project-Id header is required."},
            )
        agent_id = (request.headers.get("x-agent-id") or "").strip() or "unknown"

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
                _cached = istore.check(project_id, ikey)
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

            result = _ms.memory_save(
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
                istore.save(project_id, ikey, status_code, result)

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
        store = _get_store_or_503()

        project_id = (request.headers.get("x-project-id") or "").strip()
        if not project_id:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "X-Project-Id header is required."},
            )
        agent_id = (request.headers.get("x-agent-id") or "").strip() or "unknown"

        # TAP-629: acquire per-key guard before cache check (see _v1_remember).
        ikey, istore = _get_ikey_and_istore(request)
        guard: asyncio.Lock | None = None
        if ikey and istore is not None:
            guard = _ensure_idem_guard(project_id, ikey)
            await guard.acquire()

        try:
            # Cache check inside the guard.
            if ikey and istore is not None:
                _cached = istore.check(project_id, ikey)
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

            result = _ms.memory_reinforce(
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
                istore.save(project_id, ikey, status_code, result)

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
        store = _get_store_or_503()

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

        result = _ms.memory_save_many(store, project_id, agent_id, entries=entries)
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
        store = _get_store_or_503()

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

        result = _ms.memory_recall_many(store, project_id, agent_id, queries=queries)
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
        store = _get_store_or_503()

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

        result = _ms.memory_reinforce_many(store, project_id, agent_id, entries=entries)
        status_code = 400 if "error" in result else 200
        return JSONResponse(status_code=status_code, content=result)

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
    if args.host == "0.0.0.0" and not _auth_configured:
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
    "HttpAdapter",
    "app",
    "create_app",
    "get_settings",
    "main",
    "require_admin_auth",
    "require_data_plane_auth",
]
