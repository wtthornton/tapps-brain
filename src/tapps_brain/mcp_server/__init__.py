"""MCP server exposing tapps-brain via Model Context Protocol.

Uses FastMCP to expose MemoryStore operations as MCP tools and resources
over the MCP Streamable HTTP transport (2025-03-26 spec). Requires the
``mcp`` optional extra.

This module is not a standalone entry point — it is mounted inside the
HTTP adapter at ``/mcp`` (port 8080) and as the operator MCP server on a
separate port (default 8090, bearer-token protected). All agents connect
via the deployed ``docker-tapps-brain-http`` container.

Key public API:
- :func:`create_server` — standard server (no operator tools).
- :func:`create_operator_server` — operator server (always enables operator tools).
- :func:`main` — entry point for ``tapps-brain-mcp`` (standard, safe for AGENT.md).
- :func:`main_operator` — entry point for ``tapps-brain-operator-mcp`` (operator).

EPIC-070 STORY-070.1: tool bodies have been extracted to
``tapps_brain.services.*``. Each ``@mcp.tool()`` here is a thin wrapper
that resolves the per-call store, delegates to the service function, and
serialises the result to JSON.

EPIC-070 STORY-070.9: operator-tool separation. The **standard** server
(``tapps-brain-mcp``) never exposes operator tools — even if
``TAPPS_BRAIN_OPERATOR_TOOLS=1`` is set. The **operator** server
(``tapps-brain-operator-mcp``) always exposes them.
"""

from __future__ import annotations

import argparse
import contextvars
import importlib.metadata
import json
import logging
import os
import sys
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

# STORY-070.4: transport-neutral contextvars set by the FastAPI tenant
# middleware (HTTP) or left unset for stdio (falls back to env/argv).
REQUEST_PROJECT_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "tapps_brain_request_project_id", default=None
)
REQUEST_AGENT_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "tapps_brain_request_agent_id", default=None
)
# STORY-070.7: per-call scope / group contextvars (set by HTTP middleware from
# ``X-Tapps-Scope`` / ``X-Tapps-Group`` headers, or left unset for stdio).
REQUEST_SCOPE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "tapps_brain_request_scope", default=None
)
REQUEST_GROUP: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "tapps_brain_request_group", default=None
)
# STORY-073.2: resolved MCP profile contextvar (set by ProfileResolutionMiddleware
# from the X-Brain-Profile header / agent-registry lookup / server default).
# STORY-073.3 reads this in list_tools and call_tool interceptors.
REQUEST_PROFILE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "tapps_brain_request_profile", default=None
)

import structlog

from tapps_brain.services import (
    agents_service,
    diagnostics_service,
    feedback_service,
    flywheel_service,
    hive_service,
    maintenance_service,
    memory_service,
    profile_service,
    relay_service,
)
from tapps_brain.services._common import parse_details_json as _mcp_parse_details_json  # noqa: F401

# Silence structlog for server mode — MCP uses stdin/stdout for protocol.
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL),
)

logger = structlog.get_logger(__name__)


