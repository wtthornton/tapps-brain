"""Per-request MCP tool filter and authz enforcement — EPIC-073 STORY-073.3.

Two responsibilities installed once at server startup via :func:`install_tool_filter`:

1. **tools/list filtering** — wraps ``_tool_manager.list_tools`` to hide tools
   not in the caller's resolved profile.  Pure UX / context-bloat benefit.

2. **tools/call enforcement** — wraps ``_tool_manager.call_tool`` to reject
   calls to hidden tools with a structured JSON-RPC error (code ``-32601``
   Method not found).  This is the security-relevant half: without it a client
   that knows the tool name can invoke it despite not seeing it in
   ``tools/list``.

The ``full`` profile is the **fast path** — no filtering is applied, zero
runtime overhead.

Installing the filter
---------------------
Called once inside :func:`~tapps_brain.mcp_server.create_server`, **after**
all ``@mcp.tool`` decorators have been registered and the operator-tool
removal pass has run::

    from tapps_brain.mcp_server.tool_filter import install_tool_filter
    install_tool_filter(mcp, profile_registry=_profile_registry)

Thread-safety
-------------
The filter reads a ``contextvars.ContextVar`` on each request — no shared
mutable state, no locking required.  The resolved profile is set per-request
by ``ProfileResolutionMiddleware`` in ``http_adapter.py`` (STORY-073.2).

Observability — STORY-073.4
----------------------------
Module-level thread-safe counters are incremented inline:

* ``_MCP_TOOLS_LIST_TOTAL`` — ``{profile: count}`` counter
* ``_MCP_TOOLS_LIST_VISIBLE_GAUGE`` — ``{profile: visible_count}`` last-seen gauge
* ``_MCP_TOOLS_CALL_TOTAL`` — ``{(profile, tool, outcome): count}`` counter;
  ``outcome`` ∈ ``{allowed, denied_profile, error}``

Use :func:`get_profile_filter_metrics_snapshot` to read a frozen copy suitable
for the ``/metrics`` Prometheus renderer in ``http_adapter.py``.

Use :func:`reset_profile_filter_counters` in tests to clear state between runs.
"""

from __future__ import annotations

import contextvars
import threading
import time
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from tapps_brain.mcp_server.profile_registry import ProfileRegistry

logger = structlog.get_logger(__name__)

_DEFAULT_PROFILE = "full"

# ---------------------------------------------------------------------------
# TAP-1833: in-process tools/list cache — 300 s TTL
# ---------------------------------------------------------------------------
# Key: resolved profile name. Value: (expires_at monotonic float, tool list).
# Tools are registered at server startup and never change during process
# lifetime; the 300 s TTL is a safety valve for future hot-reload scenarios.
#
# CPython dict read/write is GIL-protected, so no separate lock is needed
# for the dict itself. Concurrent cache misses on the very first request may
# double-compute; the second write is idempotent (same tool list) and harmless.
_TOOLS_LIST_CACHE: dict[str, tuple[float, list[Any]]] = {}
_TOOLS_LIST_CACHE_TTL: float = 300.0


def clear_tools_list_cache() -> None:
    """Flush the in-process tools/list cache.  For use in tests only."""
    _TOOLS_LIST_CACHE.clear()


# ---------------------------------------------------------------------------
# STORY-073.4: module-level Prometheus counters (no per-agent-id cardinality)
# ---------------------------------------------------------------------------

_METRICS_LOCK = threading.Lock()

# mcp_tools_list_total{profile}
_MCP_TOOLS_LIST_TOTAL: dict[str, int] = {}
# mcp_tools_list_visible_tools{profile} — gauge (last observed value)
_MCP_TOOLS_LIST_VISIBLE_GAUGE: dict[str, int] = {}
# mcp_tools_call_total{profile, tool, outcome}
# key: (profile, tool, outcome) where outcome in {allowed, denied_profile, error}
_MCP_TOOLS_CALL_TOTAL: dict[tuple[str, str, str], int] = {}


