"""HTTP adapter for tapps-brain runtime API (STORY-060.3 / STORY-060.4 / STORY-065.1).

Exposes probe/metrics endpoints using **only** the Python standard library —
no external HTTP framework is required.

Routes
------
``GET /``             — Alias for ``/health`` (convenience).
``GET /health``       — Liveness: always ``200 OK`` while the process is alive.
                        Does **not** require a database connection. (public)
``GET /ready``        — Readiness: DB ping + highest applied migration version.
                        Returns ``200`` when ready, ``503`` when degraded. (public)
``GET /metrics``      — Prometheus text-format exposition of basic runtime counters.
                        (public)
``GET /info``         — Extended runtime info: version, Python, uptime, config flags.
                        **Auth-protected** when ``TAPPS_BRAIN_HTTP_AUTH_TOKEN`` is set.
``GET /snapshot``     — Live system snapshot (VisualSnapshot JSON). 15s TTL cache.
                        **Auth-protected** when ``TAPPS_BRAIN_HTTP_AUTH_TOKEN`` is set.
                        Returns ``503`` when no store is injected at construction time.
                        CORS header ``Access-Control-Allow-Origin: *`` always present.
``GET /openapi.json`` — OpenAPI 3.1 spec for this adapter (public).

Authentication
--------------
Set ``TAPPS_BRAIN_HTTP_AUTH_TOKEN`` (or pass ``auth_token`` to the constructor)
to require ``Authorization: Bearer <token>`` on protected routes.  Probe routes
(``/``, ``/health``, ``/ready``, ``/metrics``, ``/openapi.json``) are **always
public** so orchestrators can reach them without credentials.

If the token is not configured, all routes are open (not-for-production — see
the ADR and README for the auth requirements statement).

Usage::

    from tapps_brain.http_adapter import HttpAdapter

    adapter = HttpAdapter(host="0.0.0.0", port=8080, dsn="postgres://...")
    adapter.start()   # non-blocking background thread
    ...
    adapter.stop()

Or as a context manager::

    with HttpAdapter(port=8080, dsn="postgres://...", auth_token="secret") as adapter:
        ...  # adapter is running

Pass a live ``MemoryStore`` to expose the ``/snapshot`` endpoint::

    with HttpAdapter(port=8080, dsn="...", store=my_store) as adapter:
        ...

The ``dsn`` and ``auth_token`` parameters are never included in response bodies
or log output (ADR-007 / EPIC-063 secret hygiene).

EPIC-060 STORY-060.3 / STORY-060.4
EPIC-065 STORY-065.1
"""

from __future__ import annotations

import http.server
import json
import os
import platform
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from tapps_brain.store import MemoryStore

