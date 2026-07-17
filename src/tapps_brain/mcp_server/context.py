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

import contextlib
import contextvars
import os
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


def _get_logger() -> Any:  # noqa: ANN401
    """Return a structlog logger for this module, importing structlog lazily.

    TAP-1834: avoids eager structlog import at module load time so that
    ``import tapps_brain.mcp_server`` (tool-catalog probe) does not pay the
    ~60 ms structlog startup cost.
    """
    import structlog

    return structlog.get_logger(__name__)


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


def _meta_field(meta: Any, field: str) -> str | None:  # noqa: ANN401
    """Read *field* from an MCP ``RequestParams.Meta`` (or compatible) object."""
    if meta is None:
        return None
    val = getattr(meta, field, None)
    if val is None:
        extra = getattr(meta, "model_extra", None) or {}
        val = extra.get(field)
    if not val:
        return None
    return str(val).strip() or None


def _header_tenant_from_mcp_request() -> tuple[str | None, str | None]:
    """Read the tenant envelope from the inbound HTTP request on MCP ``request_ctx``.

    Streamable HTTP session tasks process ``tools/call`` outside the FastAPI
    middleware task, so :data:`REQUEST_PROJECT_ID` is often unset even though
    :class:`McpTenantMiddleware` validated ``X-Project-Id`` on the wire.
    """
    try:
        from mcp.server.lowlevel.server import request_ctx as _request_ctx_var
    except Exception:
        return None, None
    try:
        rc = _request_ctx_var.get()
    except LookupError:
        return None, None
    req = getattr(rc, "request", None)
    headers = getattr(req, "headers", None)
    if headers is None:
        return None, None
    project_id = (headers.get("x-project-id") or "").strip() or None
    agent_id = (headers.get("x-agent-id") or "").strip() or None
    tapps_agent = (headers.get("x-tapps-agent") or "").strip()
    if tapps_agent:
        agent_id = tapps_agent
    return project_id, agent_id


def _meta_tenant_from_mcp_request() -> tuple[str | None, str | None]:
    """Read ``project_id`` / ``agent_id`` from MCP JSON-RPC ``params._meta``."""
    try:
        from mcp.server.lowlevel.server import request_ctx as _request_ctx_var
    except Exception:
        return None, None
    try:
        rc = _request_ctx_var.get()
    except LookupError:
        return None, None
    meta = getattr(rc, "meta", None)
    return _meta_field(meta, "project_id"), _meta_field(meta, "agent_id")