def get_profile_filter_metrics_snapshot() -> dict[str, Any]:
    """Return a frozen copy of the profile-filter counters for ``/metrics``.

    Returns a dict with three keys:

    ``list_total``
        ``{profile: count}`` — total ``tools/list`` calls per profile.
    ``list_visible``
        ``{profile: last_visible_count}`` — last observed tool count returned.
    ``call_total``
        ``{(profile, tool, outcome): count}`` — tool invocations by outcome.
    """
    with _METRICS_LOCK:
        return {
            "list_total": dict(_MCP_TOOLS_LIST_TOTAL),
            "list_visible": dict(_MCP_TOOLS_LIST_VISIBLE_GAUGE),
            "call_total": dict(_MCP_TOOLS_CALL_TOTAL),
        }


def reset_profile_filter_counters() -> None:
    """Clear all profile-filter metric counters.  For use in tests only."""
    with _METRICS_LOCK:
        _MCP_TOOLS_LIST_TOTAL.clear()
        _MCP_TOOLS_LIST_VISIBLE_GAUGE.clear()
        _MCP_TOOLS_CALL_TOTAL.clear()


# ---------------------------------------------------------------------------
# TAP-1580: ValidationError → required_fields hint enrichment
# ---------------------------------------------------------------------------


def _missing_required_fields(exc: BaseException) -> list[str]:
    """Walk ``exc.__cause__`` chain for a pydantic ``ValidationError`` and
    return the dotted-path locations of all entries reporting
    ``type == "missing"``. Returns ``[]`` when no missing-required entries are
    present (including when no ValidationError is in the chain)."""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        errors_fn = getattr(cur, "errors", None)
        if cur.__class__.__name__ == "ValidationError" and callable(errors_fn):
            try:
                errs = errors_fn()
            except Exception:
                return []
            missing: list[str] = []
            for e in errs:
                if e.get("type") != "missing":
                    continue
                loc = e.get("loc", ())
                missing.append(".".join(str(p) for p in loc) if loc else "<root>")
            return missing
        cur = cur.__cause__
    return []


def _enriched_tool_error_message(name: str, original: str, missing: list[str]) -> str:
    """Inject a ``required_fields: [...]`` hint into a FastMCP ``ToolError``
    message string while preserving the original ``"Error executing tool <name>: "``
    prefix and the original pydantic body verbatim."""
    prefix = f"Error executing tool {name}: "
    body = original[len(prefix) :] if original.startswith(prefix) else original
    hint = "required_fields: [" + ", ".join(repr(f) for f in missing) + "]"
    return f"{prefix}{hint}. {body}"