from tapps_brain.otel_tracer import SPAN_KIND_SERVER, extract_trace_context, start_span
from tapps_brain.project_registry import ProjectNotRegisteredError as _ProjectNotRegisteredError

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# OpenAPI spec (STORY-060.4) — single source of truth for the route list.
# Kept in-module so it always matches the live code.
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
                "description": (
                    "Always returns 200 OK while the process is alive. "
                    "Does not require a database connection."
                ),
                "operationId": "getLiveness",
                "security": [],
                "responses": {
                    "200": {
                        "description": "Process is alive.",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "status": {"type": "string", "example": "ok"},
                                        "service": {"type": "string"},
                                        "version": {"type": "string"},
                                    },
                                }
                            }
                        },
                    }
                },
            }
        },
        "/ready": {
            "get": {
                "summary": "Readiness probe",
                "description": (
                    "Returns 200 when the configured Postgres database is reachable "
                    "and the latest migration has been applied. Returns 503 otherwise."
                ),
                "operationId": "getReadiness",
                "security": [],
                "responses": {
                    "200": {
                        "description": "Service is ready.",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "status": {
                                            "type": "string",
                                            "enum": ["ready", "degraded"],
                                        },
                                        "migration_version": {"type": ["integer", "null"]},
                                        "detail": {"type": "string"},
                                    },
                                }
                            }
                        },
                    },
                    "503": {"description": "Service is degraded (DB unreachable)."},
                },
            }
        },
        "/metrics": {
            "get": {
                "summary": "Prometheus metrics",
                "description": (
                    "Exposes basic runtime counters in Prometheus text format. "
                    "High-cardinality labels (query text, memory keys, agent IDs) "
                    "are never included."
                ),
                "operationId": "getMetrics",
                "security": [],
                "responses": {
                    "200": {
                        "description": "Prometheus text exposition.",
                        "content": {"text/plain": {}},
                    }
                },
            }
        },
        "/info": {
            "get": {
                "summary": "Extended runtime info",
                "description": (
                    "Returns service version, Python runtime, uptime, and config flags. "
                    "Auth-protected when TAPPS_BRAIN_HTTP_AUTH_TOKEN is configured."
                ),
                "operationId": "getInfo",
                "security": [{"bearerAuth": []}],
                "responses": {
                    "200": {
                        "description": "Runtime info.",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "service": {"type": "string"},
                                        "version": {"type": "string"},
                                        "python": {"type": "string"},
                                        "platform": {"type": "string"},
                                        "uptime_seconds": {"type": "number"},
                                        "auth_enabled": {"type": "boolean"},
                                        "dsn_configured": {"type": "boolean"},
                                    },
                                }
                            }
                        },
                    },
                    "401": {"description": "Missing or malformed Authorization header."},
                    "403": {"description": "Invalid token."},
                },
            }
        },
        "/snapshot": {
            "get": {
                "summary": "Live system snapshot",
                "description": (
                    "Returns the current VisualSnapshot as JSON, built from the injected "
                    "MemoryStore. Responses are cached for 15 seconds to limit store reads. "
                    "Returns 503 when the adapter was constructed without a store. "
                    "Auth-protected when TAPPS_BRAIN_HTTP_AUTH_TOKEN is configured. "
                    "Always includes Access-Control-Allow-Origin: * for dashboard access."
                ),
                "operationId": "getSnapshot",
                "security": [{"bearerAuth": []}],
                "responses": {
                    "200": {
                        "description": "Live VisualSnapshot JSON.",
                        "content": {"application/json": {}},
                    },
                    "401": {"description": "Missing or malformed Authorization header."},
                    "403": {"description": "Invalid token."},
                    "503": {
                        "description": "No store configured — adapter started without a store.",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"error": {"type": "string"}},
                                }
                            }
                        },
                    },
                },
            }
        },
        "/openapi.json": {
            "get": {
                "summary": "OpenAPI spec",
                "description": "Returns this OpenAPI 3.1 specification as JSON.",
                "operationId": "getOpenApiSpec",
                "security": [],
                "responses": {
                    "200": {
                        "description": "OpenAPI spec.",
                        "content": {"application/json": {}},
                    }
                },
            }
        },
    },
    "components": {
        "securitySchemes": {
            "bearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "description": (
                    "Set TAPPS_BRAIN_HTTP_AUTH_TOKEN to enable. "
                    "When not configured, protected routes are open (not-for-production)."
                ),
            }
        }
    },
}

# ---------------------------------------------------------------------------
# Version / service metadata helpers
# ---------------------------------------------------------------------------

_SERVICE_NAME = "tapps-brain"

# Number of parts in a valid "Bearer <token>" Authorization header.
_BEARER_PARTS = 2

# TTL for the /snapshot cache (seconds).
_SNAPSHOT_TTL_SECONDS: float = 15.0


