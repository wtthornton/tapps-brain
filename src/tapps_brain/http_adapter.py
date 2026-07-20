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
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import structlog
from pydantic import ValidationError

try:
    from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
    from fastapi.responses import JSONResponse, PlainTextResponse
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

# metrics counter state (re-exported so tests can mutate via ``_mod.X``).
# TAP-604 follow-up: metrics_collector is the single implementation — the
# duplicated copies that used to shadow these imports made the live /metrics
# endpoint read counters nothing wrote (split-brain).
from tapps_brain.http.metrics_collector import (  # noqa: F401
    _DISTINCT_AGENTS_PER_PROJECT,
    _HTTP_ERROR_COUNTS,
    _HTTP_ERROR_COUNTS_LOCK,
    _LABELED_REQUEST_COUNTS,
    _LABELED_REQUEST_COUNTS_LOCK,
    _MAX_AGENT_ID_CARDINALITY,
    _collect_metrics,
    _emit_probe_histogram,
    _emit_snapshot_metrics,
    _record_http_error,
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
    _resolve_tenant_headers,
)

# probe cache
from tapps_brain.http.probe_cache import (  # noqa: F401
    _PROBE_CACHE_TTL,
    _get_hive_pool_stats,
    _probe_db,
    _probe_experience_schema,
)

# profile resolver singleton
from tapps_brain.http.profile_resolver import (  # noqa: F401
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
from tapps_brain.http.settings import (  # noqa: F401
    _filter_snapshot_by_project,
    _service_version,
    _Settings,
    _settings,
    get_settings,
)

# Re-exported for http.middleware.OtelSpanMiddleware, which reads these via a
# lazy ``import tapps_brain.http_adapter`` so tests patching them keep working.
from tapps_brain.otel_tracer import (  # noqa: F401
    SPAN_KIND_SERVER,
    extract_trace_context,
    start_span,
)
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
# PUT /v1/documents (TAP-5003): documents.max_doc_bytes defaults to 2 MiB of
# raw content; base64 transport inflates by 4/3, plus JSON envelope headroom.
_DOCUMENTS_MAX_BODY_BYTES: int = 3_145_728  # 3 MiB

# Service error code → HTTP status for /v1/documents routes.
_DOCUMENT_ERROR_STATUS: dict[str, int] = {
    "document_too_large": 413,
    "not_found": 404,
    "documents_unavailable": 503,
}

# ---------------------------------------------------------------------------
# OpenAPI spec — generated from FastAPI's route table and enriched with
# the dual auth schemes, tenant headers, error envelope, and the ASGI-mounted
# /mcp route by :mod:`tapps_brain.openapi_contract` (TAP-508).  The checked-in
# snapshot lives under ``docs/contracts/`` and is gated by CI.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _coerce_int(body: dict[str, Any], field: str, default: int) -> int:
    """Coerce a client-supplied body field to ``int`` or raise a 400.

    TAP-2865/TAP-2140 class: a malformed *client* payload (``"five"``, a list,
    a dict) must report as ``bad_request``, not fall through ``int()`` to the
    generic 500 catch-all.
    """
    raw = body.get(field, default)
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail={"error": "bad_request", "detail": f"'{field}' must be an integer."},
        ) from None


def _coerce_float(body: dict[str, Any], field: str, default: float) -> float:
    """Coerce a client-supplied body field to ``float`` or raise a 400."""
    raw = body.get(field, default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail={"error": "bad_request", "detail": f"'{field}' must be a number."},
        ) from None


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


async def _parse_json_object_body(
    request: Request, *, max_bytes: int = 65_536
) -> dict[str, Any]:
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
    if len(raw) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail={"error": "payload_too_large", "detail": f"Max {max_bytes} bytes."},
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