def install_tool_filter(  # noqa: PLR0915  # single-concern wiring of list_tools + call_tool hooks
    mcp: Any,
    *,
    profile_registry: ProfileRegistry,
    profile_contextvar: contextvars.ContextVar[str | None] | None = None,
    default_profile: str = _DEFAULT_PROFILE,
) -> None:
    """Install per-request profile filter on *mcp* by wrapping its
    ``_tool_manager.list_tools`` and ``_tool_manager.call_tool`` methods.

    The wrapping is additive — tools remain registered in the underlying
    ``_tool_manager``; the filter is a read-time curtain, not a removal pass.
    Removing a tool via ``remove_tool`` (e.g. the operator-tool gate) is
    permanent; profile filtering is per-request and reversible.

    Parameters
    ----------
    mcp:
        A ``FastMCP`` instance (typed as ``Any`` to avoid a hard import of the
        optional ``mcp`` package at module-load time).
    profile_registry:
        The :class:`~tapps_brain.mcp_server.profile_registry.ProfileRegistry`
        instance that maps profile names to allowed tool-name frozensets.
    profile_contextvar:
        The ``ContextVar[str | None]`` that holds the resolved profile name for
        the current request.  Defaults to
        :data:`~tapps_brain.mcp_server.REQUEST_PROFILE` (STORY-073.2).
    default_profile:
        Profile name that disables filtering (fast path).  Defaults to
        ``"full"``.  When the contextvar is ``None`` the resolved profile falls
        back to *default_profile*.
    """
    if profile_contextvar is None:
        from tapps_brain.mcp_server import REQUEST_PROFILE

        profile_contextvar = REQUEST_PROFILE

    _orig_list_tools = mcp._tool_manager.list_tools
    _orig_call_tool = mcp._tool_manager.call_tool

    # ------------------------------------------------------------------
    # Wrap list_tools
    # ------------------------------------------------------------------

    def _filtered_list_tools() -> list[Any]:
        """Return tool list filtered to the caller's active profile.

        TAP-1833: result is cached per profile for ``_TOOLS_LIST_CACHE_TTL``
        seconds (300 s default).  The tool registry is immutable for the
        process lifetime; the TTL is a safety valve for future hot-reload
        scenarios.  Cache invalidates automatically on container restart.
        """
        profile: str = profile_contextvar.get() or default_profile
        now = time.monotonic()

        # --- TAP-1833: cache hit — return a copy so callers cannot mutate ---
        cached = _TOOLS_LIST_CACHE.get(profile)
        if cached is not None and now < cached[0]:
            cached_tools = cached[1]
            with _METRICS_LOCK:
                _MCP_TOOLS_LIST_TOTAL[profile] = _MCP_TOOLS_LIST_TOTAL.get(profile, 0) + 1
                _MCP_TOOLS_LIST_VISIBLE_GAUGE[profile] = len(cached_tools)
            return list(cached_tools)

        # --- Cache miss: build the list from the underlying registry ---
        all_tools: list[Any] = list(_orig_list_tools())

        if profile == default_profile:
            # Fast path: no filtering for the default ("full") profile.
            visible_count = len(all_tools)
            with _METRICS_LOCK:
                _MCP_TOOLS_LIST_TOTAL[profile] = _MCP_TOOLS_LIST_TOTAL.get(profile, 0) + 1
                _MCP_TOOLS_LIST_VISIBLE_GAUGE[profile] = visible_count
            # Store in cache (concurrent miss → idempotent overwrite, same result).
            # Return a copy — the cached list is the canonical reference; the
            # caller must not be able to corrupt it via mutation.
            _TOOLS_LIST_CACHE[profile] = (now + _TOOLS_LIST_CACHE_TTL, all_tools)
            return list(all_tools)
        try:
            allowed: frozenset[str] = profile_registry.get(profile)
        except Exception:
            # Unknown profile — fail open for list_tools; return full list.
            # Do NOT cache: the unknown profile may be a transient registration
            # race; a subsequent call after the profile is registered should
            # see the filtered view.
            logger.warning(
                "tool_filter.list_tools.unknown_profile",
                profile=profile,
                action="fail_open",
            )
            visible_count = len(all_tools)
            with _METRICS_LOCK:
                _MCP_TOOLS_LIST_TOTAL[profile] = _MCP_TOOLS_LIST_TOTAL.get(profile, 0) + 1
                _MCP_TOOLS_LIST_VISIBLE_GAUGE[profile] = visible_count
            return all_tools
        filtered = [t for t in all_tools if t.name in allowed]
        with _METRICS_LOCK:
            _MCP_TOOLS_LIST_TOTAL[profile] = _MCP_TOOLS_LIST_TOTAL.get(profile, 0) + 1
            _MCP_TOOLS_LIST_VISIBLE_GAUGE[profile] = len(filtered)
        # Cache the filtered result for this profile.
        # Return a copy — same mutation safety rationale as the full-profile path.
        _TOOLS_LIST_CACHE[profile] = (now + _TOOLS_LIST_CACHE_TTL, filtered)
        return list(filtered)

    # ------------------------------------------------------------------
    # Wrap call_tool
    # ------------------------------------------------------------------

    async def _filtered_call_tool(name: str, arguments: dict[str, Any], **kwargs: Any) -> Any:
        """Pre-check the caller's profile before executing a tool call.

        Raises :class:`mcp.shared.exceptions.McpError` (code ``-32601``) when
        *name* is not in the caller's allowed tool set.  On unknown profile,
        fails open (allows the call) to avoid denying legitimate operators who
        may have a profile that the server hasn't reloaded yet.
        """
        profile: str = profile_contextvar.get() or default_profile
        if profile != default_profile:
            try:
                allowed = profile_registry.get(profile)
            except Exception:
                # Unknown profile — fail open for call_tool.
                logger.warning(
                    "tool_filter.call_tool.unknown_profile",
                    tool=name,
                    profile=profile,
                    action="fail_open",
                )
                with _METRICS_LOCK:
                    key = (profile, name, "error")
                    _MCP_TOOLS_CALL_TOTAL[key] = _MCP_TOOLS_CALL_TOTAL.get(key, 0) + 1
            else:
                if name not in allowed:
                    # Lazy imports: keep the ``mcp`` package optional at
                    # module-load time so this module can be imported in
                    # environments that don't have the [mcp] extra installed.
                    from mcp.shared.exceptions import McpError
                    from mcp.types import INVALID_PARAMS, ErrorData

                    # STORY-073.4: WARN log with structured fields for alerting.
                    # Read agent_id / project_id from contextvars (best-effort;
                    # None when not running under the HTTP adapter).
                    try:
                        from tapps_brain.mcp_server import (
                            REQUEST_AGENT_ID,
                            REQUEST_PROJECT_ID,
                        )

                        _agent_id = REQUEST_AGENT_ID.get()
                        _project_id = REQUEST_PROJECT_ID.get()
                    except Exception:
                        _agent_id = None
                        _project_id = None

                    logger.warning(
                        "tool_filter.call_tool.denied",
                        tool=name,
                        profile=profile,
                        agent_id=_agent_id,
                        project_id=_project_id,
                        request_id=None,
                    )
                    with _METRICS_LOCK:
                        key = (profile, name, "denied_profile")
                        _MCP_TOOLS_CALL_TOTAL[key] = _MCP_TOOLS_CALL_TOTAL.get(key, 0) + 1
                    # TAP-1579: use INVALID_PARAMS (-32602) with
                    # `data.reason = "out_of_profile"` so MCP-bridge consumers
                    # (e.g. tapps-mcp BrainBridge) can distinguish "hidden by
                    # profile" from "tool does not exist" (-32601).
                    raise McpError(
                        ErrorData(
                            code=INVALID_PARAMS,
                            message=(f"Tool {name!r} is not available in profile {profile!r}."),
                            data={
                                "reason": "out_of_profile",
                                "tool": name,
                                "profile": profile,
                            },
                        )
                    )
                else:
                    with _METRICS_LOCK:
                        key = (profile, name, "allowed")
                        _MCP_TOOLS_CALL_TOTAL[key] = _MCP_TOOLS_CALL_TOTAL.get(key, 0) + 1
        else:
            # Fast path: full profile — count as allowed without tool-name label
            # to keep cardinality bounded (no per-tool explosion on full profile).
            with _METRICS_LOCK:
                key = (profile, "", "allowed")
                _MCP_TOOLS_CALL_TOTAL[key] = _MCP_TOOLS_CALL_TOTAL.get(key, 0) + 1
        try:
            return await _orig_call_tool(name, arguments, **kwargs)
        except Exception as exc:
            # TAP-1580: enrich ToolError messages whose underlying cause is a
            # pydantic ValidationError reporting missing required fields.
            from mcp.server.fastmcp.exceptions import ToolError

            if not isinstance(exc, ToolError):
                raise
            missing = _missing_required_fields(exc)
            if not missing:
                raise
            enriched = ToolError(_enriched_tool_error_message(name, str(exc), missing))
            raise enriched from exc.__cause__

    # Install wrappers on the tool manager instance (not the class) so only
    # this *mcp* instance is affected.
    mcp._tool_manager.list_tools = _filtered_list_tools
    mcp._tool_manager.call_tool = _filtered_call_tool

    # ------------------------------------------------------------------
    # TAP-1619: wrap the lowlevel `CallToolRequest` handler so the
    # profile denial reaches the HTTP wire as a JSON-RPC `error` envelope.
    #
    # Why this is needed in addition to the `_tool_manager.call_tool`
    # wrap above: the lowlevel server's `call_tool` decorator
    # (`mcp/server/lowlevel/server.py` ~L583) ends with a bare
    # ``except Exception as e: return _make_error_result(str(e))`` that
    # catches the `McpError` raised by `_filtered_call_tool` above and
    # converts it to ``CallToolResult(content=[TextContent(...)],
    # isError=True)`` — the structured ``data={"reason":
    # "out_of_profile", ...}`` payload is silently dropped before it
    # hits the wire.  Bridge consumers (tapps-mcp `BrainBridge`,
    # AgentForge) end up regex-matching the canonical denial string
    # because the JSON-RPC `error` envelope they're documented to
    # dispatch on never arrives.
    #
    # Fix: pre-check the profile here, *outside* the call_tool
    # decorator's try/except.  When denied, raise `McpError` directly;
    # `_handle_request` (`mcp/server/lowlevel/server.py` ~L764) catches
    # `McpError` separately and emits the full `ErrorData` as a
    # JSON-RPC `error` envelope.
    #
    # In-process callers that go straight through
    # ``mcp._tool_manager.call_tool`` (e.g. existing TAP-1579 unit
    # tests, ``test_out_of_profile_tool_raises_mcp_error`` above)
    # bypass this handler entirely and continue to receive the
    # `McpError` raised by ``_filtered_call_tool``.  The two layers
    # therefore each fire exactly once per request and metrics are not
    # double-counted: HTTP-denied requests never reach
    # ``_filtered_call_tool``; in-process-denied requests never reach
    # this handler.
    # ------------------------------------------------------------------

    try:
        from mcp import types as _mcp_types
    except ImportError:  # pragma: no cover — mcp extra not installed
        return

    lowlevel = getattr(mcp, "_mcp_server", None)
    if lowlevel is None:  # pragma: no cover — defensive against API drift
        return
    request_handlers = getattr(lowlevel, "request_handlers", None)
    if request_handlers is None:  # pragma: no cover
        return
    _orig_request_handler = request_handlers.get(_mcp_types.CallToolRequest)
    if _orig_request_handler is None:  # pragma: no cover
        return

    async def _profile_gated_request_handler(req: Any) -> Any:
        profile: str = profile_contextvar.get() or default_profile
        if profile != default_profile:
            try:
                allowed = profile_registry.get(profile)
            except Exception:
                # Unknown profile — fail open (matches list_tools / call_tool
                # behaviour above).  Delegate to the original handler so the
                # error metric and any other downstream effects still happen.
                return await _orig_request_handler(req)

            tool_name = req.params.name
            if tool_name not in allowed:
                from mcp.shared.exceptions import McpError
                from mcp.types import INVALID_PARAMS, ErrorData

                # Mirror the metrics + log emitted by ``_filtered_call_tool``
                # so HTTP-denied requests are observable on the same counters
                # as in-process-denied requests.
                try:
                    from tapps_brain.mcp_server import (
                        REQUEST_AGENT_ID,
                        REQUEST_PROJECT_ID,
                    )

                    _agent_id = REQUEST_AGENT_ID.get()
                    _project_id = REQUEST_PROJECT_ID.get()
                except Exception:
                    _agent_id = None
                    _project_id = None

                logger.warning(
                    "tool_filter.call_tool.denied",
                    tool=tool_name,
                    profile=profile,
                    agent_id=_agent_id,
                    project_id=_project_id,
                    request_id=None,
                    transport="jsonrpc",
                )
                with _METRICS_LOCK:
                    key = (profile, tool_name, "denied_profile")
                    _MCP_TOOLS_CALL_TOTAL[key] = _MCP_TOOLS_CALL_TOTAL.get(key, 0) + 1
                raise McpError(
                    ErrorData(
                        code=INVALID_PARAMS,
                        message=(f"Tool {tool_name!r} is not available in profile {profile!r}."),
                        data={
                            "reason": "out_of_profile",
                            "tool": tool_name,
                            "profile": profile,
                        },
                    )
                )
        return await _orig_request_handler(req)

    request_handlers[_mcp_types.CallToolRequest] = _profile_gated_request_handler