@contextlib.contextmanager
def _mcp_tenant_context_for_tool_call() -> Iterator[None]:
    """Bridge resolved tenant identity into contextvars for MCP tool execution."""
    pid = _current_request_project_id()
    aid = _current_request_agent_id()
    tokens: list[tuple[contextvars.ContextVar[str | None], contextvars.Token[str | None]]] = []
    if pid and REQUEST_PROJECT_ID.get() != pid:
        tokens.append((REQUEST_PROJECT_ID, REQUEST_PROJECT_ID.set(pid)))
    if aid and REQUEST_AGENT_ID.get() != aid:
        tokens.append((REQUEST_AGENT_ID, REQUEST_AGENT_ID.set(aid)))
    try:
        yield
    finally:
        for var, tok in reversed(tokens):
            var.reset(tok)


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
        _get_logger().debug("store_cache.close_failed", exc_info=True)


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

    # Compound key always includes agent_id so pooled MCP connections cannot
    # reuse another agent's store when only project_id matches.
    cache_key = f"{project_id}\x00{effective_agent_id}"

    default_pid = getattr(default_store, "_tapps_project_id", None)
    default_aid = getattr(default_store, "_agent_id", None)
    if (
        not per_call_differs
        and default_pid
        and project_id == default_pid
        and (default_aid is not None and default_aid == effective_agent_id)
    ):
        return default_store

    def _factory() -> Any:  # noqa: ANN401
        prev = os.environ.get("TAPPS_BRAIN_PROJECT")
        if project_id:
            os.environ["TAPPS_BRAIN_PROJECT"] = project_id
        try:
            target_dir = _resolve_project_dir_for_id(project_id) if project_id else Path.cwd()
            return _ms_pkg._get_store(  # type: ignore[attr-defined]
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
      1. ``X-Project-Id`` on the Starlette :class:`~starlette.requests.Request`
         attached to MCP ``request_ctx`` (authoritative for Streamable HTTP
         ``tools/call`` — session tasks inherit a stale
         :data:`REQUEST_PROJECT_ID` from ``initialize``).
      2. MCP JSON-RPC ``_meta.project_id`` on the active request context.
      3. ``REQUEST_PROJECT_ID`` contextvar (FastAPI middleware — same-task
         HTTP handlers only).
      4. ``TAPPS_BRAIN_PROJECT`` environment variable (stdio transport).
    """
    header_pid, _ = _header_tenant_from_mcp_request()
    if header_pid:
        return header_pid
    meta_pid, _ = _meta_tenant_from_mcp_request()
    if meta_pid:
        return meta_pid
    pid = REQUEST_PROJECT_ID.get()
    if pid:
        return str(pid).strip() or None
    env_pid = (os.environ.get("TAPPS_BRAIN_PROJECT") or "").strip()
    return env_pid or None


def _current_request_agent_id() -> str | None:
    """Return the effective per-request agent_id.

    Precedence (STORY-070.7):
      1. ``X-Tapps-Agent`` / ``X-Agent-Id`` on the Starlette request attached
         to MCP ``request_ctx`` (see :func:`_current_request_project_id`).
      2. MCP JSON-RPC ``_meta.agent_id`` on the active request context.
      3. :data:`REQUEST_AGENT_ID` contextvar (HTTP middleware — same-task only).
      4. ``None`` — caller falls back to the server-level default.
    """
    _, header_aid = _header_tenant_from_mcp_request()
    if header_aid:
        return header_aid
    _, meta_aid = _meta_tenant_from_mcp_request()
    if meta_aid:
        return meta_aid
    agent = REQUEST_AGENT_ID.get()
    if agent:
        val = str(agent).strip()
        if val:
            return val
    return None


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

    TAP-1936: When both *call_val* and the contextvar resolve to non-empty,
    non-equal values, emit a structured ``WARNING`` log so attribution drift
    is diagnosable.  If ``TAPPS_BRAIN_STRICT_AGENT_ID=1`` is set, raise
    :class:`ValueError` instead (caller translates to 400 / error envelope).
    """
    v = (call_val or "").strip()
    ctx = _current_request_agent_id()
    if v and ctx and v != ctx:
        strict = os.environ.get("TAPPS_BRAIN_STRICT_AGENT_ID", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        if strict:
            raise ValueError(
                f"agent_id mismatch: header/contextvar={ctx!r} kwarg={v!r}; "
                "TAPPS_BRAIN_STRICT_AGENT_ID is enabled."
            )
        _get_logger().warning(
            "agent_id.mismatch",
            kwarg_agent_id=v,
            header_agent_id=ctx,
            resolved=v,
            detail=(
                "agent_id kwarg and X-Agent-Id header (or _meta.agent_id) "
                "disagree; kwarg wins (backward compat). Set "
                "TAPPS_BRAIN_STRICT_AGENT_ID=1 to make this a hard error."
            ),
        )
    if v:
        return v
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

        pid = _ms_pkg._current_request_project_id()  # type: ignore[attr-defined]
        call_aid = _current_request_agent_id()
        try:
            return _get_store_for_project(
                pid,
                default_store=self._default_store,
                enable_hive=self._enable_hive,
                agent_id=self._agent_id,
                call_agent_id=call_aid if call_aid and call_aid != self._agent_id else None,
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