def _filter_snapshot_by_project(
    payload: dict[str, Any],
    project_id: str,
) -> dict[str, Any]:
    """Return a shallow copy of *payload* with diagnostics/feedback scoped to
    *project_id* (STORY-069.7).

    Entries missing ``project_id`` are **excluded** when the filter is active;
    this prevents legacy single-tenant rows from leaking across tenants.
    """
    filtered = dict(payload)
    for key in ("diagnostics_history", "feedback_events"):
        rows = filtered.get(key) or []
        filtered[key] = [
            row for row in rows
            if isinstance(row, dict) and row.get("project_id") == project_id
        ]
    return filtered


def _service_version() -> str:
    """Return the installed package version, or 'unknown'."""
    try:
        from importlib.metadata import version

        return version("tapps-brain")
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# DB probe helpers
# ---------------------------------------------------------------------------


def _probe_db(dsn: str | None) -> tuple[bool, int | None, str]:
    """Probe the Postgres database for readiness.

    Returns ``(is_ready, migration_version, message)``.

    - ``is_ready``:  False when the DB is unreachable.
    - ``migration_version``: Highest applied schema version, or ``None``.
    - ``message``: Human-readable status detail.

    Never raises — all errors are caught and reported via the return value.
    """
    if not dsn:
        return False, None, "no DSN configured (set TAPPS_BRAIN_DATABASE_URL)"

    try:
        from tapps_brain.postgres_migrations import get_hive_schema_status

        status = get_hive_schema_status(dsn)
        version = status.current_version if status.current_version else None
        pending = len(status.pending_migrations)
        if pending > 0:
            return (
                True,
                version,
                f"ready (migration_version={version}, pending={pending})",
            )
        return True, version, f"ready (migration_version={version})"
    except Exception as exc:
        # Redact DSN components (hostname, port, credentials) from the error
        # message so that sensitive connection details are not exposed in HTTP
        # response bodies.  See STORY-060.3.
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
        except Exception:  # noqa: BLE001
            err_str = "database unreachable"
        return False, None, f"db_error: {err_str}"


# ---------------------------------------------------------------------------
# Metrics helpers — Prometheus text format
# ---------------------------------------------------------------------------


def _collect_metrics(dsn: str | None) -> str:
    """Collect basic runtime metrics and return Prometheus text format.

    Deliberately minimal — high-cardinality labels such as query strings,
    memory keys, or agent IDs are **never** included (EPIC-061 redaction
    policy).
    """
    lines: list[str] = []

    def gauge(name: str, value: float, help_text: str = "") -> None:
        if help_text:
            lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name} {value}")

    # Process uptime (seconds since import)
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

    # Python runtime
    gauge(
        "tapps_brain_python_info",
        1.0,
        f"Python version info (version={sys.version_info.major}.{sys.version_info.minor}).",
    )

    # DB readiness
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

    lines.append("")
    return "\n".join(lines)


# Module-level start time recorded once at import
_PROCESS_START_TIME: float = time.time()


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------


