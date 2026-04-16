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
* Performs ``Origin``-header allow-listing for ``/mcp`` to prevent DNS
  rebinding (MCP spec requirement).
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

import json
import logging
import os
import platform
import sys
import threading
import time
from contextlib import asynccontextmanager
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


def _record_labeled_request(project_id: str, agent_id: str) -> None:
    """Increment the per-(project_id, agent_id) request counter (STORY-070.12)."""
    # Clamp agent_id to bounded cardinality per project.
    with _LABELED_REQUEST_COUNTS_LOCK:
        distinct_agents = {k[1] for k in _LABELED_REQUEST_COUNTS if k[0] == project_id}
        if agent_id not in distinct_agents and len(distinct_agents) >= _MAX_AGENT_ID_CARDINALITY:
            agent_id = "other"
        key = (project_id, agent_id)
        _LABELED_REQUEST_COUNTS[key] = _LABELED_REQUEST_COUNTS.get(key, 0) + 1


# ---------------------------------------------------------------------------
# OpenAPI spec — preserved verbatim from the legacy handler so /openapi.json
# continues to return the same document that existing clients parse.
# ---------------------------------------------------------------------------

_OPENAPI_SPEC: dict[str, Any] = {
    "openapi": "3.1.0",
    "info": {
        "title": "tapps-brain runtime API",
        "version": "3.0.0",
        "description": (
            "Minimal HTTP adapter exposing liveness, readiness, metrics, and "
            "extended runtime info for tapps-brain. "
            "Memory operations are **not** available over HTTP — use the "
            "AgentBrain Python API or the MCP server instead."
        ),
    },
    "paths": {
        "/health": {
            "get": {
                "summary": "Liveness probe",
                "operationId": "getLiveness",
                "security": [],
                "responses": {"200": {"description": "Process is alive."}},
            }
        },
        "/ready": {
            "get": {
                "summary": "Readiness probe",
                "operationId": "getReadiness",
                "security": [],
                "responses": {
                    "200": {"description": "Ready."},
                    "503": {"description": "Degraded."},
                },
            }
        },
        "/metrics": {
            "get": {
                "summary": "Prometheus metrics",
                "operationId": "getMetrics",
                "security": [],
                "responses": {"200": {"description": "text/plain"}},
            }
        },
        "/info": {
            "get": {
                "summary": "Extended runtime info",
                "operationId": "getInfo",
                "security": [{"bearerAuth": []}],
                "responses": {
                    "200": {"description": "Runtime info."},
                    "401": {"description": "Missing/malformed Authorization."},
                    "403": {"description": "Invalid token."},
                },
            }
        },
        "/snapshot": {
            "get": {
                "summary": "Live system snapshot",
                "operationId": "getSnapshot",
                "security": [{"bearerAuth": []}],
                "responses": {
                    "200": {"description": "VisualSnapshot JSON."},
                    "401": {"description": "Missing/malformed Authorization."},
                    "403": {"description": "Invalid token."},
                    "503": {"description": "No store configured."},
                },
            }
        },
        "/openapi.json": {
            "get": {
                "summary": "OpenAPI spec",
                "operationId": "getOpenApiSpec",
                "security": [],
                "responses": {"200": {"description": "OpenAPI."}},
            }
        },
    },
    "components": {
        "securitySchemes": {
            "bearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "description": (
                    "Set TAPPS_BRAIN_HTTP_AUTH_TOKEN (or TAPPS_BRAIN_AUTH_TOKEN) "
                    "to enable. When not configured, protected routes are open "
                    "(not-for-production)."
                ),
            }
        }
    },
}


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


def _probe_db(dsn: str | None) -> tuple[bool, int | None, str]:
    if not dsn:
        return False, None, "no DSN configured (set TAPPS_BRAIN_DATABASE_URL)"
    try:
        from tapps_brain.postgres_migrations import get_hive_schema_status

        status_ = get_hive_schema_status(dsn)
        version = status_.current_version if status_.current_version else None
        pending = len(status_.pending_migrations)
        if pending > 0:
            return True, version, f"ready (migration_version={version}, pending={pending})"
        return True, version, f"ready (migration_version={version})"
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
        return False, None, f"db_error: {err_str}"