def _resolve_wrapped_default_store(store: Any) -> Any:
    """Return the inner default store when ``store`` is a Hive ``_StoreProxy`` wrapper.

    Do not use ``getattr(store, "_default_store", store)`` on :class:`MagicMock` —
    mocks auto-create ``_default_store`` children, which breaks tenant matching in
    integration tests that inject mock stores.
    """
    store_dict = getattr(store, "__dict__", None)
    if isinstance(store_dict, dict) and "_default_store" in store_dict:
        return store_dict["_default_store"]
    slots = getattr(type(store), "__slots__", ())
    if isinstance(slots, str):
        slots = (slots,)
    if isinstance(slots, (tuple, list)) and "_default_store" in slots:
        return store._default_store
    return store


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
                from tapps_brain.postgres_connection import is_postgres_dsn

                if is_postgres_dsn(_dsn):
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
                logger.error("http_adapter.mcp_build_failed", error=str(exc), exc_info=True)
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
                    logger.error(
                        "http_adapter.session_manager_start_failed",
                        error=str(exc),
                        exc_info=True,
                    )
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
                except ValueError as exc:
                    logger.error("http_adapter.rest_profile_gate_drift", error=str(exc))
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
            # Release the shared project-registry pool used by per-tenant
            # auth and the /admin/* routes.
            from tapps_brain.http.auth import close_registry_cm

            close_registry_cm()

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
    # = first to process requests.  Request order is therefore:
    #   OtelSpan → OriginAllowlist → RestProfileGate → McpTenant → handlers.
    # OtelSpanMiddleware is outermost so every rejection (403 from the Origin
    # allowlist, 400/401/403 from the profile gate or /mcp bearer auth) still
    # gets a server span + traceparent propagation.  Origin allowlist runs
    # before MCP tenant auth so a bad Origin returns 403 before the auth check
    # can return 401/403 (TAP-627).  RestProfileGateMiddleware (TAP-1929) only
    # touches /v1/* paths and runs before the route handlers so denials avoid
    # the body parse / DB hop entirely.
    app.add_middleware(McpTenantMiddleware)
    app.add_middleware(RestProfileGateMiddleware)
    app.add_middleware(OriginAllowlistMiddleware)
    app.add_middleware(OtelSpanMiddleware)

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
            # The body is a function of X-Brain-Profile (TAP-1929) — without
            # Vary, a shared cache would key on URL alone and serve whichever
            # profile variant it cached first to every consumer for 300 s.
            "Vary": "X-Brain-Profile",
            "X-Brain-Version": cfg.version,
            "X-Catalog-Generated-At": str(generated_at),
        }
        # If-None-Match may carry a comma-separated list of validators (RFC
        # 9110 §13.1.2) — match any entry, not just an exact single string.
        client_etags = [
            v.strip() for v in (request.headers.get("if-none-match") or "").split(",") if v.strip()
        ]
        if etag in client_etags or "*" in client_etags:
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
        from tapps_brain.project_resolver import InvalidProjectIdError

        base = _get_store_or_503()
        default_store = _resolve_wrapped_default_store(base)
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
        except InvalidProjectIdError as exc:
            # Invalid slugs (e.g. leading underscore ``_system``) must be 400,
            # not an unhandled ASGI 500 from MemoryStore profile resolution.
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "invalid_project_id",
                    "detail": str(exc),
                    "project_id": project_id,
                },
            ) from exc
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
        base = _resolve_wrapped_default_store(cfg.store)
        store_pid = getattr(base, "_project_id", None) or ""
        store_aid = getattr(base, "_agent_id", None) or ""
        eff_agent = (agent_id or "").strip() or "unknown"
        agent_ok = bool(store_aid) and eff_agent == store_aid
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
    # Each entry is ``[lock, refcount]``.  The refcount tracks every request
    # registered on the guard (holder + waiters).  ``asyncio.Lock.release()``
    # clears ``locked()`` synchronously while waiters only resume on a later
    # event-loop iteration, so a ``locked()`` check at release time always saw
    # ``False`` and dropped the entry while duplicates were still queued on it
    # — a third duplicate then minted a fresh lock and ran concurrently,
    # re-opening the TAP-629 window.  Refcounting closes that hole.
    _idem_guards: dict[str, list[Any]] = {}

    def _idem_guard_key(pid: str, ikey: str) -> str:
        return f"{pid}\x00{ikey}"

    async def _acquire_idem_guard(pid: str, ikey: str) -> asyncio.Lock:
        """Register on and acquire the guard lock for ``(pid, ikey)``.

        The refcount is incremented *before* awaiting the lock so that the
        holder's release path cannot drop the entry while this coroutine is
        still queued on it.
        """
        gk = _idem_guard_key(pid, ikey)
        entry = _idem_guards.setdefault(gk, [asyncio.Lock(), 0])
        entry[1] += 1
        try:
            await entry[0].acquire()
        except BaseException:
            entry[1] -= 1
            if entry[1] <= 0:
                _idem_guards.pop(gk, None)
            raise
        return cast("asyncio.Lock", entry[0])

    def _release_idem_guard(pid: str, ikey: str) -> None:
        """Release the guard lock and drop the entry once no request holds it."""
        gk = _idem_guard_key(pid, ikey)
        entry = _idem_guards.get(gk)
        if entry is None:  # pragma: no cover - release always follows acquire
            return
        entry[0].release()
        entry[1] -= 1
        if entry[1] <= 0:
            _idem_guards.pop(gk, None)

    def _idempotency_check(
        istore: Any, project_id: str, ikey: str
    ) -> tuple[int, dict[str, Any]] | None:
        """Run idempotency check (raises IdempotencyUnavailableError on failure)."""
        return cast(
            "tuple[int, dict[str, Any]] | None",
            istore.check(project_id, ikey),
        )

    def _get_ikey_and_istore(request: Request) -> tuple[str | None, Any]:
        """Extract idempotency key + singleton store, or (None, None).

        Returns (None, None) when idempotency is disabled, the header is
        absent, or the ``IdempotencyStore`` singleton was not built at
        startup (lifespan failure / feature flag off).

        Raises ``HTTPException(503, idempotency_unavailable)`` when the
        feature is enabled and the client sent a key but the store failed to
        build — a retryable outage, not a server bug.  Mapping the 503 here
        keeps the error contract identical across every route that calls this
        (the single-shot routes used to let it fall through to the generic
        500 catch-all while the batch routes returned 503).

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
        if istore is None:
            # Feature enabled + client key present but store failed to init —
            # refuse rather than silently allowing duplicate writes.
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "idempotency_unavailable",
                    "detail": "Idempotency is enabled but the idempotency store is unavailable",
                },
            )
        return ikey, istore

    def _idempotency_save(project_id: str, ikey: str, status: int, body: dict[str, Any]) -> None:
        """Persist idempotency key → response when enabled.

        TAP-548: writes through the process-wide ``cfg.idempotency_store``
        singleton.  Failures raise :class:`IdempotencyUnavailableError` so
        callers map them to HTTP 503 instead of allowing silent duplicate
        writes on retry.
        """
        from tapps_brain.idempotency import is_idempotency_enabled

        if not is_idempotency_enabled():
            return
        istore = getattr(cfg, "idempotency_store", None)
        if istore is None:
            from tapps_brain.idempotency import IdempotencyUnavailableError

            raise IdempotencyUnavailableError(
                "Idempotency is enabled but the idempotency store is unavailable"
            )
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
        # X-Tapps-Agent wins over X-Agent-Id (same precedence as MCP / middleware).
        _, agent_id, _, _ = _resolve_tenant_headers(request)
        store = _get_tenant_store_or_503(project_id, agent_id)

        # TAP-629: acquire per-key guard BEFORE the cache check so that
        # concurrent duplicates yield at ``await guard.acquire()`` rather
        # than racing through check → execute → save.  The second (and
        # later) coroutines wake up after the first stores its result,
        # see the cached body, and return without re-running the handler.
        ikey, istore = _get_ikey_and_istore(request)
        guard: asyncio.Lock | None = None
        if ikey and istore is not None:
            guard = await _acquire_idem_guard(project_id, ikey)

        try:
            # Cache check — inside the guard so we observe the result
            # stored by whichever concurrent duplicate ran first.
            if ikey and istore is not None:
                from tapps_brain.idempotency import IdempotencyUnavailableError

                try:
                    _cached = await asyncio.to_thread(_idempotency_check, istore, project_id, ikey)
                except IdempotencyUnavailableError as exc:
                    raise HTTPException(
                        status_code=503,
                        detail={
                            "error": "idempotency_unavailable",
                            "detail": str(exc),
                        },
                    ) from exc
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
                    confidence=_coerce_float(body, "confidence", -1.0),
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
                    confidence=_coerce_float(body, "confidence", -1.0),
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
                from tapps_brain.idempotency import IdempotencyUnavailableError

                try:
                    await asyncio.to_thread(istore.save, project_id, ikey, status_code, result)
                except IdempotencyUnavailableError as exc:
                    raise HTTPException(
                        status_code=503,
                        detail={
                            "error": "idempotency_unavailable",
                            "detail": str(exc),
                        },
                    ) from exc

            return JSONResponse(status_code=status_code, content=result)

        finally:
            # Release the per-key guard so any waiting duplicates can wake
            # up, re-check the cache, and return the stored response.
            if guard is not None and ikey:
                _release_idem_guard(project_id, ikey)

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
        # X-Tapps-Agent wins over X-Agent-Id (same precedence as MCP / middleware).
        _, agent_id, _, _ = _resolve_tenant_headers(request)
        store = _get_tenant_store_or_503(project_id, agent_id)

        # TAP-629: acquire per-key guard before cache check (see _v1_remember).
        ikey, istore = _get_ikey_and_istore(request)
        guard: asyncio.Lock | None = None
        if ikey and istore is not None:
            guard = await _acquire_idem_guard(project_id, ikey)

        try:
            # Cache check inside the guard.
            if ikey and istore is not None:
                from tapps_brain.idempotency import IdempotencyUnavailableError

                try:
                    _cached = await asyncio.to_thread(_idempotency_check, istore, project_id, ikey)
                except IdempotencyUnavailableError as exc:
                    raise HTTPException(
                        status_code=503,
                        detail={
                            "error": "idempotency_unavailable",
                            "detail": str(exc),
                        },
                    ) from exc
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
                    confidence_boost=_coerce_float(body, "confidence_boost", 0.0),
                )
            else:
                # TAP-1099: offload sync DB call to a worker thread.
                result = await asyncio.to_thread(
                    _ms.memory_reinforce,
                    store,
                    project_id,
                    agent_id,
                    key=mem_key,
                    confidence_boost=_coerce_float(body, "confidence_boost", 0.0),
                )
            if isinstance(result, dict) and "error" in result:
                # Taxonomy: not_found → 404; other service errors stay 400.
                status_code = 404 if result.get("error") == "not_found" else 400
            else:
                status_code = 200

            if ikey and istore is not None:
                from tapps_brain.idempotency import IdempotencyUnavailableError

                try:
                    await asyncio.to_thread(istore.save, project_id, ikey, status_code, result)
                except IdempotencyUnavailableError as exc:
                    raise HTTPException(
                        status_code=503,
                        detail={
                            "error": "idempotency_unavailable",
                            "detail": str(exc),
                        },
                    ) from exc

            return JSONResponse(status_code=status_code, content=result)

        finally:
            if guard is not None and ikey:
                _release_idem_guard(project_id, ikey)

    # -------- bulk data-plane routes (STORY-070.6) --------

    @app.post("/v1/remember:batch", dependencies=[Depends(require_data_plane_auth)])
    async def _v1_remember_batch(request: Request) -> JSONResponse:
        """Save multiple memory entries in one request (max configurable via TAPPS_BRAIN_MAX_BATCH_SIZE).

        Request headers:
          - ``X-Project-Id`` (required): project identifier.
          - ``X-Agent-Id`` (optional, default ``"unknown"``): agent identifier.
          - ``X-Idempotency-Key`` (optional): idempotency UUID.

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
        # X-Tapps-Agent wins over X-Agent-Id (same precedence as MCP / middleware).
        _, agent_id, _, _ = _resolve_tenant_headers(request)
        store = _get_tenant_store_or_503(project_id, agent_id)

        from tapps_brain.idempotency import IdempotencyUnavailableError

        # Raises HTTPException(503, idempotency_unavailable) when the store
        # singleton is missing — mapped inside the helper (TAP-548 follow-up).
        ikey, istore = _get_ikey_and_istore(request)
        guard: asyncio.Lock | None = None
        if ikey and istore is not None:
            guard = await _acquire_idem_guard(project_id, ikey)

        try:
            if ikey and istore is not None:
                try:
                    _cached = await asyncio.to_thread(_idempotency_check, istore, project_id, ikey)
                except IdempotencyUnavailableError as exc:
                    raise HTTPException(
                        status_code=503,
                        detail={"error": "idempotency_unavailable", "detail": str(exc)},
                    ) from exc
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
            if len(raw) > 10 * 1_048_576:  # 10 MiB
                raise HTTPException(
                    status_code=413,
                    detail={
                        "error": "payload_too_large",
                        "detail": "Max 10 MiB for batch requests.",
                    },
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
            if ikey and istore is not None:
                try:
                    await asyncio.to_thread(istore.save, project_id, ikey, status_code, result)
                except IdempotencyUnavailableError as exc:
                    raise HTTPException(
                        status_code=503,
                        detail={"error": "idempotency_unavailable", "detail": str(exc)},
                    ) from exc
            return JSONResponse(status_code=status_code, content=result)
        finally:
            if guard is not None and ikey:
                _release_idem_guard(project_id, ikey)

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
        # X-Tapps-Agent wins over X-Agent-Id (same precedence as MCP / middleware).
        _, agent_id, _, _ = _resolve_tenant_headers(request)
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
          - ``X-Idempotency-Key`` (optional): idempotency UUID.

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
        # X-Tapps-Agent wins over X-Agent-Id (same precedence as MCP / middleware).
        _, agent_id, _, _ = _resolve_tenant_headers(request)
        store = _get_tenant_store_or_503(project_id, agent_id)

        from tapps_brain.idempotency import IdempotencyUnavailableError

        # Raises HTTPException(503, idempotency_unavailable) when the store
        # singleton is missing — mapped inside the helper (TAP-548 follow-up).
        ikey, istore = _get_ikey_and_istore(request)
        guard: asyncio.Lock | None = None
        if ikey and istore is not None:
            guard = await _acquire_idem_guard(project_id, ikey)

        try:
            if ikey and istore is not None:
                try:
                    _cached = await asyncio.to_thread(_idempotency_check, istore, project_id, ikey)
                except IdempotencyUnavailableError as exc:
                    raise HTTPException(
                        status_code=503,
                        detail={"error": "idempotency_unavailable", "detail": str(exc)},
                    ) from exc
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
            if len(raw) > 10 * 1_048_576:
                raise HTTPException(
                    status_code=413,
                    detail={
                        "error": "payload_too_large",
                        "detail": "Max 10 MiB for batch requests.",
                    },
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
            if ikey and istore is not None:
                try:
                    await asyncio.to_thread(istore.save, project_id, ikey, status_code, result)
                except IdempotencyUnavailableError as exc:
                    raise HTTPException(
                        status_code=503,
                        detail={"error": "idempotency_unavailable", "detail": str(exc)},
                    ) from exc
            return JSONResponse(status_code=status_code, content=result)
        finally:
            if guard is not None and ikey:
                _release_idem_guard(project_id, ikey)

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
        # X-Tapps-Agent wins over X-Agent-Id (same precedence as MCP / middleware).
        _, agent_id, _, _ = _resolve_tenant_headers(request)
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

        max_results = _coerce_int(body, "max_results", 5)
        max_results = max(max_results, 1)

        from tapps_brain.services import memory_service as _ms

        filter_tags = body.get("filter_tags")
        filter_tags_any = body.get("filter_tags_any")
        for field_name, tags_val in (
            ("filter_tags", filter_tags),
            ("filter_tags_any", filter_tags_any),
        ):
            if tags_val is None:
                continue
            if not isinstance(tags_val, list) or not all(isinstance(t, str) for t in tags_val):
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "bad_request",
                        "detail": f"{field_name} must be a JSON array of strings.",
                    },
                )

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
            filter_tags=filter_tags,
            filter_tags_any=filter_tags_any,
            filter_memory_class=body.get("filter_memory_class"),
        )
        return JSONResponse(status_code=200, content={"results": results, "query": query})

    @app.post("/v1/forget", dependencies=[Depends(require_data_plane_auth)])
    async def _v1_forget(request: Request) -> JSONResponse:
        """Archive a memory by key.

        REST counterpart of the ``brain_forget`` MCP tool. The entry is
        archived to the ``gc_archive`` table and removed from the active
        store — not permanently deleted.

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
        # X-Tapps-Agent wins over X-Agent-Id (same precedence as MCP / middleware).
        _, agent_id, _, _ = _resolve_tenant_headers(request)
        store = _get_tenant_store_or_503(project_id, agent_id)

        ikey, istore = _get_ikey_and_istore(request)
        guard: asyncio.Lock | None = None
        if ikey and istore is not None:
            guard = await _acquire_idem_guard(project_id, ikey)

        try:
            if ikey and istore is not None:
                from tapps_brain.idempotency import IdempotencyUnavailableError

                try:
                    _cached = await asyncio.to_thread(_idempotency_check, istore, project_id, ikey)
                except IdempotencyUnavailableError as exc:
                    raise HTTPException(
                        status_code=503,
                        detail={
                            "error": "idempotency_unavailable",
                            "detail": str(exc),
                        },
                    ) from exc
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
                from tapps_brain.idempotency import IdempotencyUnavailableError

                try:
                    await asyncio.to_thread(istore.save, project_id, ikey, status_code, result)
                except IdempotencyUnavailableError as exc:
                    raise HTTPException(
                        status_code=503,
                        detail={
                            "error": "idempotency_unavailable",
                            "detail": str(exc),
                        },
                    ) from exc

            return JSONResponse(status_code=status_code, content=result)

        finally:
            if guard is not None and ikey:
                _release_idem_guard(project_id, ikey)

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
        # X-Tapps-Agent wins over X-Agent-Id (same precedence as MCP / middleware).
        _, agent_id, _, _ = _resolve_tenant_headers(request)
        store = _get_tenant_store_or_503(project_id, agent_id)

        ikey, istore = _get_ikey_and_istore(request)
        guard: asyncio.Lock | None = None
        if ikey and istore is not None:
            guard = await _acquire_idem_guard(project_id, ikey)

        try:
            if ikey and istore is not None:
                from tapps_brain.idempotency import IdempotencyUnavailableError

                try:
                    _cached = await asyncio.to_thread(_idempotency_check, istore, project_id, ikey)
                except IdempotencyUnavailableError as exc:
                    raise HTTPException(
                        status_code=503,
                        detail={
                            "error": "idempotency_unavailable",
                            "detail": str(exc),
                        },
                    ) from exc
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

            # Accept MCP-style ``description`` or REST ``task_description``.
            task_description = (
                body.get("description") or body.get("task_description") or ""
            ).strip()
            if not task_description:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "bad_request",
                        "detail": "description or task_description is required.",
                    },
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
            status_code = 400 if isinstance(result, dict) and "error" in result else 200

            if ikey and istore is not None:
                from tapps_brain.idempotency import IdempotencyUnavailableError

                try:
                    await asyncio.to_thread(istore.save, project_id, ikey, status_code, result)
                except IdempotencyUnavailableError as exc:
                    raise HTTPException(
                        status_code=503,
                        detail={
                            "error": "idempotency_unavailable",
                            "detail": str(exc),
                        },
                    ) from exc

            return JSONResponse(status_code=status_code, content=result)

        finally:
            if guard is not None and ikey:
                _release_idem_guard(project_id, ikey)

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
        # X-Tapps-Agent wins over X-Agent-Id (same precedence as MCP / middleware).
        _, agent_id, _, _ = _resolve_tenant_headers(request)
        store = _get_tenant_store_or_503(project_id, agent_id)

        ikey, istore = _get_ikey_and_istore(request)
        guard: asyncio.Lock | None = None
        if ikey and istore is not None:
            guard = await _acquire_idem_guard(project_id, ikey)

        try:
            if ikey and istore is not None:
                from tapps_brain.idempotency import IdempotencyUnavailableError

                try:
                    _cached = await asyncio.to_thread(_idempotency_check, istore, project_id, ikey)
                except IdempotencyUnavailableError as exc:
                    raise HTTPException(
                        status_code=503,
                        detail={
                            "error": "idempotency_unavailable",
                            "detail": str(exc),
                        },
                    ) from exc
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
            status_code = 400 if isinstance(result, dict) and "error" in result else 200

            if ikey and istore is not None:
                from tapps_brain.idempotency import IdempotencyUnavailableError

                try:
                    await asyncio.to_thread(istore.save, project_id, ikey, status_code, result)
                except IdempotencyUnavailableError as exc:
                    raise HTTPException(
                        status_code=503,
                        detail={
                            "error": "idempotency_unavailable",
                            "detail": str(exc),
                        },
                    ) from exc

            return JSONResponse(status_code=status_code, content=result)

        finally:
            if guard is not None and ikey:
                _release_idem_guard(project_id, ikey)

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
        # X-Tapps-Agent wins over X-Agent-Id (same precedence as MCP / middleware).
        _, agent_id, _, _ = _resolve_tenant_headers(request)

        from tapps_brain.idempotency import IdempotencyUnavailableError

        # Raises HTTPException(503, idempotency_unavailable) when the store
        # singleton is missing — mapped inside the helper (TAP-548 follow-up).
        ikey, istore = _get_ikey_and_istore(request)
        guard: asyncio.Lock | None = None
        if ikey and istore is not None:
            guard = await _acquire_idem_guard(project_id, ikey)

        try:
            if ikey and istore is not None:
                try:
                    _cached = await asyncio.to_thread(_idempotency_check, istore, project_id, ikey)
                except IdempotencyUnavailableError as exc:
                    raise HTTPException(
                        status_code=503,
                        detail={"error": "idempotency_unavailable", "detail": str(exc)},
                    ) from exc
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
                logger.exception("http_adapter.kg.read_body_failed")
                raise HTTPException(
                    status_code=400,
                    detail={"error": "bad_request", "detail": "Failed to read request body."},
                )
            if not raw:
                raise HTTPException(
                    status_code=400,
                    detail={"error": "bad_request", "detail": "Empty request body."},
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
                    detail={
                        "error": "bad_request",
                        "detail": "Request body must be a JSON object.",
                    },
                )

            event_type = (body.get("event_type") or "").strip()
            if not event_type:
                raise HTTPException(
                    status_code=400,
                    detail={"error": "bad_request", "detail": "event_type is required."},
                )

            cm = _get_kg_cm_or_503()
            from tapps_brain.services import kg_service as _kg_svc

            if "payload" not in body or body.get("payload") is None:
                payload: dict[str, Any] = {}
            else:
                payload_raw = body.get("payload")
                if not isinstance(payload_raw, dict):
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "error": "bad_request",
                            "detail": "payload must be a JSON object.",
                        },
                    )
                payload = payload_raw

            try:
                result = await asyncio.to_thread(
                    _kg_svc.record_event,
                    cm,
                    project_id,
                    _kg_brain_id(),
                    agent_id,
                    event_type=event_type,
                    subject_key=body.get("subject_key") or None,
                    utility_score=_coerce_float(body, "utility_score", 0.0),
                    payload=payload,
                    entities=list(body.get("entities") or []),
                    edges=list(body.get("edges") or []),
                    evidence=list(body.get("evidence") or []),
                    memory_key=body.get("memory_key") or None,
                    memory_value=body.get("memory_value") or None,
                    memory_tier=str(body.get("memory_tier") or "pattern"),
                    session_id=body.get("session_id") or None,
                    workflow_run_id=body.get("workflow_run_id") or None,
                )
            except Exception as exc:
                # Pydantic ValidationError (and similar) → 400, not ASGI 500.
                from pydantic import ValidationError as _PydanticValidationError

                if isinstance(exc, _PydanticValidationError):
                    raise HTTPException(
                        status_code=400,
                        detail={"error": "bad_request", "detail": str(exc)},
                    ) from exc
                raise
            # Hydrate tenant MemoryStore cache after out-of-band private_memories write.
            mem_key = result.get("memory_key") if isinstance(result, dict) else None
            if mem_key:
                try:
                    store = _get_tenant_store_or_503(project_id, agent_id)
                    ensure = getattr(store, "_ensure_entry_cached", None)
                    if callable(ensure):
                        ensure(str(mem_key))
                except Exception:
                    logger.warning(
                        "http_adapter.experience_cache_hydrate_failed",
                        project_id=project_id,
                        memory_key=mem_key,
                        exc_info=True,
                    )
            if ikey and istore is not None:
                try:
                    await asyncio.to_thread(istore.save, project_id, ikey, 200, result)
                except IdempotencyUnavailableError as exc:
                    raise HTTPException(
                        status_code=503,
                        detail={"error": "idempotency_unavailable", "detail": str(exc)},
                    ) from exc
            # TAP-2727: data-plane writes return 200 (matches /v1/remember,
            # /v1/learn_*, /v1/reinforce and the documented OpenAPI contract).
            return JSONResponse(status_code=200, content=result)
        finally:
            if guard is not None and ikey:
                _release_idem_guard(project_id, ikey)

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
        # X-Tapps-Agent wins over X-Agent-Id (same precedence as MCP / middleware).
        _, agent_id, _, _ = _resolve_tenant_headers(request)

        from tapps_brain.idempotency import IdempotencyUnavailableError

        # Raises HTTPException(503, idempotency_unavailable) when the store
        # singleton is missing — mapped inside the helper (TAP-548 follow-up).
        ikey, istore = _get_ikey_and_istore(request)
        guard: asyncio.Lock | None = None
        if ikey and istore is not None:
            guard = await _acquire_idem_guard(project_id, ikey)

        try:
            if ikey and istore is not None:
                try:
                    _cached = await asyncio.to_thread(_idempotency_check, istore, project_id, ikey)
                except IdempotencyUnavailableError as exc:
                    raise HTTPException(
                        status_code=503,
                        detail={"error": "idempotency_unavailable", "detail": str(exc)},
                    ) from exc
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
                logger.exception("http_adapter.kg.batch.read_body_failed")
                raise HTTPException(
                    status_code=400,
                    detail={"error": "bad_request", "detail": "Failed to read request body."},
                )
            if not raw:
                raise HTTPException(
                    status_code=400,
                    detail={"error": "bad_request", "detail": "Empty request body."},
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
                    detail={
                        "error": "bad_request",
                        "detail": "Request body must be a JSON object.",
                    },
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
            # Hydrate cache for any memory keys written by the batch.
            if isinstance(result, dict):
                try:
                    store = _get_tenant_store_or_503(project_id, agent_id)
                    ensure = getattr(store, "_ensure_entry_cached", None)
                    if callable(ensure):
                        for item in result.get("results") or []:
                            if isinstance(item, dict) and item.get("memory_key"):
                                ensure(str(item["memory_key"]))
                except Exception:
                    logger.warning(
                        "http_adapter.experience_batch_cache_hydrate_failed",
                        project_id=project_id,
                        exc_info=True,
                    )
            if ikey and istore is not None:
                try:
                    await asyncio.to_thread(istore.save, project_id, ikey, 200, result)
                except IdempotencyUnavailableError as exc:
                    raise HTTPException(
                        status_code=503,
                        detail={"error": "idempotency_unavailable", "detail": str(exc)},
                    ) from exc
            # TAP-2727: data-plane writes return 200 (matches the single-event
            # /v1/experience endpoint and the documented OpenAPI contract).
            return JSONResponse(status_code=200, content=result)
        finally:
            if guard is not None and ikey:
                _release_idem_guard(project_id, ikey)

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
            limit=_coerce_int(body, "limit", _kg_svc._QUERY_EVENTS_DEFAULT_LIMIT),
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

    # ------------------------------------------------------------------
    # Document plane routes (TAP-4998 / TAP-5003)
    # ------------------------------------------------------------------

    def _require_project_id(request: Request) -> str:
        project_id = (request.headers.get("x-project-id") or "").strip()
        if not project_id:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "X-Project-Id header is required."},
            )
        return project_id

    def _document_response(result: dict[str, Any]) -> JSONResponse:
        error = result.get("error") if isinstance(result, dict) else None
        if error is not None:
            return JSONResponse(
                status_code=_DOCUMENT_ERROR_STATUS.get(str(error), 400),
                content=result,
            )
        return JSONResponse(status_code=200, content=result)

    @app.put("/v1/documents", dependencies=[Depends(require_data_plane_auth)])
    async def _v1_documents_put(request: Request) -> JSONResponse:
        """Store a document durably with optional deterministic chunk + embed indexing.

        Request headers:
          - ``X-Project-Id`` (required): project identifier.
          - ``X-Agent-Id`` (optional): agent identifier (recorded as writer).

        Request body (JSON):
          ``{ "title": str, "content"?: str, "content_base64"?: str,
              "content_type"?: str, "tags"?: [str], "index"?: bool = true,
              "retention"?: "project" | "days:<n>" }``

        Content above ``documents.max_doc_bytes`` (default 2 MiB) is rejected
        with 413 ``document_too_large``.
        """
        project_id = _require_project_id(request)
        _, agent_id, _, _ = _resolve_tenant_headers(request)
        store = _get_tenant_store_or_503(project_id, agent_id)
        body = await _parse_json_object_body(request, max_bytes=_DOCUMENTS_MAX_BODY_BYTES)

        tags = body.get("tags")
        if tags is not None and not (
            isinstance(tags, list) and all(isinstance(t, str) for t in tags)
        ):
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_request", "detail": "tags must be a list of strings."},
            )

        from tapps_brain.services import document_service as _docs_svc

        result = await asyncio.to_thread(
            _docs_svc.document_put,
            store,
            project_id,
            agent_id,
            title=str(body.get("title") or ""),
            content=str(body.get("content") or ""),
            content_base64=str(body.get("content_base64") or ""),
            content_type=str(body.get("content_type") or "text/plain"),
            tags=tags,
            index=bool(body.get("index", True)),
            retention=str(body.get("retention") or "project"),
        )
        return _document_response(result)

    @app.get("/v1/documents", dependencies=[Depends(require_data_plane_auth)])
    async def _v1_documents_list(
        request: Request,
        tag: str = Query("", description="Only documents carrying this tag."),
        limit: int = Query(100, ge=1, le=500),
    ) -> JSONResponse:
        """List document metadata for the project (newest first)."""
        project_id = _require_project_id(request)
        _, agent_id, _, _ = _resolve_tenant_headers(request)
        store = _get_tenant_store_or_503(project_id, agent_id)

        from tapps_brain.services import document_service as _docs_svc

        result = await asyncio.to_thread(
            _docs_svc.document_list,
            store,
            project_id,
            agent_id,
            tag=tag,
            limit=limit,
        )
        return _document_response(result)

    @app.post("/v1/documents:search", dependencies=[Depends(require_data_plane_auth)])
    async def _v1_documents_search(request: Request) -> JSONResponse:
        """Hybrid tsvector + pgvector search over document chunks, RRF-fused.

        Request body (JSON): ``{ "query": str, "limit"?: int = 10 }``
        """
        project_id = _require_project_id(request)
        _, agent_id, _, _ = _resolve_tenant_headers(request)
        store = _get_tenant_store_or_503(project_id, agent_id)
        body = await _parse_json_object_body(request)

        from tapps_brain.services import document_service as _docs_svc

        result = await asyncio.to_thread(
            _docs_svc.document_search,
            store,
            project_id,
            agent_id,
            query=str(body.get("query") or ""),
            limit=_coerce_int(body, "limit", 10),
        )
        return _document_response(result)

    @app.get("/v1/documents/{doc_id}", dependencies=[Depends(require_data_plane_auth)])
    async def _v1_documents_get(
        request: Request,
        doc_id: str,
        meta_only: int = Query(0, description="1 to omit content from the response."),
    ) -> JSONResponse:
        """Fetch one document's metadata and (unless ``meta_only=1``) its content."""
        project_id = _require_project_id(request)
        _, agent_id, _, _ = _resolve_tenant_headers(request)
        store = _get_tenant_store_or_503(project_id, agent_id)

        from tapps_brain.services import document_service as _docs_svc

        result = await asyncio.to_thread(
            _docs_svc.document_get,
            store,
            project_id,
            agent_id,
            doc_id=doc_id,
            meta_only=bool(meta_only),
        )
        return _document_response(result)

    @app.delete("/v1/documents/{doc_id}", dependencies=[Depends(require_data_plane_auth)])
    async def _v1_documents_delete(request: Request, doc_id: str) -> JSONResponse:
        """Delete a document; its chunks cascade."""
        project_id = _require_project_id(request)
        _, agent_id, _, _ = _resolve_tenant_headers(request)
        store = _get_tenant_store_or_503(project_id, agent_id)

        from tapps_brain.services import document_service as _docs_svc

        result = await asyncio.to_thread(
            _docs_svc.document_delete,
            store,
            project_id,
            agent_id,
            doc_id=doc_id,
        )
        return _document_response(result)

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
            hops=max(1, min(_coerce_int(body, "hops", 1), 2)),
            limit=max(1, min(_coerce_int(body, "limit", 20), 200)),
            predicate_filter=str(body.get("predicate_filter") or "") or None,
        )
        return JSONResponse(status_code=200, content=result)

    @app.get("/snapshot/graph", dependencies=[Depends(require_data_plane_auth)])
    async def _snapshot_graph(
        request: Request,
        entity: str = Query(..., description="Focal entity UUID."),
        project: str | None = Query(
            None,
            description="Tenant id (required unless X-Project-Id header is set).",
        ),
        limit: int = Query(40, ge=1, le=200, description="Max neighbours to return."),
    ) -> JSONResponse:
        """Focus view of the knowledge graph around one entity (1-hop star).

        Powers the brain-visual KG panel: the focal entity plus its direct
        neighbours, with edge ``confidence`` / ``status`` / ``contradicted`` /
        ``stability`` (decay) / ``evidence_count`` so the panel can encode the
        signals that make the KG richer than a plain link graph. Expand-on-click
        re-roots on a neighbour.

        Query params: ``entity`` (UUID, required), ``project`` (required unless the
        ``X-Project-Id`` header is set), ``limit`` (default 40, max 200). The
        ``?project=`` query mirrors ``GET /snapshot`` so the dashboard can call it
        the same way; API clients may use the ``X-Project-Id`` header instead.
        Response: ``{root, nodes, edges, node_count, edge_count}``.
        """
        project_id = (project or request.headers.get("x-project-id") or "").strip()
        if not project_id:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "bad_request",
                    "detail": "project is required (query ?project= or X-Project-Id header).",
                },
            )
        entity_id = _validate_uuid_field(entity, "entity")

        cm = _get_kg_cm_or_503()
        from tapps_brain.services import kg_service as _kg_svc
        from tapps_brain.visual_snapshot import build_kg_graph

        result = await asyncio.to_thread(
            _kg_svc.get_neighbors,
            cm,
            project_id,
            _kg_brain_id(),
            entity_ids=[entity_id],
            hops=1,
            limit=limit,
        )
        graph = build_kg_graph(entity_id, result.get("neighbors", []))
        return JSONResponse(
            status_code=200,
            content=graph,
            headers={"Access-Control-Allow-Origin": "*"},
        )

    @app.get("/snapshot/graph/health", dependencies=[Depends(require_data_plane_auth)])
    async def _snapshot_graph_health(
        request: Request,
        project: str | None = Query(
            None,
            description="Tenant id (required unless X-Project-Id header is set).",
        ),
    ) -> JSONResponse:
        """KG-graph health for a project: orphan entities, stale/superseded and
        contradicted edge ratios — the graph rot the memory-store scorecard
        (freshness/staleness/GC) does not cover.

        Query param ``project`` (or ``X-Project-Id`` header) required. Response:
        ``{entities_active, orphan_entities, orphan_ratio, edges_*, stale_ratio,
        contradicted_ratio, status, recommendations}``.
        """
        project_id = (project or request.headers.get("x-project-id") or "").strip()
        if not project_id:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "bad_request",
                    "detail": "project is required (query ?project= or X-Project-Id header).",
                },
            )

        cm = _get_kg_cm_or_503()
        from tapps_brain.services import kg_service as _kg_svc
        from tapps_brain.visual_snapshot import build_kg_health

        counts = await asyncio.to_thread(_kg_svc.graph_health, cm, project_id, _kg_brain_id())
        return JSONResponse(
            status_code=200,
            content=build_kg_health(counts),
            headers={"Access-Control-Allow-Origin": "*"},
        )

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
            max_hops=max(1, min(_coerce_int(body, "max_hops", 3), ceiling)),
        )
        if isinstance(result, dict) and result.get("error"):
            status = 400 if result.get("error") == "bad_request" else 503
            raise HTTPException(status_code=status, detail=result)
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
        # X-Tapps-Agent wins over X-Agent-Id (same precedence as MCP / middleware).
        _, agent_id, _, _ = _resolve_tenant_headers(request)
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

        # Normalize to the MCP {recorded, edge_id, feedback_type, kg_update}
        # shape and map missing edges to 404 (audit may still have been written).
        kg_upd = result.get("kg_update") if isinstance(result, dict) else None
        if isinstance(kg_upd, dict) and kg_upd.get("reason") == "edge_not_found":
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "not_found",
                    "detail": f"KG edge not found: {edge_id}",
                    "edge_id": edge_id,
                },
            )
        content: dict[str, Any] = {
            "recorded": True,
            "edge_id": edge_id,
            "feedback_type": feedback_type,
        }
        if isinstance(result, dict):
            if "kg_update" in result:
                content["kg_update"] = result["kg_update"]
            if "event" in result:
                content["event"] = result["event"]
        return JSONResponse(status_code=200, content=content)

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

    def _open_registry() -> Any:
        """Return a ``ProjectRegistry`` bound to the shared registry pool.

        TAP-548-class fix: each admin request used to construct and tear down
        a full ``PostgresConnectionManager`` pool.  The pool is now the
        process-wide singleton shared with per-tenant auth
        (:func:`tapps_brain.http.auth.get_registry_cm`) and is closed once in
        lifespan shutdown.
        """
        if not cfg.dsn:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "db_unavailable",
                    "detail": "TAPPS_BRAIN_DATABASE_URL is not configured.",
                },
            )
        from tapps_brain.http.auth import get_registry_cm
        from tapps_brain.project_registry import ProjectRegistry

        return ProjectRegistry(get_registry_cm(cfg.dsn))

    @app.get("/admin/projects", dependencies=[Depends(require_admin_auth)])
    async def _admin_projects_list() -> JSONResponse:
        registry = _open_registry()
        rows = registry.list_all()
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

        registry = _open_registry()
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
        registry = _open_registry()
        record = registry.get(project_id)
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
        registry = _open_registry()
        updated = registry.approve(project_id)
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
        registry = _open_registry()
        deleted = registry.delete(project_id)
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
        registry = _open_registry()
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
        registry = _open_registry()
        revoked = registry.revoke_token(project_id)
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
        # TAP-2866: every 4xx/5xx must land in tapps_brain_http_errors_total —
        # before this only 422/500 were counted, so a data-plane endpoint
        # failing with 503 (idempotency/store/db unavailable) was invisible.
        if exc.status_code >= 400:
            _record_http_error(_request.url.path, exc.status_code)
        return JSONResponse(status_code=exc.status_code, content=body)

    # STORY-069.4: map ProjectNotRegisteredError → structured 403 so admin
    # routes that touch the registry report the same envelope as the
    # legacy handler.  Shape preserved for backward compat.
    @app.exception_handler(_ProjectNotRegisteredError)
    async def _pne_handler(_request: Request, exc: _ProjectNotRegisteredError) -> JSONResponse:
        _record_http_error(_request.url.path, 403)
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
        _record_http_error(_request.url.path, 503)
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
        _record_http_error(_request.url.path, 429)
        return JSONResponse(
            status_code=429,
            content=exc.http_body(retry_after=retry_after),
            headers={"Retry-After": str(retry_after)},
        )

    @app.exception_handler(_TaxonomyError)
    async def _taxonomy_handler(_request: Request, exc: _TaxonomyError) -> JSONResponse:
        """Catch-all for all remaining TaxonomyError subclasses."""
        _record_http_error(_request.url.path, exc.http_status)
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