def _lazy_import_mcp() -> Any:
    """Import ``mcp`` lazily so the module can be imported without the extra."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        sys.stderr.write(
            "ERROR: The 'mcp' package is required for the MCP server.\n"
            "Install it with: uv sync --extra mcp  (or --extra all)\n"
        )
        sys.exit(1)
    return FastMCP


def _resolve_project_dir(project_dir: str | None) -> Path:
    """Resolve project directory, defaulting to cwd."""
    return Path(project_dir).resolve() if project_dir else Path.cwd().resolve()


def _build_transport_security() -> Any:
    """Build TransportSecuritySettings from TAPPS_BRAIN_MCP_ALLOWED_HOSTS.

    When the env var is set (comma-separated host[:port] entries), DNS-rebinding
    protection is enabled with the explicit allow-list so Docker bridge / K8s
    service-DNS hostnames are accepted.  When unset, ``None`` is returned and
    FastMCP applies its own default (localhost-only guard when host=127.0.0.1).

    Example::

        TAPPS_BRAIN_MCP_ALLOWED_HOSTS=tapps-brain-http:8080,localhost:8080
    """
    raw = (os.environ.get("TAPPS_BRAIN_MCP_ALLOWED_HOSTS") or "").strip()
    if not raw:
        return None
    try:
        from mcp.server.transport_security import TransportSecuritySettings
    except ImportError:
        logger.warning(
            "mcp_server.transport_security_unavailable",
            detail="TransportSecuritySettings not found in installed mcp package; "
            "TAPPS_BRAIN_MCP_ALLOWED_HOSTS will be ignored.",
        )
        return None
    hosts = [h.strip() for h in raw.split(",") if h.strip()]
    origins = [f"http://{h}" for h in hosts]
    logger.info(
        "mcp_server.transport_security_configured",
        allowed_hosts=hosts,
    )
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=origins,
    )


def _get_store(
    project_dir: Path,
    *,
    enable_hive: bool = True,
    agent_id: str = "unknown",
) -> Any:
    """Open a MemoryStore for the given project directory.

    When *enable_hive* is ``True``, a Postgres :class:`HiveBackend` is
    wired in when ``TAPPS_BRAIN_HIVE_DSN`` is set (ADR-007 — no SQLite
    Hive).

    **Strict mode** (``TAPPS_BRAIN_STRICT=1``): When this env var is set,
    startup **fails immediately** if ``TAPPS_BRAIN_HIVE_DSN`` is not
    configured.  This prevents silent degradation in production where a
    missing DSN would quietly disable Hive tools.
    """
    from tapps_brain.backends import resolve_hive_backend_from_env
    from tapps_brain.store import MemoryStore

    strict = os.environ.get("TAPPS_BRAIN_STRICT", "") == "1"

    hive_store = None
    if enable_hive:
        hive_store = resolve_hive_backend_from_env()
        if strict and hive_store is None:
            raise RuntimeError(
                "TAPPS_BRAIN_STRICT=1 requires TAPPS_BRAIN_HIVE_DSN to be set (postgresql://...)"
            )

    agent_id_for_store = agent_id if agent_id != "unknown" else None
    return MemoryStore(
        project_dir,
        agent_id=agent_id_for_store,
        hive_store=hive_store,
        hive_agent_id=agent_id,
    )


# --------------------------------------------------------------------------
# STORY-069.3: Per-call project_id dispatch with a bounded LRU store cache.
# --------------------------------------------------------------------------

_DEFAULT_STORE_CACHE_SIZE = 16


class _StoreCache:
    """Bounded LRU cache of ``MemoryStore`` instances keyed by project_id."""

    def __init__(self, *, maxsize: int | None = None) -> None:
        if maxsize is None:
            try:
                maxsize = int(
                    os.environ.get("TAPPS_BRAIN_STORE_CACHE_SIZE", "") or _DEFAULT_STORE_CACHE_SIZE
                )
            except ValueError:
                maxsize = _DEFAULT_STORE_CACHE_SIZE
        self._maxsize = max(1, maxsize)
        self._entries: OrderedDict[str, Any] = OrderedDict()
        self._lock = threading.Lock()

    @property
    def maxsize(self) -> int:
        return self._maxsize

    def get_or_create(self, project_id: str, factory: Any) -> Any:
        with self._lock:
            if project_id in self._entries:
                self._entries.move_to_end(project_id)
                return self._entries[project_id]

        store = factory()

        evicted: list[Any] = []
        with self._lock:
            existing = self._entries.get(project_id)
            if existing is not None:
                self._entries.move_to_end(project_id)
                evicted.append(store)
                result = existing
            else:
                self._entries[project_id] = store
                self._entries.move_to_end(project_id)
                while len(self._entries) > self._maxsize:
                    _, old = self._entries.popitem(last=False)
                    evicted.append(old)
                result = store

        for victim in evicted:
            _safe_close_store(victim)
        return result

    def clear(self) -> None:
        with self._lock:
            victims = list(self._entries.values())
            self._entries.clear()
        for victim in victims:
            _safe_close_store(victim)

    def __contains__(self, project_id: str) -> bool:
        with self._lock:
            return project_id in self._entries

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


def _safe_close_store(store: Any) -> None:
    close = getattr(store, "close", None)
    if close is None:
        return
    try:
        close()
    except Exception:  # noqa: BLE001 — best-effort close; failure logged
        logger.debug("store_cache.close_failed", exc_info=True)


_STORE_CACHE = _StoreCache()


def _resolve_project_dir_for_id(project_id: str) -> Path:
    return Path.cwd().resolve()


def _get_store_for_project(
    project_id: str | None,
    *,
    default_store: Any,
    enable_hive: bool = True,
    agent_id: str = "unknown",
    call_agent_id: str | None = None,
) -> Any:
    """Resolve a ``MemoryStore`` for *project_id*, optionally scoped to a per-call agent.

    STORY-070.7 — when *call_agent_id* is supplied and differs from the
    server-level *agent_id*, the cache key becomes ``"<pid>\\x00<aid>"`` so
    pooled MCP connections can multiplex many agents without bleeding
    Hive / propagation identity across tool calls.
    """
    effective_agent_id = call_agent_id if call_agent_id else agent_id
    per_call_differs = bool(call_agent_id and call_agent_id != agent_id)

    if not project_id and not per_call_differs:
        return default_store

    if not project_id:
        project_id = getattr(default_store, "_tapps_project_id", "") or ""

    # Compound key when the per-call agent differs from the server default;
    # otherwise keep the historical bare-project_id key for cache compat.
    cache_key = f"{project_id}\x00{effective_agent_id}" if per_call_differs else project_id

    default_pid = getattr(default_store, "_tapps_project_id", None)
    if not per_call_differs and default_pid and project_id == default_pid:
        return default_store

    def _factory() -> Any:
        prev = os.environ.get("TAPPS_BRAIN_PROJECT")
        if project_id:
            os.environ["TAPPS_BRAIN_PROJECT"] = project_id
        try:
            target_dir = _resolve_project_dir_for_id(project_id) if project_id else Path.cwd()
            return _get_store(
                target_dir,
                enable_hive=enable_hive,
                agent_id=effective_agent_id,
            )
        finally:
            if prev is None:
                os.environ.pop("TAPPS_BRAIN_PROJECT", None)
            else:
                os.environ["TAPPS_BRAIN_PROJECT"] = prev

    return _STORE_CACHE.get_or_create(cache_key, _factory)


def _current_request_project_id() -> str | None:
    """Resolve the per-request project_id across transports.

    Precedence:
      1. ``REQUEST_PROJECT_ID`` contextvar (set by the FastAPI tenant
         middleware for Streamable HTTP — STORY-070.4).
      2. MCP JSON-RPC ``_meta.project_id`` on the active request context
         (legacy per-call override; also works over stdio).
      3. ``TAPPS_BRAIN_PROJECT`` environment variable (stdio transport
         set by the client's ``.mcp.json`` ``env``).
    """
    pid = REQUEST_PROJECT_ID.get()
    if pid:
        return str(pid).strip() or None
    try:
        from mcp.server.lowlevel.server import request_ctx
    except Exception:  # noqa: BLE001 — optional dependency detection
        request_ctx = None  # type: ignore[assignment]
    if request_ctx is not None:
        try:
            rc = request_ctx.get()
        except LookupError:
            rc = None
        if rc is not None:
            meta = getattr(rc, "meta", None)
            if meta is not None:
                mpid = getattr(meta, "project_id", None)
                if mpid is None:
                    extra = getattr(meta, "model_extra", None) or {}
                    mpid = extra.get("project_id")
                if mpid:
                    return str(mpid).strip() or None
    env_pid = (os.environ.get("TAPPS_BRAIN_PROJECT") or "").strip()
    return env_pid or None


def _current_request_agent_id() -> str | None:
    """Return the effective per-request agent_id.

    Precedence (STORY-070.7):
      1. :data:`REQUEST_AGENT_ID` contextvar (set by HTTP middleware from
         ``X-Agent-Id`` / ``X-Tapps-Agent``).
      2. MCP JSON-RPC ``_meta.agent_id`` on the active request context
         (mirrors ``_current_request_project_id``).
      3. ``None`` — caller falls back to the server-level default.
    """
    agent = REQUEST_AGENT_ID.get()
    if agent:
        val = str(agent).strip()
        if val:
            return val
    # Also check _meta.agent_id from the MCP JSON-RPC envelope so stdio
    # clients can multiplex agents without a header layer.
    try:
        from mcp.server.lowlevel.server import request_ctx
    except Exception:  # noqa: BLE001 — optional dependency detection
        return None
    try:
        rc = request_ctx.get()
    except LookupError:
        return None
    if rc is None:
        return None
    meta = getattr(rc, "meta", None)
    if meta is None:
        return None
    aid = getattr(meta, "agent_id", None)
    if aid is None:
        extra = getattr(meta, "model_extra", None) or {}
        aid = extra.get("agent_id")
    if not aid:
        return None
    return str(aid).strip() or None


def _current_request_scope() -> str | None:
    """Return the per-request scope contextvar value (STORY-070.7).

    Set by :class:`McpTenantMiddleware` from the ``X-Tapps-Scope`` header.
    """
    s = REQUEST_SCOPE.get()
    if not s:
        return None
    return str(s).strip() or None


def _current_request_group() -> str | None:
    """Return the per-request group contextvar value (STORY-070.7).

    Set by :class:`McpTenantMiddleware` from the ``X-Tapps-Group`` header.
    """
    g = REQUEST_GROUP.get()
    if not g:
        return None
    return str(g).strip() or None


def _resolve_per_call_agent_id(call_val: str, *, default: str) -> str:
    """Resolve the effective ``agent_id`` for a single MCP tool call.

    Precedence (STORY-070.7):
      1. *call_val* — explicit ``agent_id=`` parameter from the tool call.
      2. :func:`_current_request_agent_id` — contextvar (header) or
         ``_meta.agent_id`` from the MCP envelope.
      3. *default* — the server-level agent_id passed to :func:`create_server`.
    """
    v = (call_val or "").strip()
    if v:
        return v
    ctx = _current_request_agent_id()
    if ctx:
        return ctx
    return default


def _current_request_idempotency_key() -> str | None:
    """Return ``_meta.idempotency_key`` from the active MCP request context.

    When ``TAPPS_BRAIN_IDEMPOTENCY=1`` is set, the MCP client can pass an
    ``idempotency_key`` UUID inside the JSON-RPC ``_meta`` envelope to get
    duplicate-safe ``memory_save`` / ``memory_reinforce`` calls::

        {"method": "tools/call", "params": {
            "name": "memory_save",
            "arguments": {"key": "...", "value": "..."},
            "_meta": {"idempotency_key": "uuid-here"}
        }}
    """
    try:
        from mcp.server.lowlevel.server import request_ctx
    except Exception:  # noqa: BLE001 — optional dependency detection
        return None
    try:
        rc = request_ctx.get()
    except LookupError:
        return None
    if rc is None:
        return None
    meta = getattr(rc, "meta", None)
    if meta is None:
        return None
    ikey = getattr(meta, "idempotency_key", None)
    if ikey is None:
        extra = getattr(meta, "model_extra", None) or {}
        ikey = extra.get("idempotency_key")
    return str(ikey).strip() or None if ikey else None


def _raise_project_not_registered(project_id: str | None) -> None:
    """Raise an MCP error for an unregistered project_id.

    Uses the STORY-070.4 error taxonomy (code=-32002, error="project_not_registered").
    Wire shape is backward-compatible with EPIC-069.
    """
    from mcp.shared.exceptions import McpError
    from mcp.types import ErrorData

    from tapps_brain.errors import ErrorCode, jsonrpc_code, mcp_error_data

    err_code = ErrorCode.PROJECT_NOT_REGISTERED
    raise McpError(
        ErrorData(
            code=jsonrpc_code(err_code),
            message=err_code.value,
            data=mcp_error_data(err_code, err_code.value, project_id=project_id),
        )
    )


class _StoreProxy:
    """Per-call dispatch shim that looks like a ``MemoryStore``."""

    __slots__ = ("_agent_id", "_default_store", "_enable_hive")

    def __init__(
        self,
        default_store: Any,
        *,
        enable_hive: bool,
        agent_id: str,
    ) -> None:
        object.__setattr__(self, "_default_store", default_store)
        object.__setattr__(self, "_enable_hive", enable_hive)
        object.__setattr__(self, "_agent_id", agent_id)

    def _resolve(self) -> Any:
        pid = _current_request_project_id()
        try:
            return _get_store_for_project(
                pid,
                default_store=self._default_store,
                enable_hive=self._enable_hive,
                agent_id=self._agent_id,
            )
        except Exception as exc:  # noqa: BLE001 — MCP handler must not propagate to client
            from tapps_brain.project_registry import ProjectNotRegisteredError

            if isinstance(exc, ProjectNotRegisteredError):
                _raise_project_not_registered(exc.project_id)
            raise

    def __getattr__(self, name: str) -> Any:
        return getattr(self._resolve(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in self.__slots__:
            object.__setattr__(self, name, value)
        else:
            setattr(self._resolve(), name, value)

    @property  # type: ignore[misc]
    def __class__(self) -> type:
        try:
            resolved_class: type = self._resolve().__class__
            return resolved_class
        except Exception:  # noqa: BLE001 — MCP handler must not propagate to client
            return _StoreProxy


_MCP_INSTRUCTIONS = (
    "tapps-brain is a persistent cross-session memory system. "
    "Use memory tools to save, retrieve, search, and manage "
    "knowledge across coding sessions.\n\n"
    "## Hive (multi-agent memory sharing)\n\n"
    "When Hive is enabled, memories can be shared across agents "
    "using the `agent_scope` parameter on `memory_save`:\n\n"
    "- **private** (default): Only visible to the saving agent.\n"
    "- **domain**: Visible to all agents sharing the same profile.\n"
    "- **hive**: Visible to ALL agents in the Hive.\n"
    "- **group:<name>**: Hive namespace *name* for members of that group.\n\n"
    "Recall automatically merges local and Hive results."
)


def create_server(  # noqa: PLR0915
    project_dir: Path | None = None,
    *,
    enable_hive: bool = True,
    agent_id: str = "unknown",
    enable_operator_tools: bool = False,
) -> Any:
    """Create and configure a FastMCP server instance.

    See module docstring for behaviour notes; tool bodies live in
    ``tapps_brain.services.*`` (EPIC-070 STORY-070.1).
    """
    fastmcp_cls = _lazy_import_mcp()

    resolved_dir = _resolve_project_dir(str(project_dir) if project_dir else None)
    try:
        default_store = _get_store(resolved_dir, enable_hive=enable_hive, agent_id=agent_id)
    except Exception as exc:  # noqa: BLE001 — MCP handler must not propagate to client
        from tapps_brain.project_registry import ProjectNotRegisteredError

        if isinstance(exc, ProjectNotRegisteredError):
            from mcp.shared.exceptions import McpError
            from mcp.types import ErrorData

            from tapps_brain.errors import ErrorCode, jsonrpc_code, mcp_error_data

            err_code = ErrorCode.PROJECT_NOT_REGISTERED
            raise McpError(
                ErrorData(
                    code=jsonrpc_code(err_code),
                    message=err_code.value,
                    data=mcp_error_data(err_code, err_code.value, project_id=exc.project_id),
                )
            ) from exc
        raise

    store = _StoreProxy(
        default_store,
        enable_hive=enable_hive,
        agent_id=agent_id,
    )

    # STORY-070.7 — capture the server-level agent_id before defining tools
    # so tool functions can accept an ``agent_id`` parameter that shadows
    # the closure name without losing access to the default.
    _server_agent_id = agent_id

    def _resolve_store_for_call(call_agent_id: str = "") -> Any:
        """Return the ``MemoryStore`` appropriate for a per-call agent override.

        When the effective agent_id equals the server default, the
        :class:`_StoreProxy` is returned (preserving historical behaviour
        and project dispatch).  Otherwise a dedicated store keyed by
        ``(project_id, effective_agent_id)`` is produced via
        :func:`_get_store_for_project` so per-agent context (Hive
        propagation, logging, cache isolation) does not bleed across
        concurrent tool calls sharing one pooled MCP connection.
        """
        eff = _resolve_per_call_agent_id(call_agent_id, default=_server_agent_id)
        if eff == _server_agent_id:
            return store
        pid = _current_request_project_id()
        default_target = store._default_store
        try:
            return _get_store_for_project(
                pid,
                default_store=default_target,
                enable_hive=enable_hive,
                agent_id=_server_agent_id,
                call_agent_id=eff,
            )
        except Exception as exc:
            from tapps_brain.project_registry import ProjectNotRegisteredError

            if isinstance(exc, ProjectNotRegisteredError):
                _raise_project_not_registered(exc.project_id)
            raise

    def _hive_for_tools() -> tuple[Any, bool]:
        """Return ``(hive_backend, should_close)`` for Hive MCP tools (ADR-007)."""
        shared = getattr(store, "_hive_store", None)
        if shared is not None:
            return shared, False
        from tapps_brain.backends import resolve_hive_backend_from_env

        extra = resolve_hive_backend_from_env()
        if extra is None:
            raise RuntimeError(
                "Hive tools require TAPPS_BRAIN_HIVE_DSN (postgresql://...) "
                "or MemoryStore configured with a Hive backend (ADR-007)."
            )
        return extra, True

    def _pid() -> str:
        return _current_request_project_id() or ""

    def _require_operator_enabled() -> None:
        """Fail-closed guard invoked at the top of every operator tool.

        The primary defense against data-plane callers invoking operator
        tools is registry removal (see the ``_OPERATOR_TOOL_NAMES`` block
        below).  This runtime check is belt-and-suspenders for TAP-545:
        if a future regression (FastMCP API drift, mis-wired flag, etc.)
        leaves an operator tool callable on a non-operator server, the
        tool refuses to execute instead of running with data-plane auth.
        """
        if not getattr(mcp, "_tapps_operator_tools_enabled", False):
            raise RuntimeError(
                "operator tool invoked on non-operator server "
                "(operator-tool gate failed open — refusing to execute)"
            )

    # STORY-070.2: Modern MCP 2025-03-26 Streamable HTTP transport.
    #
    # - stateless_http: when True, no Mcp-Session-Id is issued and each POST
    #   to /mcp is self-contained (required for horizontal scaling).  Some
    #   MCP HTTP clients — notably Claude Code's VSCode extension
    #   (agent-sdk) — complete the initialize handshake but then never
    #   enumerate tools when no session id comes back.  The Python MCP SDK
    #   client (mcp.client.streamable_http) works fine either way.
    #   Default: False (stateful) for broad client compatibility; set
    #   TAPPS_BRAIN_STATELESS_HTTP=1 to opt in to stateless behaviour.
    # - json_response: when True, /mcp returns plain application/json bodies
    #   instead of text/event-stream frames so ordinary HTTP clients (curl,
    #   requests, httpx) work out of the box.  Kept on by default.
    #   Override with TAPPS_BRAIN_JSON_RESPONSE=0 to force SSE-only responses.
    def _env_flag(name: str, default: bool) -> bool:
        raw = os.environ.get(name)
        if raw is None:
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    stateless_http = _env_flag("TAPPS_BRAIN_STATELESS_HTTP", default=False)
    json_response = _env_flag("TAPPS_BRAIN_JSON_RESPONSE", default=True)

    mcp_kwargs: dict[str, Any] = {"instructions": _MCP_INSTRUCTIONS}
    transport_security = _build_transport_security()
    if transport_security is not None:
        mcp_kwargs["transport_security"] = transport_security
    try:
        mcp = fastmcp_cls(
            "tapps-brain",
            stateless_http=stateless_http,
            json_response=json_response,
            **mcp_kwargs,
        )
    except TypeError:
        # Older FastMCP builds (< 1.25 / < 3.2) that lack these kwargs.
        # Stdio path still works; Streamable HTTP mount will degrade to
        # whatever the installed mcp package supports.
        mcp_kwargs.pop("transport_security", None)
        mcp = fastmcp_cls("tapps-brain", **mcp_kwargs)

    # Health endpoint for Docker/compose health checks.  GET /health returns
    # 200 OK.  Probing /mcp directly requires Accept: text/event-stream per
    # the MCP 2025-03-26 spec and will return 406 from any plain HTTP client.
    @mcp.custom_route("/health", methods=["GET"])  # type: ignore[untyped-decorator]
    async def _mcp_health(request: Any) -> Any:
        from starlette.responses import PlainTextResponse

        return PlainTextResponse("ok")

    # The original ``instructions=`` block kept below is unused now that
    # mcp_kwargs carries it; left as a no-op assignment for diff hygiene.
    _unused = (
        "tapps-brain is a persistent cross-session memory system. "
        "Use memory tools to save, retrieve, search, and manage "
        "knowledge across coding sessions.\n\n"
        "## Hive (multi-agent memory sharing)\n\n"
        "When Hive is enabled, memories can be shared across agents "
        "using the `agent_scope` parameter on `memory_save`:\n\n"
        "- **private** (default): Only visible to the saving agent. "
        "Use for scratch notes, intermediate reasoning, and "
        "agent-specific context.\n"
        "- **domain**: Visible to all agents sharing the same memory "
        "profile (e.g., all 'repo-brain' agents). Use for conventions, "
        "patterns, and role-specific knowledge.\n"
        "- **hive**: Visible to ALL agents in the Hive regardless of "
        "profile. Use for cross-cutting facts: tech stack decisions, "
        "project architecture, API contracts, and team agreements.\n"
        "- **group:<name>**: Hive namespace *name* for members of that group "
        "(distinct from project-local `group` on saves). Requires Hive group "
        "membership.\n\n"
        "Recall automatically merges local and Hive results. Use "
        "`hive_status` to see registered agents and namespaces, "
        "`hive_search` to query the shared store directly, "
        "`hive_propagate` to manually share an existing local memory, "
        "and `hive_write_revision` / `hive_wait_write` to poll for new "
        "Hive memory writes (lightweight pub-sub)."
    )

    # ------------------------------------------------------------------
    # Simplified Agent Brain tools (EPIC-057)
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_remember(
        fact: str,
        tier: str = "procedural",
        share: bool = False,
        share_with: str = "",
        agent_id: str = "",
        temporal_sensitivity: str | None = None,
    ) -> str:
        """Save a memory to the agent's brain.

        Use tier='architectural' for lasting decisions, 'pattern' for conventions,
        'procedural' for how-to knowledge. Set share=True to share with all groups,
        or share_with='hive' for org-wide.  Pass ``agent_id`` to override the
        server-level default for this call (STORY-070.7).

        Pass ``temporal_sensitivity='high'`` for facts that change quickly (decays
        4x faster), ``'low'`` for stable facts (decays 4x slower), or omit for the
        tier default.
        """
        eff_aid = _resolve_per_call_agent_id(agent_id, default=_server_agent_id)
        s = _resolve_store_for_call(agent_id)
        return json.dumps(
            memory_service.brain_remember(
                s,
                _pid(),
                eff_aid,
                fact=fact,
                tier=tier,
                share=share,
                share_with=share_with,
                temporal_sensitivity=temporal_sensitivity,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_recall(query: str, max_results: int = 5, agent_id: str = "") -> str:
        """Recall memories matching a query.

        Pass ``agent_id`` to override the server-level default for this call
        (STORY-070.7).
        """
        eff_aid = _resolve_per_call_agent_id(agent_id, default=_server_agent_id)
        s = _resolve_store_for_call(agent_id)
        return json.dumps(
            memory_service.brain_recall(
                s,
                _pid(),
                eff_aid,
                query=query,
                max_results=max_results,
            ),
            default=str,
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_forget(key: str, agent_id: str = "") -> str:
        """Archive a memory by key. The memory is not permanently deleted."""
        eff_aid = _resolve_per_call_agent_id(agent_id, default=_server_agent_id)
        s = _resolve_store_for_call(agent_id)
        return json.dumps(memory_service.brain_forget(s, _pid(), eff_aid, key=key))

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_learn_success(
        task_description: str,
        task_id: str = "",
        agent_id: str = "",
    ) -> str:
        """Record a successful task outcome."""
        eff_aid = _resolve_per_call_agent_id(agent_id, default=_server_agent_id)
        s = _resolve_store_for_call(agent_id)
        return json.dumps(
            memory_service.brain_learn_success(
                s,
                _pid(),
                eff_aid,
                task_description=task_description,
                task_id=task_id,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_learn_failure(
        description: str,
        task_id: str = "",
        error: str = "",
        agent_id: str = "",
    ) -> str:
        """Record a failed task outcome to avoid repeating mistakes."""
        eff_aid = _resolve_per_call_agent_id(agent_id, default=_server_agent_id)
        s = _resolve_store_for_call(agent_id)
        return json.dumps(
            memory_service.brain_learn_failure(
                s,
                _pid(),
                eff_aid,
                description=description,
                task_id=task_id,
                error=error,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_status(agent_id: str = "") -> str:
        """Show agent identity, group memberships, store stats, and Hive connectivity.

        The response reflects the effective ``agent_id`` after STORY-070.7
        per-call resolution (call param > contextvar/``_meta`` > server default).
        """
        eff_aid = _resolve_per_call_agent_id(agent_id, default=_server_agent_id)
        s = _resolve_store_for_call(agent_id)
        return json.dumps(memory_service.brain_status(s, _pid(), eff_aid), default=str)

    # ------------------------------------------------------------------
    # Tools — model-controlled operations
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_save(
        key: str,
        value: str,
        tier: str = "pattern",
        source: str = "agent",
        tags: list[str] | None = None,
        scope: str = "project",
        confidence: float = -1.0,
        agent_scope: str = "private",
        source_agent: str = "",
        group: str | None = None,
        agent_id: str = "",
    ) -> str:
        """Save or update a memory entry.

        When ``TAPPS_BRAIN_IDEMPOTENCY=1``, pass ``_meta.idempotency_key`` (UUID)
        in the JSON-RPC envelope for duplicate-safe writes.

        Pass ``agent_id`` to override the server-level default for this
        call (STORY-070.7).
        """
        from tapps_brain.idempotency import IdempotencyStore, is_idempotency_enabled

        eff_aid = _resolve_per_call_agent_id(agent_id, default=_server_agent_id)
        s = _resolve_store_for_call(agent_id)

        ikey = _current_request_idempotency_key()
        project_id = _pid()
        dsn = os.environ.get("TAPPS_BRAIN_DATABASE_URL", "").strip()

        if ikey and is_idempotency_enabled() and dsn and project_id:
            istore = IdempotencyStore(dsn)
            try:
                cached = istore.check(project_id, ikey)
                if cached is not None:
                    _status, body = cached
                    return json.dumps(body)
            finally:
                istore.close()

        result = memory_service.memory_save(
            s,
            project_id,
            eff_aid,
            key=key,
            value=value,
            tier=tier,
            source=source,
            tags=tags,
            scope=scope,
            confidence=confidence,
            agent_scope=agent_scope,
            source_agent=source_agent,
            group=group,
        )

        if ikey and is_idempotency_enabled() and dsn and project_id:
            status_code = 400 if (isinstance(result, dict) and "error" in result) else 200
            istore2 = IdempotencyStore(dsn)
            try:
                istore2.save(project_id, ikey, status_code, result)
            finally:
                istore2.close()

        return json.dumps(result)

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_get(key: str, agent_id: str = "") -> str:
        """Retrieve a single memory entry by key."""
        eff_aid = _resolve_per_call_agent_id(agent_id, default=_server_agent_id)
        s = _resolve_store_for_call(agent_id)
        return json.dumps(memory_service.memory_get(s, _pid(), eff_aid, key=key))

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_delete(key: str, agent_id: str = "") -> str:
        """Delete a memory entry by key."""
        eff_aid = _resolve_per_call_agent_id(agent_id, default=_server_agent_id)
        s = _resolve_store_for_call(agent_id)
        return json.dumps(memory_service.memory_delete(s, _pid(), eff_aid, key=key))

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_search(
        query: str,
        tier: str | None = None,
        scope: str | None = None,
        as_of: str | None = None,
        group: str | None = None,
        since: str = "",
        until: str = "",
        time_field: str = "created_at",
        agent_id: str = "",
    ) -> str:
        """Search memory entries using full-text search."""
        eff_aid = _resolve_per_call_agent_id(agent_id, default=_server_agent_id)
        s = _resolve_store_for_call(agent_id)
        return json.dumps(
            memory_service.memory_search(
                s,
                _pid(),
                eff_aid,
                query=query,
                tier=tier,
                scope=scope,
                as_of=as_of,
                group=group,
                since=since,
                until=until,
                time_field=time_field,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_list(
        tier: str | None = None,
        scope: str | None = None,
        include_superseded: bool = False,
        group: str | None = None,
        agent_id: str = "",
    ) -> str:
        """List memory entries with optional filters."""
        eff_aid = _resolve_per_call_agent_id(agent_id, default=_server_agent_id)
        s = _resolve_store_for_call(agent_id)
        return json.dumps(
            memory_service.memory_list(
                s,
                _pid(),
                eff_aid,
                tier=tier,
                scope=scope,
                include_superseded=include_superseded,
                group=group,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_list_groups(agent_id: str = "") -> str:
        """List distinct project-local memory group names (GitHub #49)."""
        eff_aid = _resolve_per_call_agent_id(agent_id, default=_server_agent_id)
        s = _resolve_store_for_call(agent_id)
        return json.dumps(memory_service.memory_list_groups(s, _pid(), eff_aid))

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_recall(
        message: str,
        group: str | None = None,
        agent_id: str = "",
    ) -> str:
        """Run auto-recall for a message and return ranked memories."""
        eff_aid = _resolve_per_call_agent_id(agent_id, default=_server_agent_id)
        s = _resolve_store_for_call(agent_id)
        return json.dumps(
            memory_service.memory_recall(s, _pid(), eff_aid, message=message, group=group)
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_reinforce(
        key: str,
        confidence_boost: float = 0.0,
        agent_id: str = "",
    ) -> str:
        """Reinforce a memory entry, boosting its confidence and resetting decay.

        When ``TAPPS_BRAIN_IDEMPOTENCY=1``, pass ``_meta.idempotency_key`` (UUID)
        in the JSON-RPC envelope for duplicate-safe writes.

        Pass ``agent_id`` to override the server-level default (STORY-070.7).
        """
        from tapps_brain.idempotency import IdempotencyStore, is_idempotency_enabled

        eff_aid = _resolve_per_call_agent_id(agent_id, default=_server_agent_id)
        s = _resolve_store_for_call(agent_id)

        ikey = _current_request_idempotency_key()
        project_id = _pid()
        dsn = os.environ.get("TAPPS_BRAIN_DATABASE_URL", "").strip()

        if ikey and is_idempotency_enabled() and dsn and project_id:
            istore = IdempotencyStore(dsn)
            try:
                cached = istore.check(project_id, ikey)
                if cached is not None:
                    _status, body = cached
                    return json.dumps(body)
            finally:
                istore.close()

        result = memory_service.memory_reinforce(
            s,
            project_id,
            eff_aid,
            key=key,
            confidence_boost=confidence_boost,
        )

        if ikey and is_idempotency_enabled() and dsn and project_id:
            status_code = 400 if (isinstance(result, dict) and "error" in result) else 200
            istore2 = IdempotencyStore(dsn)
            try:
                istore2.save(project_id, ikey, status_code, result)
            finally:
                istore2.close()

        return json.dumps(result)

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_ingest(
        context: str,
        source: str = "agent",
        agent_scope: str = "private",
        agent_id: str = "",
    ) -> str:
        """Extract and store durable facts from conversation context."""
        eff_aid = _resolve_per_call_agent_id(agent_id, default=_server_agent_id)
        s = _resolve_store_for_call(agent_id)
        return json.dumps(
            memory_service.memory_ingest(
                s,
                _pid(),
                eff_aid,
                context=context,
                source=source,
                agent_scope=agent_scope,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_supersede(
        old_key: str,
        new_value: str,
        key: str | None = None,
        tier: str | None = None,
        tags: list[str] | None = None,
        agent_id: str = "",
    ) -> str:
        """Create a new version of a memory, superseding the old one."""
        eff_aid = _resolve_per_call_agent_id(agent_id, default=_server_agent_id)
        s = _resolve_store_for_call(agent_id)
        return json.dumps(
            memory_service.memory_supersede(
                s,
                _pid(),
                eff_aid,
                old_key=old_key,
                new_value=new_value,
                key=key,
                tier=tier,
                tags=tags,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_history(key: str, agent_id: str = "") -> str:
        """Show the full version chain for a memory key."""
        eff_aid = _resolve_per_call_agent_id(agent_id, default=_server_agent_id)
        s = _resolve_store_for_call(agent_id)
        return json.dumps(memory_service.memory_history(s, _pid(), eff_aid, key=key))

    # ------------------------------------------------------------------
    # Bulk tools (STORY-070.6)
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_save_many(
        entries: list[dict[str, str | float | list[str] | None]],
        agent_id: str = "",
    ) -> str:
        """Save multiple memory entries in a single call.

        Each entry must be a dict with at least ``key`` and ``value``.  Optional
        fields: ``tier``, ``source``, ``tags``, ``scope``, ``confidence``,
        ``agent_scope``, ``group``.

        Batch size is capped by ``TAPPS_BRAIN_MAX_BATCH_SIZE`` (default 100).

        Returns::

            {
                "results": [<per-item save result>, ...],
                "saved_count": int,
                "error_count": int,
            }
        """
        eff_aid = _resolve_per_call_agent_id(agent_id, default=_server_agent_id)
        s = _resolve_store_for_call(agent_id)
        return json.dumps(
            memory_service.memory_save_many(
                s,
                _pid(),
                eff_aid,
                entries=list(entries),
            ),
            default=str,
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_recall_many(queries: list[str], agent_id: str = "") -> str:
        """Run recall against multiple queries in a single call.

        Each query is a plain string message.  Batch size is capped by
        ``TAPPS_BRAIN_MAX_BATCH_SIZE`` (default 50 reads).

        Returns::

            {
                "results": [<per-query recall result>, ...],
                "query_count": int,
            }
        """
        eff_aid = _resolve_per_call_agent_id(agent_id, default=_server_agent_id)
        s = _resolve_store_for_call(agent_id)
        return json.dumps(
            memory_service.memory_recall_many(
                s,
                _pid(),
                eff_aid,
                queries=list(queries),
            ),
            default=str,
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_reinforce_many(
        entries: list[dict[str, str | float]],
        agent_id: str = "",
    ) -> str:
        """Reinforce multiple memory entries in a single call.

        Each entry must be a dict with at least ``key``.  Optional field:
        ``confidence_boost`` (float, default 0.0).

        Batch size is capped by ``TAPPS_BRAIN_MAX_BATCH_SIZE`` (default 100).

        Returns::

            {
                "results": [<per-item reinforce result>, ...],
                "reinforced_count": int,
                "error_count": int,
            }
        """
        eff_aid = _resolve_per_call_agent_id(agent_id, default=_server_agent_id)
        s = _resolve_store_for_call(agent_id)
        return json.dumps(
            memory_service.memory_reinforce_many(
                s,
                _pid(),
                eff_aid,
                entries=list(entries),
            ),
            default=str,
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_index_session(
        session_id: str,
        chunks: list[str],
        agent_id: str = "",
    ) -> str:
        """Index session chunks for future search."""
        eff_aid = _resolve_per_call_agent_id(agent_id, default=_server_agent_id)
        s = _resolve_store_for_call(agent_id)
        return json.dumps(
            memory_service.memory_index_session(
                s,
                _pid(),
                eff_aid,
                session_id=session_id,
                chunks=chunks,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_search_sessions(
        query: str,
        limit: int = 10,
        agent_id: str = "",
    ) -> str:
        """Search past session summaries."""
        eff_aid = _resolve_per_call_agent_id(agent_id, default=_server_agent_id)
        s = _resolve_store_for_call(agent_id)
        return json.dumps(
            memory_service.memory_search_sessions(
                s,
                _pid(),
                eff_aid,
                query=query,
                limit=limit,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_capture(
        response: str,
        source: str = "agent",
        agent_scope: str = "private",
        agent_id: str = "",
    ) -> str:
        """Extract and persist new facts from an agent response."""
        eff_aid = _resolve_per_call_agent_id(agent_id, default=_server_agent_id)
        s = _resolve_store_for_call(agent_id)
        return json.dumps(
            memory_service.memory_capture(
                s,
                _pid(),
                eff_aid,
                response=response,
                source=source,
                agent_scope=agent_scope,
            )
        )

    # ------------------------------------------------------------------
    # Feedback tools (EPIC-029 / STORY-029.4)
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def feedback_rate(
        entry_key: str,
        rating: str = "helpful",
        session_id: str = "",
        details_json: str = "",
    ) -> str:
        """Rate a recalled memory entry (creates ``recall_rated`` event)."""
        return json.dumps(
            feedback_service.feedback_rate(
                store,
                _pid(),
                agent_id,
                entry_key=entry_key,
                rating=rating,
                session_id=session_id,
                details_json=details_json,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def feedback_gap(
        query: str,
        session_id: str = "",
        details_json: str = "",
    ) -> str:
        """Report a knowledge gap (``gap_reported`` event)."""
        return json.dumps(
            feedback_service.feedback_gap(
                store,
                _pid(),
                agent_id,
                query=query,
                session_id=session_id,
                details_json=details_json,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def feedback_issue(
        entry_key: str,
        issue: str,
        session_id: str = "",
        details_json: str = "",
    ) -> str:
        """Flag a quality issue with a memory entry (``issue_flagged``)."""
        return json.dumps(
            feedback_service.feedback_issue(
                store,
                _pid(),
                agent_id,
                entry_key=entry_key,
                issue=issue,
                session_id=session_id,
                details_json=details_json,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def feedback_record(
        event_type: str,
        entry_key: str = "",
        session_id: str = "",
        utility_score: float | None = None,
        details_json: str = "",
    ) -> str:
        """Record a generic feedback event (built-in or custom type)."""
        return json.dumps(
            feedback_service.feedback_record(
                store,
                _pid(),
                agent_id,
                event_type=event_type,
                entry_key=entry_key,
                session_id=session_id,
                utility_score=utility_score,
                details_json=details_json,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def feedback_query(
        event_type: str = "",
        entry_key: str = "",
        session_id: str = "",
        since: str = "",
        until: str = "",
        limit: int = 100,
    ) -> str:
        """Query recorded feedback events with optional filters."""
        return json.dumps(
            feedback_service.feedback_query(
                store,
                _pid(),
                agent_id,
                event_type=event_type,
                entry_key=entry_key,
                session_id=session_id,
                since=since,
                until=until,
                limit=limit,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def diagnostics_report(
        record_history: bool = True,
    ) -> str:
        """Run quality diagnostics (EPIC-030): composite score, dimensions, circuit state."""
        return json.dumps(
            diagnostics_service.diagnostics_report(
                store,
                _pid(),
                agent_id,
                record_history=record_history,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def diagnostics_history(
        limit: int = 50,
    ) -> str:
        """Return recent persisted diagnostics snapshots."""
        return json.dumps(
            diagnostics_service.diagnostics_history(
                store,
                _pid(),
                agent_id,
                limit=limit,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def flywheel_process(since: str = "") -> str:
        """Run feedback → confidence pipeline (EPIC-031)."""
        return json.dumps(flywheel_service.flywheel_process(store, _pid(), agent_id, since=since))

    @mcp.tool()  # type: ignore[untyped-decorator]
    def flywheel_gaps(limit: int = 10, semantic: bool = False) -> str:
        """Return top knowledge gaps as JSON."""
        return json.dumps(
            flywheel_service.flywheel_gaps(
                store,
                _pid(),
                agent_id,
                limit=limit,
                semantic=semantic,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def flywheel_report(period_days: int = 7) -> str:
        """Generate quality report (markdown + structured summary)."""
        return json.dumps(
            flywheel_service.flywheel_report(
                store,
                _pid(),
                agent_id,
                period_days=period_days,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def flywheel_evaluate(suite_path: str, k: int = 5) -> str:
        """Run BEIR-format directory or YAML suite evaluation."""
        _require_operator_enabled()
        return json.dumps(
            flywheel_service.flywheel_evaluate(
                store,
                _pid(),
                agent_id,
                suite_path=suite_path,
                k=k,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def flywheel_hive_feedback(threshold: int = 3) -> str:
        """Aggregate / apply Hive cross-project feedback penalties."""
        _require_operator_enabled()
        return json.dumps(
            flywheel_service.flywheel_hive_feedback(
                store,
                _pid(),
                agent_id,
                threshold=threshold,
            )
        )

    # ------------------------------------------------------------------
    # Resources — read-only store views
    # ------------------------------------------------------------------

    @mcp.resource("memory://stats")  # type: ignore[untyped-decorator]
    def stats_resource() -> str:
        """Store stats: counts, tiers, schema, package/profile, optional profile_seed_version."""
        snap = store.snapshot()
        schema_ver = store.get_schema_version()
        h = store.health()
        return json.dumps(
            {
                "project_root": str(snap.project_root),
                "total_entries": snap.total_count,
                "max_entries": store._max_entries,
                "max_entries_per_group": store._max_entries_per_group,
                "schema_version": schema_ver,
                "package_version": h.package_version,
                "profile_name": h.profile_name,
                "profile_seed_version": h.profile_seed_version,
                "tier_distribution": snap.tier_counts,
            }
        )

    @mcp.resource("memory://agent-contract")  # type: ignore[untyped-decorator]
    def agent_contract_resource() -> str:
        """Agent integration snapshot: versions, profile, tiers, recall empty codes."""
        from tapps_brain.models import MemoryTier
        from tapps_brain.recall_diagnostics import (
            RECALL_EMPTY_BELOW_SCORE_THRESHOLD,
            RECALL_EMPTY_ENGAGEMENT_LOW,
            RECALL_EMPTY_GROUP_EMPTY,
            RECALL_EMPTY_NO_RANKED_MATCHES,
            RECALL_EMPTY_POST_FILTER,
            RECALL_EMPTY_RAG_BLOCKED,
            RECALL_EMPTY_SEARCH_FAILED,
            RECALL_EMPTY_STORE_EMPTY,
        )

        h = store.health()
        prof = store.profile
        layers = list(prof.layer_names) if prof is not None else []
        return json.dumps(
            {
                "package_version": h.package_version,
                "schema_version": h.schema_version,
                "profile_name": h.profile_name,
                "profile_layer_names": layers,
                "canonical_memory_tiers": [m.value for m in MemoryTier],
                "recall_empty_reason_codes": sorted(
                    {
                        RECALL_EMPTY_ENGAGEMENT_LOW,
                        RECALL_EMPTY_SEARCH_FAILED,
                        RECALL_EMPTY_STORE_EMPTY,
                        RECALL_EMPTY_GROUP_EMPTY,
                        RECALL_EMPTY_NO_RANKED_MATCHES,
                        RECALL_EMPTY_BELOW_SCORE_THRESHOLD,
                        RECALL_EMPTY_RAG_BLOCKED,
                        RECALL_EMPTY_POST_FILTER,
                    }
                ),
                "write_path_mcp": "memory_save",
                "write_path_cli": "tapps-brain memory save KEY VALUE [options]",
                "read_paths_mcp": ["memory_search", "memory_recall", "memory_list", "memory_get"],
                "operator_docs": "https://github.com/wtthornton/tapps-brain/tree/main/docs/guides",
            },
            indent=2,
        )

    @mcp.resource("memory://health")  # type: ignore[untyped-decorator]
    def health_resource() -> str:
        """Store health report."""
        report = store.health()
        return json.dumps(report.model_dump(mode="json"))

    @mcp.resource("memory://entries/{key}")  # type: ignore[untyped-decorator]
    def entry_resource(key: str) -> str:
        """Full detail view of a single memory entry."""
        entry = store.get(key)
        if entry is None:
            return json.dumps({"error": "not_found", "key": key})
        return json.dumps(entry.model_dump(mode="json"))

    @mcp.resource("memory://metrics")  # type: ignore[untyped-decorator]
    def metrics_resource() -> str:
        """Operation metrics: counters and latency histograms."""
        snapshot = store.get_metrics()
        return json.dumps(snapshot.to_dict())

    @mcp.resource("memory://feedback")  # type: ignore[untyped-decorator]
    def feedback_resource() -> str:
        """Recent feedback events (up to 500), newest-friendly order by query default."""
        events = store.query_feedback(limit=500)
        return json.dumps(
            {
                "events": [e.model_dump(mode="json") for e in events],
                "count": len(events),
            }
        )

    @mcp.resource("memory://diagnostics")  # type: ignore[untyped-decorator]
    def diagnostics_resource() -> str:
        """Latest diagnostics report (does not append history by default)."""
        rep = store.diagnostics(record_history=False)
        return json.dumps(rep.model_dump(mode="json"))

    @mcp.resource("memory://report")  # type: ignore[untyped-decorator]
    def report_resource() -> str:
        """Latest flywheel quality report summary (from last ``generate_report``)."""
        latest = store.latest_quality_report()
        if latest is None:
            rep = store.generate_report(period_days=7)
            payload = rep.structured_data
        else:
            payload = latest.get("structured_data", latest)
        return json.dumps(payload)

    # ------------------------------------------------------------------
    # Prompts — user-invoked workflow templates (STORY-008.6)
    # ------------------------------------------------------------------

    @mcp.prompt()  # type: ignore[untyped-decorator]
    def recall(topic: str) -> list[dict[str, str]]:
        """What do you remember about a topic?"""
        result = store.recall(topic)
        if result.memory_count == 0:
            body = f"No memories found about: {topic}"
        else:
            body = (
                f'Here are {result.memory_count} memories about "{topic}":\n\n'
                f"{result.memory_section}"
            )
        return [{"role": "user", "content": body}]

    @mcp.prompt()  # type: ignore[untyped-decorator]
    def store_summary() -> list[dict[str, str]]:
        """Generate a summary of what's in the memory store."""
        snap = store.snapshot()
        schema_ver = store.get_schema_version()
        entries = store.list_all()

        lines = [
            f"Memory store summary for: {snap.project_root}",
            f"Total entries: {snap.total_count} / 500",
            f"Schema version: {schema_ver}",
            f"Tier distribution: {json.dumps(snap.tier_counts)}",
            "",
        ]
        preview_len = 80
        if entries:
            lines.append("Recent entries (up to 10):")
            for entry in entries[:10]:
                truncated = entry.value[:preview_len]
                suffix = "…" if len(entry.value) > preview_len else ""
                lines.append(f"  - [{entry.tier!s}] {entry.key}: {truncated}{suffix}")
        else:
            lines.append("The store is empty.")

        return [{"role": "user", "content": "\n".join(lines)}]

    @mcp.prompt()  # type: ignore[untyped-decorator]
    def remember(fact: str) -> list[dict[str, str]]:
        """Remember a fact by saving it to the memory store."""
        body = (
            f"The user wants you to remember the following:\n\n"
            f'"{fact}"\n\n'
            "Please save this to the memory store using the memory_save tool. "
            "Choose an appropriate:\n"
            "- **key**: a short, descriptive kebab-case identifier\n"
            "- **tier**: one of architectural (system-level decisions), "
            "pattern (coding patterns/conventions), "
            "procedural (workflows/processes), "
            "or context (session-specific facts)\n"
            "- **tags**: relevant category tags\n"
            "- **confidence**: 0.7-0.9 for stated facts, 0.5-0.7 for inferences\n\n"
            "Confirm what you saved back to the user."
        )
        return [{"role": "user", "content": body}]

    # ------------------------------------------------------------------
    # Maintenance tools (STORY-008.5)
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def maintenance_consolidate(
        threshold: float = 0.7,
        min_group_size: int = 3,
        force: bool = True,
    ) -> str:
        """Trigger memory consolidation to merge similar entries."""
        _require_operator_enabled()
        return json.dumps(
            maintenance_service.maintenance_consolidate(
                store,
                _pid(),
                agent_id,
                project_root=resolved_dir,
                threshold=threshold,
                min_group_size=min_group_size,
                force=force,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def maintenance_gc(dry_run: bool = False) -> str:
        """Run garbage collection to archive stale memories."""
        _require_operator_enabled()
        return json.dumps(
            maintenance_service.maintenance_gc(store, _pid(), agent_id, dry_run=dry_run)
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def maintenance_stale() -> str:
        """List GC stale memory candidates with reasons (read-only; GitHub #21)."""
        _require_operator_enabled()
        return json.dumps(maintenance_service.maintenance_stale(store, _pid(), agent_id))

    @mcp.tool()  # type: ignore[untyped-decorator]
    def tapps_brain_health(check_hive: bool = True) -> str:
        """Return a structured health report for tapps-brain (issue #15)."""
        _require_operator_enabled()
        return json.dumps(
            diagnostics_service.tapps_brain_health(
                store,
                _pid(),
                agent_id,
                check_hive=check_hive,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_gc_config() -> str:
        """Return the current garbage collection configuration."""
        _require_operator_enabled()
        return json.dumps(memory_service.memory_gc_config(store, _pid(), agent_id))

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_gc_config_set(
        floor_retention_days: int | None = None,
        session_expiry_days: int | None = None,
        contradicted_threshold: float | None = None,
    ) -> str:
        """Update garbage collection configuration thresholds."""
        _require_operator_enabled()
        return json.dumps(
            memory_service.memory_gc_config_set(
                store,
                _pid(),
                agent_id,
                floor_retention_days=floor_retention_days,
                session_expiry_days=session_expiry_days,
                contradicted_threshold=contradicted_threshold,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_consolidation_config() -> str:
        """Return the current auto-consolidation configuration."""
        _require_operator_enabled()
        return json.dumps(memory_service.memory_consolidation_config(store, _pid(), agent_id))

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_consolidation_config_set(
        enabled: bool | None = None,
        threshold: float | None = None,
        min_entries: int | None = None,
    ) -> str:
        """Update auto-consolidation configuration."""
        _require_operator_enabled()
        return json.dumps(
            memory_service.memory_consolidation_config_set(
                store,
                _pid(),
                agent_id,
                enabled=enabled,
                threshold=threshold,
                min_entries=min_entries,
            )
        )

    # ------------------------------------------------------------------
    # Export / Import tools (STORY-008.5)
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_export(
        tier: str | None = None,
        scope: str | None = None,
        min_confidence: float | None = None,
    ) -> str:
        """Export memory entries as JSON."""
        _require_operator_enabled()
        return json.dumps(
            memory_service.memory_export(
                store,
                _pid(),
                agent_id,
                project_root=str(resolved_dir),
                tier=tier,
                scope=scope,
                min_confidence=min_confidence,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_import(
        memories_json: str,
        overwrite: bool = False,
    ) -> str:
        """Import memory entries from a JSON string."""
        _require_operator_enabled()
        return json.dumps(
            memory_service.memory_import(
                store,
                _pid(),
                agent_id,
                memories_json=memories_json,
                overwrite=overwrite,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def tapps_brain_relay_export(source_agent: str, items_json: str) -> str:
        """Build a memory relay JSON payload for cross-node handoff (GitHub #19)."""
        _require_operator_enabled()
        return json.dumps(
            relay_service.tapps_brain_relay_export(
                store,
                _pid(),
                agent_id,
                source_agent=source_agent,
                items_json=items_json,
            )
        )

    # ------------------------------------------------------------------
    # Profile tools (EPIC-010)
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def profile_info() -> str:
        """Return the active profile name, layers, and scoring config."""
        return json.dumps(profile_service.profile_info(store, _pid(), agent_id))

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_profile_onboarding() -> str:
        """Return Markdown onboarding guidance for the active memory profile (GitHub #45)."""
        return json.dumps(profile_service.memory_profile_onboarding(store, _pid(), agent_id))

    @mcp.tool()  # type: ignore[untyped-decorator]
    def profile_switch(name: str) -> str:
        """Switch to a different built-in memory profile."""
        return json.dumps(profile_service.profile_switch(store, _pid(), agent_id, name=name))

    # ------------------------------------------------------------------
    # Hive tools (EPIC-011)
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def hive_status() -> str:
        """Return Hive status: namespaces, entry counts, and registered agents."""
        return json.dumps(
            hive_service.hive_status(
                store,
                _pid(),
                agent_id,
                hive_resolver=_hive_for_tools,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def hive_search(query: str, namespace: str | None = None) -> str:
        """Search the shared Hive for memories from other agents."""
        return json.dumps(
            hive_service.hive_search(
                store,
                _pid(),
                agent_id,
                hive_resolver=_hive_for_tools,
                query=query,
                namespace=namespace,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def hive_propagate(
        key: str,
        agent_scope: str = "hive",
        force: bool = False,
        dry_run: bool = False,
    ) -> str:
        """Manually propagate a local memory to the Hive shared store."""
        return json.dumps(
            hive_service.hive_propagate(
                store,
                _pid(),
                agent_id,
                hive_resolver=_hive_for_tools,
                key=key,
                agent_scope=agent_scope,
                force=force,
                dry_run=dry_run,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def hive_push(
        agent_scope: str = "hive",
        push_all: bool = False,
        tags: str = "",
        tier: str | None = None,
        keys: str = "",
        dry_run: bool = False,
        force: bool = False,
    ) -> str:
        """Batch-promote local project memories to the Hive (GitHub #18)."""
        return json.dumps(
            hive_service.hive_push(
                store,
                _pid(),
                agent_id,
                hive_resolver=_hive_for_tools,
                agent_scope=agent_scope,
                push_all=push_all,
                tags=tags,
                tier=tier,
                keys=keys,
                dry_run=dry_run,
                force=force,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def hive_write_revision() -> str:
        """Return the Hive write notification revision (GitHub #12)."""
        return json.dumps(
            hive_service.hive_write_revision(
                store,
                _pid(),
                agent_id,
                hive_resolver=_hive_for_tools,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def hive_wait_write(since_revision: int = 0, timeout_seconds: float = 10.0) -> str:
        """Wait until the Hive write revision exceeds *since_revision* or timeout."""
        return json.dumps(
            hive_service.hive_wait_write(
                store,
                _pid(),
                agent_id,
                hive_resolver=_hive_for_tools,
                since_revision=since_revision,
                timeout_seconds=timeout_seconds,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def agent_register(
        agent_id: str,
        profile: str = "repo-brain",
        skills: str = "",
    ) -> str:
        """Register an agent in the Hive."""
        return json.dumps(
            agents_service.agent_register(
                store,
                _pid(),
                agent_id,
                new_agent_id=agent_id,
                profile=profile,
                skills=skills,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def agent_create(
        agent_id: str,
        profile: str = "repo-brain",
        skills: str = "",
    ) -> str:
        """Create an agent: register in the Hive with a validated profile."""
        return json.dumps(
            agents_service.agent_create(
                store,
                _pid(),
                agent_id,
                new_agent_id=agent_id,
                profile=profile,
                skills=skills,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def agent_list() -> str:
        """List all registered agents in the Hive."""
        return json.dumps(agents_service.agent_list(store, _pid(), agent_id))

    @mcp.tool()  # type: ignore[untyped-decorator]
    def agent_delete(agent_id: str) -> str:
        """Delete a registered agent from the Hive."""
        return json.dumps(
            agents_service.agent_delete(
                store,
                _pid(),
                agent_id,
                target_agent_id=agent_id,
            )
        )

    # ------------------------------------------------------------------
    # Knowledge graph tools (EPIC-015)
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_relations(key: str, agent_id: str = "") -> str:
        """Return all relations associated with a memory entry key."""
        eff_aid = _resolve_per_call_agent_id(agent_id, default=_server_agent_id)
        s = _resolve_store_for_call(agent_id)
        return json.dumps(memory_service.memory_relations(s, _pid(), eff_aid, key=key))

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_relations_get_batch(keys_json: str, agent_id: str = "") -> str:
        """Return relations for multiple memory keys in one call (STORY-048.2)."""
        eff_aid = _resolve_per_call_agent_id(agent_id, default=_server_agent_id)
        s = _resolve_store_for_call(agent_id)
        return json.dumps(
            memory_service.memory_relations_get_batch(
                s,
                _pid(),
                eff_aid,
                keys_json=keys_json,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_find_related(key: str, max_hops: int = 2, agent_id: str = "") -> str:
        """Find entries related to a key via BFS traversal of the relation graph."""
        eff_aid = _resolve_per_call_agent_id(agent_id, default=_server_agent_id)
        s = _resolve_store_for_call(agent_id)
        return json.dumps(
            memory_service.memory_find_related(
                s,
                _pid(),
                eff_aid,
                key=key,
                max_hops=max_hops,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_query_relations(
        subject: str = "",
        predicate: str = "",
        object_entity: str = "",
        agent_id: str = "",
    ) -> str:
        """Filter relations by subject, predicate, and/or object_entity."""
        eff_aid = _resolve_per_call_agent_id(agent_id, default=_server_agent_id)
        s = _resolve_store_for_call(agent_id)
        return json.dumps(
            memory_service.memory_query_relations(
                s,
                _pid(),
                eff_aid,
                subject=subject,
                predicate=predicate,
                object_entity=object_entity,
            )
        )

    # ------------------------------------------------------------------
    # Audit trail tools (EPIC-015)
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_audit(
        key: str = "",
        event_type: str = "",
        since: str = "",
        until: str = "",
        limit: int = 50,
        agent_id: str = "",
    ) -> str:
        """Query the audit trail for memory events."""
        eff_aid = _resolve_per_call_agent_id(agent_id, default=_server_agent_id)
        s = _resolve_store_for_call(agent_id)
        return json.dumps(
            memory_service.memory_audit(
                s,
                _pid(),
                eff_aid,
                key=key,
                event_type=event_type,
                since=since,
                until=until,
                limit=limit,
            )
        )

    # ------------------------------------------------------------------
    # Tag management tools (EPIC-015)
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_list_tags(agent_id: str = "") -> str:
        """List all tags used in the memory store with their usage counts."""
        eff_aid = _resolve_per_call_agent_id(agent_id, default=_server_agent_id)
        s = _resolve_store_for_call(agent_id)
        return json.dumps(memory_service.memory_list_tags(s, _pid(), eff_aid))

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_update_tags(
        key: str,
        add: list[str] | None = None,
        remove: list[str] | None = None,
        agent_id: str = "",
    ) -> str:
        """Atomically add and/or remove tags on an existing memory entry."""
        eff_aid = _resolve_per_call_agent_id(agent_id, default=_server_agent_id)
        s = _resolve_store_for_call(agent_id)
        return json.dumps(
            memory_service.memory_update_tags(
                s,
                _pid(),
                eff_aid,
                key=key,
                add=add,
                remove=remove,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_entries_by_tag(
        tag: str,
        tier: str = "",
        agent_id: str = "",
    ) -> str:
        """Return all memory entries that carry a specific tag."""
        eff_aid = _resolve_per_call_agent_id(agent_id, default=_server_agent_id)
        s = _resolve_store_for_call(agent_id)
        return json.dumps(
            memory_service.memory_entries_by_tag(
                s,
                _pid(),
                eff_aid,
                tag=tag,
                tier=tier,
            )
        )

    # ------------------------------------------------------------------
    # Session end tool (Issue #17 — episodic memory capture)
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def tapps_brain_session_end(
        summary: str,
        tags: list[str] | None = None,
        daily_note: bool = False,
    ) -> str:
        """Record an end-of-session episodic memory entry."""
        return json.dumps(
            maintenance_service.tapps_brain_session_end(
                store,
                _pid(),
                agent_id,
                project_root=resolved_dir,
                summary=summary,
                tags=tags,
                daily_note=daily_note,
            ),
            default=str,
        )

    # ------------------------------------------------------------------
    # Profile registry (EPIC-073 STORY-073.1)
    # Validate YAML tool names against the full registered set *before* the
    # operator-tool removal pass so the ``operator`` profile (which references
    # all 68 tools) passes validation even when enable_operator_tools=False.
    # ------------------------------------------------------------------
    from tapps_brain.mcp_server.profile_registry import ProfileRegistry

    _profile_registry = ProfileRegistry()
    _all_registered = frozenset(t.name for t in mcp._tool_manager.list_tools())
    _profile_registry.validate_against(_all_registered)

    # ------------------------------------------------------------------
    # Operator tool gate
    # ------------------------------------------------------------------

    _OPERATOR_TOOL_NAMES: frozenset[str] = frozenset(
        {
            "maintenance_consolidate",
            "maintenance_gc",
            "maintenance_stale",
            "tapps_brain_health",
            "memory_gc_config",
            "memory_gc_config_set",
            "memory_consolidation_config",
            "memory_consolidation_config_set",
            "memory_export",
            "memory_import",
            "tapps_brain_relay_export",
            "flywheel_evaluate",
            "flywheel_hive_feedback",
        }
    )

    if not enable_operator_tools:
        # Fail closed: if ``remove_tool`` raises (e.g. FastMCP API drift
        # renamed or removed ``_tool_manager.remove_tool``), propagate so
        # the server refuses to start.  Previously this path swallowed
        # every exception and silently left operator tools callable by
        # data-plane holders of the standard token (TAP-545).
        for _op_tool in _OPERATOR_TOOL_NAMES:
            mcp._tool_manager.remove_tool(_op_tool)
        # Belt-and-suspenders: verify the registry no longer lists any
        # operator tool names, guarding against any future removal-logic
        # regression that completes without raising but leaves tools
        # behind.
        _remaining_ops = {t.name for t in mcp._tool_manager.list_tools()} & _OPERATOR_TOOL_NAMES
        if _remaining_ops:
            raise RuntimeError(
                "operator-tool gate failed: non-operator server still "
                f"exposes {sorted(_remaining_ops)} after removal pass"
            )

    # ------------------------------------------------------------------
    # Attach store and Hive metadata to server for testing / tool access
    # ------------------------------------------------------------------
    mcp._tapps_store = store
    mcp._tapps_default_store = default_store
    mcp._tapps_agent_id = agent_id
    mcp._tapps_hive_enabled = enable_hive
    mcp._tapps_operator_tools_enabled = enable_operator_tools
    mcp._tapps_hive_store = getattr(default_store, "_hive_store", None)
    # EPIC-073 STORY-073.1: attach profile registry so middleware / tools can
    # resolve the active profile without re-loading YAML per request.
    mcp._tapps_profile_registry = _profile_registry

    # ------------------------------------------------------------------
    # Per-request tool filter (EPIC-073 STORY-073.3)
    # Wraps _tool_manager.list_tools and _tool_manager.call_tool so that:
    # - tools/list returns only tools allowed by the resolved profile
    # - tools/call raises McpError(-32601) for tools outside the profile
    # The "full" profile is the fast path — zero overhead for existing clients.
    # ------------------------------------------------------------------
    from tapps_brain.mcp_server.tool_filter import install_tool_filter

    install_tool_filter(mcp, profile_registry=_profile_registry)

    return mcp


def _build_base_parser(prog: str, description: str) -> argparse.ArgumentParser:
    """Build the common argument parser shared by standard and operator CLIs."""
    try:
        pkg_ver = importlib.metadata.version("tapps-brain")
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover
        pkg_ver = "0.0.0-dev"
    parser = argparse.ArgumentParser(prog=prog, description=description)
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {pkg_ver}",
    )
    parser.add_argument(
        "--project-dir",
        type=str,
        default=None,
        help="Project root directory (defaults to cwd).",
    )
    parser.add_argument(
        "--agent-id",
        type=str,
        default="unknown",
        help="Agent identifier for Hive propagation (default: 'unknown').",
    )
    parser.add_argument(
        "--enable-hive",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable Hive multi-agent shared brain (default: enabled).",
    )
    # STORY-070.15: one binary, both transports
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default=os.environ.get("TAPPS_BRAIN_MCP_TRANSPORT", "stdio"),
        help=(
            "MCP transport to use: 'stdio' (default, for AGENT.md/IDE use) or "
            "'streamable-http' (for Docker/network use). "
            "Override via TAPPS_BRAIN_MCP_TRANSPORT env var."
        ),
    )
    parser.add_argument(
        "--mcp-host",
        type=str,
        default=os.environ.get("TAPPS_BRAIN_MCP_HOST", "127.0.0.1"),
        help=(
            "Host to bind when --transport=streamable-http (default: 127.0.0.1). "
            "Override via TAPPS_BRAIN_MCP_HOST env var."
        ),
    )
    parser.add_argument(
        "--mcp-port",
        type=int,
        default=int(os.environ.get("TAPPS_BRAIN_MCP_PORT", "8091")),
        help=(
            "Port to bind when --transport=streamable-http (default: 8091). "
            "Override via TAPPS_BRAIN_MCP_PORT env var."
        ),
    )
    return parser


def create_operator_server(
    project_dir: Path | None = None,
    *,
    enable_hive: bool = True,
    agent_id: str = "unknown",
) -> Any:
    """Create a FastMCP server with **operator tools always enabled**.

    Exposes the full set of maintenance tools (GC, consolidation, import,
    export, migration, relay).  Intended for operator / admin access only —
    do **not** grant this server in a normal agent's ``AGENT.md``.
    See ``tapps-brain-operator-mcp`` CLI entry point.
    """
    return create_server(
        project_dir, enable_hive=enable_hive, agent_id=agent_id, enable_operator_tools=True
    )


def main() -> None:
    """Entry point for ``tapps-brain-mcp`` (standard — safe for AGENT.md).

    STORY-070.9: operator tools are **never** exposed regardless of the
    ``TAPPS_BRAIN_OPERATOR_TOOLS`` environment variable.  Use
    ``tapps-brain-operator-mcp`` when operator-level access is required.

    STORY-070.15: supports ``--transport streamable-http`` for Docker/network use.
    """
    parser = _build_base_parser(
        "tapps-brain-mcp",
        (
            "Run the tapps-brain MCP server (stdio or streamable-http transport). "
            "Standard server: no operator tools (safe for AGENT.md grants). "
            "Version matches the installed tapps-brain package."
        ),
    )
    args = parser.parse_args()

    effective_agent_id = args.agent_id
    if effective_agent_id == "unknown":
        effective_agent_id = os.environ.get("TAPPS_BRAIN_AGENT_ID", "unknown")

    project_dir = Path(args.project_dir) if args.project_dir else None
    try:
        # STORY-070.9: standard server — operator tools are NEVER enabled.
        # TAPPS_BRAIN_OPERATOR_TOOLS is intentionally not read here.
        server = create_server(
            project_dir,
            enable_hive=args.enable_hive,
            agent_id=effective_agent_id,
            enable_operator_tools=False,
        )
    except RuntimeError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        sys.exit(1)
    # STORY-070.15: configure host/port for streamable-http transport
    if args.transport == "streamable-http":
        server.settings.host = args.mcp_host
        server.settings.port = args.mcp_port
    server.run(transport=args.transport)


def main_operator() -> None:
    """Entry point for ``tapps-brain-operator-mcp`` (operator tools always on).

    STORY-070.9: this server always exposes operator tools (GC, consolidation,
    import/export, migration, relay).  Do **not** grant this entry point to
    regular agents — use ``tapps-brain-mcp`` instead.

    STORY-070.15: supports ``--transport streamable-http`` for Docker/network use.
    The default port for operator streamable-http is 8090 (set TAPPS_BRAIN_MCP_PORT).
    """
    parser = _build_base_parser(
        "tapps-brain-operator-mcp",
        (
            "Run the tapps-brain operator MCP server (stdio or streamable-http transport). "
            "Operator server: GC, consolidation, import/export, migration, relay. "
            "Not for regular agent sessions — use tapps-brain-mcp for AGENT.md grants."
        ),
    )
    args = parser.parse_args()

    effective_agent_id = args.agent_id
    if effective_agent_id == "unknown":
        effective_agent_id = os.environ.get("TAPPS_BRAIN_AGENT_ID", "unknown")

    project_dir = Path(args.project_dir) if args.project_dir else None
    try:
        # STORY-070.9: operator server — operator tools are ALWAYS enabled.
        server = create_operator_server(
            project_dir,
            enable_hive=args.enable_hive,
            agent_id=effective_agent_id,
        )
    except RuntimeError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        sys.exit(1)
    # STORY-070.15: configure host/port for streamable-http transport
    if args.transport == "streamable-http":
        server.settings.host = args.mcp_host
        server.settings.port = args.mcp_port
    server.run(transport=args.transport)


if __name__ == "__main__":
    main()