def _get_hive_pool_stats(store: Any) -> dict[str, Any] | None:
    """Return pool stats dict from a store's hive connection manager, or None."""
    if store is None:
        return None
    try:
        hive = getattr(store, "_hive_store", None)
        cm = getattr(hive, "_cm", None)
        if cm is not None and hasattr(cm, "get_pool_stats"):
            return cm.get_pool_stats()
    except Exception:
        pass
    return None


def _collect_metrics(dsn: str | None, store: Any = None) -> str:
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
    with _LABELED_REQUEST_COUNTS_LOCK:
        snapshot_counts = dict(_LABELED_REQUEST_COUNTS)
    if snapshot_counts:
        lines.append(
            "# HELP tapps_brain_mcp_requests_total "
            "Total MCP requests, labelled by project_id and agent_id."
        )
        lines.append("# TYPE tapps_brain_mcp_requests_total counter")
        for (pid, aid), count in sorted(snapshot_counts.items()):
            safe_pid = pid.replace('"', '\\"')
            safe_aid = aid.replace('"', '\\"')
            lines.append(
                f'tapps_brain_mcp_requests_total{{project_id="{safe_pid}",'
                f'agent_id="{safe_aid}"}} {count}'
            )

    # STORY-070.12: per-(project_id, agent_id, tool, status) tool call counters.
    try:
        from tapps_brain.otel_tracer import get_tool_call_counts_snapshot

        tool_counts = get_tool_call_counts_snapshot()
        if tool_counts:
            lines.append(
                "# HELP tapps_brain_tool_calls_total "
                "Total MCP tool invocations labelled by project_id, agent_id, tool, and status."
            )
            lines.append("# TYPE tapps_brain_tool_calls_total counter")
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
    except Exception:  # pragma: no cover — otel_tracer import error must not crash /metrics
        pass

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
        self.allowed_origins = self._resolve_allowed_origins()
        self.version = _service_version()
        # Optional store injected by the CLI entry point / tests.
        self.store: MemoryStore | None = None
        # Snapshot cache
        self.snapshot_lock = threading.Lock()
        self.snapshot_cache: Any = None
        self.snapshot_cache_at: float = 0.0

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
      * Extracts the bearer token and ``X-Project-Id`` header.
      * Verifies the token against the project's argon2id hash in
        ``project_profiles.hashed_token``.
      * If the project has **no** per-tenant token, falls back to the
        global ``TAPPS_BRAIN_AUTH_TOKEN`` check so deployments without
        per-tenant tokens continue to work unchanged.

    When the flag is unset (default), behaves exactly as before: checks
    the global ``TAPPS_BRAIN_AUTH_TOKEN`` only.

    When the global token is also unset, requests pass through
    (not-for-production).
    """
    cfg = get_settings()
    tok = _extract_bearer(request)

    # ---- per-tenant path (STORY-070.8) ----
    if _per_tenant_auth_enabled() and cfg.dsn:
        project_id = (request.headers.get("x-project-id") or "").strip()
        if project_id:
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
            # result is None → project has no per-tenant token, fall through

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
    if tok != cfg.auth_token:
        raise HTTPException(
            status_code=403,
            detail={"error": "forbidden", "detail": "Invalid token."},
        )


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
    if tok != cfg.admin_token:
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


class OriginAllowlistMiddleware(BaseHTTPMiddleware):
    """MCP DNS-rebinding guard for ``/mcp`` (STORY-070.3/4)."""

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        if request.url.path.startswith("/mcp"):
            cfg = get_settings()
            if cfg.allowed_origins:
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
            if tok != cfg.auth_token:
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

        # Bridge into tapps_brain.mcp_server contextvars so the service layer
        # sees the per-request tenant regardless of whether the JSON-RPC
        # envelope also carries ``_meta.project_id``.
        from tapps_brain import mcp_server as _mcp_mod

        token_pid = _mcp_mod.REQUEST_PROJECT_ID.set(project_id)
        token_agent = _mcp_mod.REQUEST_AGENT_ID.set(agent_id)
        token_scope = _mcp_mod.REQUEST_SCOPE.set(scope)
        token_group = _mcp_mod.REQUEST_GROUP.set(group)
        # Also mirror into request.state for handlers / observability.
        request.state.project_id = project_id
        request.state.agent_id = agent_id
        request.state.scope = scope
        request.state.group = group
        # STORY-070.12: track per-(project_id, agent_id) request counts.
        _record_labeled_request(project_id, agent_id)
        try:
            return await call_next(request)  # type: ignore[no-any-return]
        finally:
            _mcp_mod.REQUEST_PROJECT_ID.reset(token_pid)
            _mcp_mod.REQUEST_AGENT_ID.reset(token_agent)
            _mcp_mod.REQUEST_SCOPE.reset(token_scope)
            _mcp_mod.REQUEST_GROUP.reset(token_group)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def _build_mcp_server() -> Any:
    """Import and build a default FastMCP instance for the ASGI mount."""
    from tapps_brain.mcp_server import create_server

    project_dir = Path(os.environ.get("TAPPS_BRAIN_SERVE_ROOT", "/var/lib/tapps-brain"))
    try:
        project_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Fall back to cwd in non-container environments / tests.
        project_dir = Path.cwd()
    agent_id = os.environ.get("TAPPS_BRAIN_AGENT_ID", "http-adapter") or "http-adapter"
    enable_operator = os.environ.get("TAPPS_BRAIN_OPERATOR_TOOLS", "") == "1"
    return create_server(
        project_dir,
        enable_hive=True,
        agent_id=agent_id,
        enable_operator_tools=enable_operator,
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

    # Defer MCP server build so stdio-only environments can import this
    # module without paying for it.
    mcp_holder: dict[str, Any] = {"mcp": mcp_server}

    def _get_mcp_asgi_sub(mcp: Any) -> Any:
        """Return the Streamable HTTP ASGI sub-app from a FastMCP instance."""
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
            try:
                sm = getattr(mcp, "session_manager", None)
            except RuntimeError:
                # FastMCP raises RuntimeError if session_manager is accessed
                # before streamable_http_app() is called (lazy init guard).
                sm = None
            if sm is not None and hasattr(sm, "run"):
                try:
                    session_cm = sm.run()
                    await session_cm.__aenter__()
                except Exception as exc:
                    logger.error("http_adapter.session_manager_start_failed", error=str(exc))
                    session_cm = None

            # Mount the MCP Streamable HTTP ASGI sub-app if not already mounted
            # eagerly (STORY-070.4).  When mcp_server was provided at create_app
            # call time the sub-app is mounted below, before lifespan runs, so
            # that httpx.ASGITransport (no-lifespan) test clients see the route.
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
        try:
            yield
        finally:
            if session_cm is not None:
                try:
                    await session_cm.__aexit__(None, None, None)
                except Exception:
                    logger.debug("http_adapter.session_manager_stop_failed", exc_info=True)

    app = FastAPI(
        title="tapps-brain runtime API",
        version=cfg.version,
        docs_url=None,  # we serve our hand-crafted /openapi.json instead
        redoc_url=None,
        openapi_url=None,
        lifespan=_lifespan,
    )

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
    async def _metrics() -> PlainTextResponse:
        return PlainTextResponse(
            content=_collect_metrics(cfg.dsn, store=cfg.store),
            status_code=200,
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    @app.get("/info", dependencies=[Depends(require_data_plane_auth)])
    async def _info() -> JSONResponse:
        body = {
            "service": _SERVICE_NAME,
            "version": cfg.version,
            "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "platform": platform.system(),
            "uptime_seconds": round(time.time() - _PROCESS_START_TIME, 3),
            "auth_enabled": cfg.auth_token is not None,
            "dsn_configured": cfg.dsn is not None,
        }
        return JSONResponse(status_code=200, content=body)

    @app.get("/openapi.json", include_in_schema=False)
    async def _openapi() -> JSONResponse:
        return JSONResponse(status_code=200, content=_OPENAPI_SPEC)

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

    def _idempotency_check(request: Request, project_id: str) -> tuple[str | None, Any | None]:
        """Return (ikey, cached_response_json) when idempotency is enabled and a hit exists.

        Returns (ikey, None) when the key is present but not yet cached.
        Returns (None, None) when idempotency is disabled or no key header.
        """
        from tapps_brain.idempotency import IdempotencyStore, is_idempotency_enabled

        if not is_idempotency_enabled():
            return None, None
        ikey = (request.headers.get("x-idempotency-key") or "").strip() or None
        if not ikey or not cfg.dsn:
            return ikey, None
        istore = IdempotencyStore(cfg.dsn)
        try:
            cached = istore.check(project_id, ikey)
        finally:
            istore.close()
        if cached is None:
            return ikey, None
        _status, _body = cached
        return ikey, JSONResponse(
            status_code=_status,
            content=_body,
            headers={"Idempotency-Replayed": "true"},
        )

    def _idempotency_save(project_id: str, ikey: str, status: int, body: dict[str, Any]) -> None:
        """Persist idempotency key → response when enabled and a DSN is available."""
        from tapps_brain.idempotency import IdempotencyStore, is_idempotency_enabled

        if not is_idempotency_enabled() or not cfg.dsn:
            return
        istore = IdempotencyStore(cfg.dsn)
        try:
            istore.save(project_id, ikey, status, body)
        finally:
            istore.close()

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

        # Idempotency check.
        ikey, replay = _idempotency_check(request, project_id)
        if replay is not None:
            return replay  # type: ignore[no-any-return]

        try:
            raw = await request.body()
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail={"error": "bad_request", "detail": f"Read error: {exc}"}
            )
        if not raw:
            raise HTTPException(
                status_code=400, detail={"error": "bad_request", "detail": "Empty request body."}
            )
        if len(raw) > 65_536:
            raise HTTPException(
                status_code=413, detail={"error": "payload_too_large", "detail": "Max 65536 bytes."}
            )
        try:
            body = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise HTTPException(
                status_code=400, detail={"error": "bad_request", "detail": f"Invalid JSON: {exc}"}
            )
        if not isinstance(body, dict):
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Request body must be a JSON object."},
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

        if ikey:
            _idempotency_save(project_id, ikey, status_code, result)

        return JSONResponse(status_code=status_code, content=result)

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

        # Idempotency check.
        ikey, replay = _idempotency_check(request, project_id)
        if replay is not None:
            return replay  # type: ignore[no-any-return]

        try:
            raw = await request.body()
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail={"error": "bad_request", "detail": f"Read error: {exc}"}
            )
        if not raw:
            raise HTTPException(
                status_code=400, detail={"error": "bad_request", "detail": "Empty request body."}
            )
        if len(raw) > 65_536:
            raise HTTPException(
                status_code=413, detail={"error": "payload_too_large", "detail": "Max 65536 bytes."}
            )
        try:
            body = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise HTTPException(
                status_code=400, detail={"error": "bad_request", "detail": f"Invalid JSON: {exc}"}
            )
        if not isinstance(body, dict):
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "Request body must be a JSON object."},
            )

        mem_key = (body.get("key") or "").strip()
        if not mem_key:
            raise HTTPException(
                status_code=400, detail={"error": "bad_request", "detail": "key is required."}
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

        if ikey:
            _idempotency_save(project_id, ikey, status_code, result)

        return JSONResponse(status_code=status_code, content=result)

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
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail={"error": "bad_request", "detail": f"Read error: {exc}"}
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
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise HTTPException(
                status_code=400, detail={"error": "bad_request", "detail": f"Invalid JSON: {exc}"}
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
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail={"error": "bad_request", "detail": f"Read error: {exc}"}
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
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise HTTPException(
                status_code=400, detail={"error": "bad_request", "detail": f"Invalid JSON: {exc}"}
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
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail={"error": "bad_request", "detail": f"Read error: {exc}"}
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
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise HTTPException(
                status_code=400, detail={"error": "bad_request", "detail": f"Invalid JSON: {exc}"}
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
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": f"Read error: {exc}"},
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
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": f"Invalid JSON: {exc}"},
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
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": str(exc)},
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
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail={"error": "bad_request", "detail": str(exc)},
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
            except ImportError as exc:
                raise HTTPException(
                    status_code=503,
                    detail={"error": "feature_unavailable", "detail": str(exc)},
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
    parser.add_argument("--host", default=os.environ.get("TAPPS_BRAIN_HTTP_HOST", "0.0.0.0"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("TAPPS_BRAIN_HTTP_PORT", "8080"))
    )
    parser.add_argument("--log-level", default=os.environ.get("TAPPS_BRAIN_LOG_LEVEL", "info"))
    args = parser.parse_args()

    logging.basicConfig(level=args.log_level.upper())

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
