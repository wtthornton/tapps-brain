"""Per-request context, store cache, and tool-context dataclass for MCP server.

Extracted from ``tapps_brain.mcp_server.__init__`` (TAP-605) to keep the
package entry point small.  This module owns:

- The request-scoped :mod:`contextvars` set by the HTTP tenant middleware
  (``REQUEST_PROJECT_ID``, ``REQUEST_AGENT_ID``, ``REQUEST_SCOPE``,
  ``REQUEST_GROUP``, ``REQUEST_PROFILE``).
- The bounded LRU :class:`_StoreCache` of ``MemoryStore`` instances keyed by
  ``project_id`` (STORY-069.3).
- The :class:`_StoreProxy` shim that dispatches each attribute access to the
  per-request store resolved from the active contextvars.
- The :class:`ToolContext` dataclass used by the ``register_*`` helpers
  in ``tools_*`` to avoid threading half a dozen closures through each
  registration call.

Public re-exports remain available from ``tapps_brain.mcp_server`` for
backward compatibility.
"""

from __future__ import annotations

import contextvars
import os
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

import structlog

logger = structlog.get_logger(__name__)


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

    def get_or_create(self, project_id: str, factory: Any) -> Any:  # noqa: ANN401
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


def _safe_close_store(store: Any) -> None:  # noqa: ANN401
    close = getattr(store, "close", None)
    if close is None:
        return
    try:
        close()
    except Exception:
        logger.debug("store_cache.close_failed", exc_info=True)


_STORE_CACHE = _StoreCache()


def _resolve_project_dir_for_id(project_id: str) -> Path:
    return Path.cwd().resolve()


def _get_store_for_project(
    project_id: str | None,
    *,
    default_store: Any,  # noqa: ANN401
    enable_hive: bool = True,
    agent_id: str = "unknown",
    call_agent_id: str | None = None,
) -> Any:  # noqa: ANN401
    """Resolve a ``MemoryStore`` for *project_id*, optionally scoped to a per-call agent.

    STORY-070.7 — when *call_agent_id* is supplied and differs from the
    server-level *agent_id*, the cache key becomes ``"<pid>\\x00<aid>"`` so
    pooled MCP connections can multiplex many agents without bleeding
    Hive / propagation identity across tool calls.
    """
    # Import via package level so monkeypatch on tapps_brain.mcp_server._get_store works.
    # (Lazy import avoids circular: server.py imports from context.py.)
    import tapps_brain.mcp_server as _ms_pkg

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

    def _factory() -> Any:  # noqa: ANN401
        prev = os.environ.get("TAPPS_BRAIN_PROJECT")
        if project_id:
            os.environ["TAPPS_BRAIN_PROJECT"] = project_id
        try:
            target_dir = _resolve_project_dir_for_id(project_id) if project_id else Path.cwd()
            return _ms_pkg._get_store(
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
    except Exception:
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


def _current_request_agent_id() -> str | None:  # noqa: PLR0911
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
    except Exception:
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
    except Exception:
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
        default_store: Any,  # noqa: ANN401
        *,
        enable_hive: bool,
        agent_id: str,
    ) -> None:
        object.__setattr__(self, "_default_store", default_store)
        object.__setattr__(self, "_enable_hive", enable_hive)
        object.__setattr__(self, "_agent_id", agent_id)

    def _resolve(self) -> Any:  # noqa: ANN401
        import tapps_brain.mcp_server as _ms_pkg

        pid = _ms_pkg._current_request_project_id()
        try:
            return _get_store_for_project(
                pid,
                default_store=self._default_store,
                enable_hive=self._enable_hive,
                agent_id=self._agent_id,
            )
        except Exception as exc:
            from tapps_brain.project_registry import ProjectNotRegisteredError

            if isinstance(exc, ProjectNotRegisteredError):
                _raise_project_not_registered(exc.project_id)
            raise

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        return getattr(self._resolve(), name)

    def __setattr__(self, name: str, value: Any) -> None:  # noqa: ANN401
        if name in self.__slots__:
            object.__setattr__(self, name, value)
        else:
            setattr(self._resolve(), name, value)

    @property  # type: ignore[misc]
    def __class__(self) -> type:
        try:
            resolved_class: type = self._resolve().__class__
            return resolved_class
        except Exception:
            return _StoreProxy


@dataclass
class ToolContext:
    """Aggregate of per-server closures shared by every ``register_*`` helper.

    Each ``register_*`` function in ``tools_*.py`` accepts a single
    :class:`ToolContext` instead of many positional arguments.  This keeps
    the registration call sites narrow and avoids threading 8+ closures
    through each call.
    """

    store: Any
    """The :class:`_StoreProxy` configured for this server."""

    server_agent_id: str
    """Default agent_id passed to :func:`create_server` (STORY-070.7 fallback)."""

    resolve_store_for_call: Callable[[str], Any]
    """``(call_agent_id: str) -> MemoryStore`` — per-call store resolver."""

    hive_for_tools: Callable[[], tuple[Any, bool]]
    """``() -> (hive_backend, should_close)`` for Hive-aware tools."""

    pid: Callable[[], str]
    """``() -> str`` — returns the effective per-request project_id."""

    require_operator_enabled: Callable[[], None]
    """Guard invoked at the top of every operator tool (raises if disabled)."""

    resolved_dir: Path
    """Project root used by ``maintenance_consolidate`` and ``memory_export``."""

    resolve_per_call_agent_id: Callable[..., str]
    """``(call_val: str, *, default: str) -> str`` — agent_id precedence resolver."""
