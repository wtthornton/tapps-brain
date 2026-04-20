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

* :mod:`tapps_brain.http.settings`          – ``_Settings``, ``get_settings``
* :mod:`tapps_brain.http.probe_cache`       – ``_probe_db``, pool helpers
* :mod:`tapps_brain.http.metrics_collector` – Prometheus text rendering
* :mod:`tapps_brain.http.profile_resolver`  – singleton ``ProfileResolver``
* :mod:`tapps_brain.http.auth`              – bearer-token auth dependencies
* :mod:`tapps_brain.http.middleware`        – ASGI middleware classes

All public names are re-exported from this module for backward compat.
"""

from __future__ import annotations

import asyncio
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

# ---------------------------------------------------------------------------
# Sub-package imports — split by concern (TAP-604)
# Re-exported at module level for backward compatibility with tests and callers
# that do ``from tapps_brain.http_adapter import <name>``.
# ---------------------------------------------------------------------------

# settings
from tapps_brain.http.settings import (
    _Settings,
    _filter_snapshot_by_project,
    _service_version,
    _settings,
    get_settings,
)

# probe cache
from tapps_brain.http.probe_cache import (
    _PROBE_CACHE,
    _PROBE_CACHE_TTL,
    _get_hive_pool_stats,
    _probe_db,
)

# metrics counter state (re-exported so tests can mutate via ``_mod.X``)
from tapps_brain.http.metrics_collector import (
    _DISTINCT_AGENTS_PER_PROJECT,
    _LABELED_REQUEST_COUNTS,
    _LABELED_REQUEST_COUNTS_LOCK,
    _MAX_AGENT_ID_CARDINALITY,
    _collect_metrics,
    _record_labeled_request,
)

# profile resolver singleton
from tapps_brain.http.profile_resolver import (
    _PROFILE_RESOLVER,
    _PROFILE_RESOLVER_LOCK,
    _get_profile_resolver,
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

# middleware
from tapps_brain.http.middleware import (
    McpTenantMiddleware,
    OriginAllowlistMiddleware,
    OtelSpanMiddleware,
    _ORIGIN_EXEMPT_PATHS,
)

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants / module-level state kept here for closure + test-patch access
# ---------------------------------------------------------------------------

_SERVICE_NAME = "tapps-brain"
_SNAPSHOT_TTL_SECONDS: float = 15.0
_PROCESS_START_TIME: float = time.time()
_BEARER_PREFIX = "bearer "


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