class _Handler(http.server.BaseHTTPRequestHandler):
    """Request handler for the tapps-brain HTTP adapter."""

    # Injected by HttpAdapter before serving
    _dsn: str | None = None
    _version: str = "unknown"
    _auth_token: str | None = None  # None → auth disabled
    _admin_token: str | None = None  # None → admin routes return 503 (EPIC-069)
    _store: Any = None  # MemoryStore | None; Any to avoid import at class level

    # /snapshot TTL cache — shared across all handler instances via class (STORY-065.1).
    # Overridden per-BoundHandler subclass in HttpAdapter.start() so each adapter
    # instance has its own independent cache and lock.
    _snapshot_lock: threading.Lock = threading.Lock()
    _snapshot_cache: Any = None  # VisualSnapshot | None
    _snapshot_cache_at: float = 0.0

    # Routes that are always public — no auth check applied.
    _PUBLIC_PATHS: frozenset[str] = frozenset(
        {"/", "/health", "/ready", "/metrics", "/openapi.json"}
    )

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------

    def _check_auth(self) -> bool:
        """Return True if the request passes auth (or auth is disabled).

        When ``_auth_token`` is ``None`` the route is open.
        Otherwise, the request must include::

            Authorization: Bearer <token>

        Returns False and sends the appropriate 401/403 response when auth
        fails — the caller must return immediately without sending further
        output.
        """
        if not self._auth_token:
            # Auth not configured → open (not-for-production)
            return True

        header = self.headers.get("Authorization", "")
        if not header:
            self._send_json(
                401,
                {
                    "error": "unauthorized",
                    "detail": "Authorization header required (Bearer token).",
                },
            )
            return False

        parts = header.split(" ", 1)
        if len(parts) != _BEARER_PARTS or parts[0].lower() != "bearer":
            self._send_json(
                401,
                {
                    "error": "unauthorized",
                    "detail": "Malformed Authorization header — expected 'Bearer <token>'.",
                },
            )
            return False

        if parts[1] != self._auth_token:
            self._send_json(
                403,
                {
                    "error": "forbidden",
                    "detail": "Invalid token.",
                },
            )
            return False

        return True

    # ------------------------------------------------------------------
    # Route dispatch
    # ------------------------------------------------------------------

    def do_OPTIONS(self) -> None:
        """Handle OPTIONS preflight requests for CORS (STORY-065.1).

        Returns the CORS headers required by browsers when the dashboard at
        port 8088 calls the adapter at port 8080 directly.
        """
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Authorization, Content-Type, X-Tapps-Project",
        )
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ------------------------------------------------------------------
    # Admin auth (EPIC-069 STORY-069.5)
    # ------------------------------------------------------------------

    def _check_admin_auth(self) -> bool:
        """Gate for ``/admin/*`` routes using a separate token.

        Returns False (and sends the response) on any failure.  Admin
        routes short-circuit to 503 when ``TAPPS_BRAIN_ADMIN_TOKEN`` is
        not configured — registering projects against a brain with no
        admin token would bypass the trust model.
        """
        if not self._admin_token:
            self._send_json(
                503,
                {
                    "error": "admin_disabled",
                    "detail": "Admin routes require TAPPS_BRAIN_ADMIN_TOKEN to be set.",
                },
            )
            return False
        header = self.headers.get("Authorization", "")
        parts = header.split(" ", 1)
        if len(parts) != _BEARER_PARTS or parts[0].lower() != "bearer":
            self._send_json(
                401,
                {"error": "unauthorized", "detail": "Bearer token required for admin routes."},
            )
            return False
        if parts[1] != self._admin_token:
            self._send_json(403, {"error": "forbidden", "detail": "Invalid admin token."})
            return False
        return True

    def _read_json_body(self, *, max_bytes: int = 65_536) -> dict[str, Any] | None:
        """Read and decode a JSON request body.

        Returns the parsed object, or ``None`` after sending a 400/413
        response on any failure — callers must ``return`` immediately.
        """
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "bad_request", "detail": "Invalid Content-Length."})
            return None
        if length <= 0:
            self._send_json(400, {"error": "bad_request", "detail": "Empty request body."})
            return None
        if length > max_bytes:
            self._send_json(
                413, {"error": "payload_too_large", "detail": f"Max {max_bytes} bytes."}
            )
            return None
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._send_json(400, {"error": "bad_request", "detail": f"Invalid JSON: {exc}"})
            return None
        if not isinstance(data, dict):
            self._send_json(
                400,
                {"error": "bad_request", "detail": "Request body must be a JSON object."},
            )
            return None
        return data

    def do_GET(self) -> None:
        """Handle GET requests.

        W3C ``traceparent`` propagation (STORY-061.3): when a ``traceparent``
        header is present in the request, its trace context is extracted and
        the request handling span is created as a child of the caller's trace.
        This is a no-op when the OTel SDK is not configured.
        """
        path = self.path.split("?", 1)[0]  # strip query string

        # Extract W3C traceparent context for distributed tracing (STORY-061.3).
        # carrier must be a plain dict with lowercased header names.
        _carrier: dict[str, str] = {}
        _tp = self.headers.get("traceparent")
        if _tp:
            _carrier["traceparent"] = _tp
        _ts = self.headers.get("tracestate")
        if _ts:
            _carrier["tracestate"] = _ts
        _trace_ctx = extract_trace_context(_carrier) if _carrier else None

        with start_span(
            f"GET {path}",
            {"http.method": "GET", "http.route": path},
            kind=SPAN_KIND_SERVER,
            context=_trace_ctx,
        ):
            # Auth gate: apply to routes not in the public set
            if path not in self._PUBLIC_PATHS and not self._check_auth():
                return  # _check_auth already sent the 401/403 response

            try:
                if path in ("/", "/health"):
                    self._handle_health()
                elif path == "/ready":
                    self._handle_ready()
                elif path == "/metrics":
                    self._handle_metrics()
                elif path == "/info":
                    self._handle_info()
                elif path == "/snapshot":
                    self._handle_snapshot()
                elif path == "/openapi.json":
                    self._handle_openapi()
                elif path == "/admin/projects":
                    self._handle_admin_projects_list()
                elif path.startswith("/admin/projects/"):
                    pid = path[len("/admin/projects/") :]
                    self._handle_admin_project_show(pid)
                else:
                    self._send_json(404, {"error": "not_found", "path": path})
            except _ProjectNotRegisteredError as exc:  # STORY-069.4
                self._send_project_not_registered(exc.project_id)

    # ------------------------------------------------------------------
    # Write methods — EPIC-069 admin surface
    # ------------------------------------------------------------------

    def do_POST(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler contract)
        """Handle POST requests — today only the admin project surface."""
        path = self.path.split("?", 1)[0]
        try:
            if path == "/admin/projects":
                self._handle_admin_projects_register()
            elif path.startswith("/admin/projects/") and path.endswith("/approve"):
                pid = path[len("/admin/projects/") : -len("/approve")]
                self._handle_admin_project_approve(pid)
            else:
                self._send_json(404, {"error": "not_found", "path": path})
        except _ProjectNotRegisteredError as exc:  # STORY-069.4
            self._send_project_not_registered(exc.project_id)

    def do_DELETE(self) -> None:  # noqa: N802
        """Handle DELETE requests — admin project removal."""
        path = self.path.split("?", 1)[0]
        try:
            if path.startswith("/admin/projects/"):
                pid = path[len("/admin/projects/") :]
                self._handle_admin_project_delete(pid)
            else:
                self._send_json(404, {"error": "not_found", "path": path})
        except _ProjectNotRegisteredError as exc:  # STORY-069.4
            self._send_project_not_registered(exc.project_id)

    def _handle_health(self) -> None:
        """Liveness: always 200 while process is alive."""
        body: dict[str, Any] = {
            "status": "ok",
            "service": _SERVICE_NAME,
            "version": self._version,
        }
        self._send_json(200, body)

    def _handle_ready(self) -> None:
        """Readiness: DB ping + migration version."""
        is_ready, migration_version, message = _probe_db(self._dsn)
        body: dict[str, Any] = {
            "status": "ready" if is_ready else "degraded",
            "migration_version": migration_version,
            "detail": message,
        }
        status_code = 200 if is_ready else 503
        self._send_json(status_code, body)

    def _handle_metrics(self) -> None:
        """Prometheus text-format metrics."""
        payload = _collect_metrics(self._dsn)
        encoded = payload.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _handle_info(self) -> None:
        """Extended runtime info — auth-protected when token is configured."""
        body: dict[str, Any] = {
            "service": _SERVICE_NAME,
            "version": self._version,
            "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "platform": platform.system(),
            "uptime_seconds": round(time.time() - _PROCESS_START_TIME, 3),
            "auth_enabled": self._auth_token is not None,
            "dsn_configured": self._dsn is not None,
        }
        self._send_json(200, body)

    def _handle_openapi(self) -> None:
        """Return the OpenAPI 3.1 spec as JSON (always public)."""
        self._send_json(200, _OPENAPI_SPEC)

    def _handle_snapshot(self) -> None:
        """Return the live VisualSnapshot as JSON with a 15s TTL cache (STORY-065.1).

        - Returns 503 when the adapter was constructed without a store.
        - Caches the snapshot for ``_SNAPSHOT_TTL_SECONDS`` to avoid hammering
          the store with O(n) list_all() calls on rapid polls.
        - Always includes ``Access-Control-Allow-Origin: *`` so the nginx-served
          dashboard at port 8088 can reach the adapter at port 8080 directly.

        STORY-069.7: when a ``?project=<id>`` query parameter is provided,
        ``diagnostics_history`` and ``feedback_events`` are filtered to rows
        whose ``project_id`` matches exactly.  Rows missing ``project_id``
        (legacy / single-tenant) are excluded when the filter is active.
        Unfiltered behavior (no ``?project=``) is unchanged.
        """
        if self._store is None:
            self._send_json_cors(503, {"error": "no store configured"})
            return

        # Parse the optional ``project`` query param.  Empty string = unfiltered.
        from urllib.parse import parse_qs, urlparse

        _query = urlparse(self.path).query
        _params = parse_qs(_query)
        _project_values = _params.get("project") or []
        project_filter = (_project_values[0].strip() if _project_values else "") or None

        cls = type(self)
        with cls._snapshot_lock:
            now = time.time()
            cache_hit = cls._snapshot_cache is not None and (
                now - cls._snapshot_cache_at
            ) < _SNAPSHOT_TTL_SECONDS
            if cache_hit:
                snapshot = cls._snapshot_cache
            else:
                # Import at call site to avoid circular imports at module load.
                from tapps_brain.visual_snapshot import build_visual_snapshot

                snapshot = build_visual_snapshot(self._store, privacy="standard")
                cls._snapshot_cache = snapshot
                cls._snapshot_cache_at = now

        payload = snapshot.model_dump(mode="json")
        if project_filter is not None:
            payload = _filter_snapshot_by_project(payload, project_filter)
        self._send_json_cors(200, payload)

    # ------------------------------------------------------------------
    # EPIC-069 admin handlers (/admin/projects/*)
    # ------------------------------------------------------------------

    def _open_registry(self) -> tuple[Any, Any] | None:
        """Return ``(registry, connection_manager)`` or ``None`` after
        sending a 503 when no DSN is configured."""
        if not self._dsn:
            self._send_json(
                503,
                {
                    "error": "db_unavailable",
                    "detail": "TAPPS_BRAIN_DATABASE_URL is not configured.",
                },
            )
            return None
        from tapps_brain.postgres_connection import PostgresConnectionManager
        from tapps_brain.project_registry import ProjectRegistry

        cm = PostgresConnectionManager(self._dsn)
        return ProjectRegistry(cm), cm

    def _handle_admin_projects_list(self) -> None:
        """GET /admin/projects — list all registered projects."""
        if not self._check_admin_auth():
            return
        opened = self._open_registry()
        if opened is None:
            return
        registry, cm = opened
        try:
            rows = registry.list_all()
        finally:
            cm.close()
        self._send_json(
            200,
            {
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

    def _handle_admin_project_show(self, project_id: str) -> None:
        """GET /admin/projects/<id> — return one project's record."""
        if not self._check_admin_auth():
            return
        opened = self._open_registry()
        if opened is None:
            return
        registry, cm = opened
        try:
            record = registry.get(project_id)
        finally:
            cm.close()
        if record is None:
            self._send_json(404, {"error": "not_found", "project_id": project_id})
            return
        self._send_json(
            200,
            {
                "project_id": record.project_id,
                "profile": record.profile.model_dump(mode="json"),
                "approved": record.approved,
                "source": record.source,
                "notes": record.notes,
            },
        )

    def _handle_admin_projects_register(self) -> None:
        """POST /admin/projects — register (or overwrite) a project profile.

        Body::

            {
                "project_id": "alpaca",
                "profile": { ...MemoryProfile JSON... },
                "approved": true,
                "source": "admin",
                "notes": ""
            }
        """
        if not self._check_admin_auth():
            return
        body = self._read_json_body()
        if body is None:
            return

        project_id = (body.get("project_id") or "").strip()
        profile_json = body.get("profile")
        approved = bool(body.get("approved", True))
        source = body.get("source") or "admin"
        notes = body.get("notes") or ""

        if not project_id or not isinstance(profile_json, dict):
            self._send_json(
                400,
                {
                    "error": "bad_request",
                    "detail": "project_id and profile (JSON object) are required.",
                },
            )
            return

        try:
            from tapps_brain.profile import MemoryProfile
            from tapps_brain.project_resolver import validate_project_id

            validate_project_id(project_id)
            profile = MemoryProfile.model_validate(profile_json)
        except Exception as exc:
            self._send_json(400, {"error": "bad_request", "detail": str(exc)})
            return

        opened = self._open_registry()
        if opened is None:
            return
        registry, cm = opened
        try:
            record = registry.register(
                project_id,
                profile,
                source=source,
                approved=approved,
                notes=notes,
            )
        except ValueError as exc:
            self._send_json(400, {"error": "bad_request", "detail": str(exc)})
            return
        finally:
            cm.close()
        self._send_json(
            201,
            {
                "project_id": record.project_id,
                "profile_name": record.profile.name,
                "approved": record.approved,
                "source": record.source,
            },
        )

    def _handle_admin_project_approve(self, project_id: str) -> None:
        """POST /admin/projects/<id>/approve — flip approved=true."""
        if not self._check_admin_auth():
            return
        opened = self._open_registry()
        if opened is None:
            return
        registry, cm = opened
        try:
            updated = registry.approve(project_id)
        finally:
            cm.close()
        if not updated:
            self._send_json(404, {"error": "not_found", "project_id": project_id})
            return
        self._send_json(200, {"project_id": project_id, "approved": True})

    def _handle_admin_project_delete(self, project_id: str) -> None:
        """DELETE /admin/projects/<id> — remove a profile row.

        Does not cascade to ``private_memories``.
        """
        if not self._check_admin_auth():
            return
        opened = self._open_registry()
        if opened is None:
            return
        registry, cm = opened
        try:
            deleted = registry.delete(project_id)
        finally:
            cm.close()
        if not deleted:
            self._send_json(404, {"error": "not_found", "project_id": project_id})
            return
        self._send_json(200, {"project_id": project_id, "deleted": True})

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _send_project_not_registered(self, project_id: str) -> None:
        """STORY-069.4: map ProjectNotRegisteredError to a structured 403."""
        self._send_json(
            403,
            {"error": "project_not_registered", "project_id": project_id},
        )

    def _send_json(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_json_cors(self, status: int, body: dict[str, Any]) -> None:
        """Send a JSON response with CORS headers (used by /snapshot)."""
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args: object) -> None:
        """Redirect access log to structlog (suppress noisy stdout output)."""
        logger.debug(
            "http_adapter.request",
            remote=self.address_string(),
            method=self.command,
            path=self.path,
            status=args[1] if len(args) > 1 else "",
        )


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class HttpAdapter:
    """Minimal HTTP adapter exposing liveness, readiness, metrics, and info probes.

    Parameters
    ----------
    host:
        Bind address (default ``"127.0.0.1"``).
    port:
        TCP port to listen on (default ``8080``).
    dsn:
        PostgreSQL DSN used for ``/ready`` and ``/metrics``.
        Falls back to the ``TAPPS_BRAIN_DATABASE_URL`` environment variable,
        then ``TAPPS_BRAIN_HIVE_DSN``.  When neither is set, ``/ready``
        returns ``503`` and ``/metrics`` reports ``tapps_brain_db_ready 0``.
    auth_token:
        Bearer token required for protected routes (``/info``, ``/snapshot``).
        Falls back to the ``TAPPS_BRAIN_HTTP_AUTH_TOKEN`` environment variable.
        When neither is set, protected routes are open (**not for production**).
        Probe routes (``/health``, ``/ready``, ``/metrics``, ``/openapi.json``)
        are always public regardless of this setting.
    store:
        Optional live ``MemoryStore`` instance.  When provided, enables the
        ``GET /snapshot`` endpoint which returns a ``VisualSnapshot`` as JSON
        with a 15-second TTL cache.  When ``None`` (default), ``/snapshot``
        returns ``503``.
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
        self._host = host
        self._port = port
        self._dsn = dsn or self._resolve_dsn_from_env()
        self._auth_token = auth_token or self._resolve_auth_token_from_env()
        self._admin_token = admin_token or self._resolve_admin_token_from_env()
        self._store = store
        self._server: http.server.HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._version = _service_version()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the HTTP server in a background daemon thread."""
        if self._server is not None:
            return  # already running

        # Build a handler subclass with DSN, auth token, and store bound in.
        # Each BoundHandler gets its own lock and cache so multiple adapter
        # instances running in the same process don't share snapshot state.
        dsn = self._dsn
        version = self._version
        auth_token = self._auth_token
        admin_token = self._admin_token
        store = self._store
        _lock = threading.Lock()

        class BoundHandler(_Handler):
            _dsn = dsn
            _version = version
            _auth_token = auth_token
            _admin_token = admin_token
            _store = store
            _snapshot_lock = _lock
            _snapshot_cache: Any = None
            _snapshot_cache_at: float = 0.0

        self._server = http.server.HTTPServer((self._host, self._port), BoundHandler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
            name="tapps-brain-http",
        )
        self._thread.start()
        logger.info(
            "http_adapter.started",
            host=self._host,
            port=self._port,
            platform=platform.system(),
        )

    def stop(self) -> None:
        """Shut down the HTTP server and wait for the background thread to exit."""
        if self._server is None:
            return
        self._server.shutdown()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._server.server_close()
        self._server = None
        self._thread = None
        logger.info("http_adapter.stopped")

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def __enter__(self) -> HttpAdapter:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def address(self) -> tuple[str, int]:
        """Return ``(host, port)`` for the bound server."""
        if self._server is not None:
            return self._server.server_address  # type: ignore[return-value]
        return (self._host, self._port)

    @property
    def is_running(self) -> bool:
        """True if the server thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_dsn_from_env() -> str | None:
        dsn = (
            os.environ.get("TAPPS_BRAIN_DATABASE_URL")
            or os.environ.get("TAPPS_BRAIN_HIVE_DSN")
            or ""
        ).strip()
        return dsn or None

    @staticmethod
    def _resolve_auth_token_from_env() -> str | None:
        # Plain env var takes precedence.
        token = os.environ.get("TAPPS_BRAIN_HTTP_AUTH_TOKEN", "").strip()
        if token:
            return token
        # Docker secrets pattern: _FILE variant points to a file containing the token.
        token_file = os.environ.get("TAPPS_BRAIN_HTTP_AUTH_TOKEN_FILE", "").strip()
        if token_file:
            try:
                return Path(token_file).read_text().strip() or None
            except OSError:
                pass
        return None

    @staticmethod
    def _resolve_admin_token_from_env() -> str | None:
        """Read the admin-route bearer token (EPIC-069)."""
        token = os.environ.get("TAPPS_BRAIN_ADMIN_TOKEN", "").strip()
        if token:
            return token
        token_file = os.environ.get("TAPPS_BRAIN_ADMIN_TOKEN_FILE", "").strip()
        if token_file:
            try:
                return Path(token_file).read_text().strip() or None
            except OSError:
                pass
        return None
