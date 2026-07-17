"""Per-request MCP tool filter and authz enforcement — EPIC-073 STORY-073.3.

Two responsibilities installed once at server startup via :func:`install_tool_filter`:

1. **tools/list filtering** — wraps ``_tool_manager.list_tools`` to hide tools
   not in the caller's resolved profile.  Pure UX / context-bloat benefit.

2. **tools/call enforcement** — wraps ``_tool_manager.call_tool`` to reject
   calls to hidden tools with a structured JSON-RPC error (code ``-32601``
   Method not found).  This is the security-relevant half: without it a client
   that knows the tool name can invoke it despite not seeing it in
   ``tools/list``.

TAP-1985: deferred-tool filtering
---------------------------------
Profiles may mark individual tools with ``defer_loading: true`` in the YAML.
Deferred tools remain **callable** (they're not removed from the allowed-set
for ``tools/call``) but are **omitted from the default ``tools/list`` response**
so the eager catalog stays within the per-server budget (parent epic TAP-1983).
Clients adopt Anthropic Tool Search BETA via the
``advanced-tool-use-2025-11-20`` header to discover deferred tools on demand.

When a profile has no deferred entries, the ``full``-profile fast path stays
zero-overhead.

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

# ---------------------------------------------------------------------------
# TAP-1849: tapps_brain_mcp_probe_duration_seconds histogram
# ---------------------------------------------------------------------------
# Prometheus-style fixed-bucket histogram tracking tools/list latency.
# Separate histograms for cache-hit (warm) and cache-miss (cold) paths so
# operators can graph warm vs cold distributions independently.

#: Bucket upper bounds in seconds.  Covers warm cache (< 50 ms) through worst-
#: case cold-start (> 60 s).  MUST stay sorted ascending; +Inf is implicit.
_PROBE_HISTOGRAM_BUCKETS: tuple[float, ...] = (
    0.05,
    0.1,
    0.2,
    0.5,
    1.0,
    2.0,
    5.0,
    30.0,
    60.0,
    120.0,
)


class _ProbeHistogram:
    """Thread-safe Prometheus-style fixed-bucket histogram for probe durations.

    Each :meth:`observe` call records one *value_seconds* observation.
    Bucket counts are cumulative — ``bucket_counts[i]`` is the number of
    observations where ``value_seconds <= buckets[i]``.

    Only used for the tools/list probe metric; keep it minimal and lock-free
    except at observe/snapshot boundaries.
    """

    __slots__ = ("_bucket_counts", "_buckets", "_count", "_lock", "_sum")

    def __init__(self, buckets: tuple[float, ...]) -> None:
        self._buckets: tuple[float, ...] = buckets
        # One slot per declared bucket; each stores cumulative count ≤ that bound.
        self._bucket_counts: list[int] = [0] * len(buckets)
        self._sum: float = 0.0
        self._count: int = 0
        self._lock = threading.Lock()

    def observe(self, value_seconds: float) -> None:
        """Record one observation of *value_seconds*."""
        with self._lock:
            self._sum += value_seconds
            self._count += 1
            # Increment every bucket whose upper bound covers this observation.
            for i, bound in enumerate(self._buckets):
                if value_seconds <= bound:
                    self._bucket_counts[i] += 1

    def snapshot(self) -> dict[str, object]:
        """Return a frozen snapshot: ``{bucket_counts, sum, count, buckets}``."""
        with self._lock:
            return {
                "buckets": self._buckets,
                "bucket_counts": list(self._bucket_counts),
                "sum": self._sum,
                "count": self._count,
            }

    def reset(self) -> None:
        """Clear all state.  For use in tests only."""
        with self._lock:
            self._bucket_counts = [0] * len(self._buckets)
            self._sum = 0.0
            self._count = 0


# One histogram per cache_hit label value.
_PROBE_HIST_HIT: _ProbeHistogram = _ProbeHistogram(_PROBE_HISTOGRAM_BUCKETS)
_PROBE_HIST_MISS: _ProbeHistogram = _ProbeHistogram(_PROBE_HISTOGRAM_BUCKETS)


def get_probe_duration_histogram_snapshot() -> dict[str, dict[str, object]]:
    """Return frozen snapshots for the MCP probe duration histogram.

    Returns a dict with two keys:

    ``"true"``
        Snapshot for cache-hit (warm) observations.
    ``"false"``
        Snapshot for cache-miss (cold) observations.

    Each value is the dict returned by :meth:`_ProbeHistogram.snapshot`.
    Intended for the ``/metrics`` Prometheus renderer.
    """
    return {
        "true": _PROBE_HIST_HIT.snapshot(),
        "false": _PROBE_HIST_MISS.snapshot(),
    }


def reset_probe_histogram_counters() -> None:
    """Reset both probe duration histograms.  For use in tests only."""
    _PROBE_HIST_HIT.reset()
    _PROBE_HIST_MISS.reset()


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


def _suggest_profile(profile_registry: Any, tool: str, *, exclude: str | None = None) -> str | None:
    """Best-effort wrapper around :meth:`ProfileRegistry.suggest_profile_for_tool`.

    Returns ``None`` when *profile_registry* doesn't expose the helper (older
    instances or mock test doubles) or raises — the ``out_of_profile``
    envelope just omits the hint in that case rather than failing the denial.
    """
    fn = getattr(profile_registry, "suggest_profile_for_tool", None)
    if not callable(fn):
        return None
    try:
        result = fn(tool, exclude=exclude)
    except Exception:
        return None
    return result if (result is None or isinstance(result, str)) else None


def _deferred_for(profile_registry: Any, profile: str) -> frozenset[str]:
    """Return the deferred-tool set for *profile* with mock-friendly fallback.

    Mocks in unit tests (``MagicMock(spec=ProfileRegistry)``) auto-create a
    ``get_deferred`` attribute that returns a ``MagicMock`` rather than a
    frozenset.  When the registry doesn't expose ``get_deferred`` (older
    ProfileRegistry instances) or returns a non-iterable sentinel, fall back to
    an empty frozenset so the filter remains backwards-compatible.
    """
    fn = getattr(profile_registry, "get_deferred", None)
    if not callable(fn):
        return frozenset()
    try:
        result = fn(profile)
    except Exception:
        return frozenset()
    if isinstance(result, frozenset):
        return result
    try:
        return frozenset(result)
    except TypeError:
        return frozenset()


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

        TAP-1849: every call is timed and recorded to
        ``_PROBE_HIST_HIT`` (cache-warm path) or ``_PROBE_HIST_MISS``
        (cache-cold path) so ``/metrics`` can expose the probe duration
        histogram split by ``cache_hit`` label.
        """
        _t0 = time.monotonic()
        _cache_hit = False
        try:
            profile: str = profile_contextvar.get() or default_profile
            now = _t0  # reuse the monotonic timestamp; sub-ms drift is harmless

            # --- TAP-1833: cache hit — return a copy so callers cannot mutate ---
            cached = _TOOLS_LIST_CACHE.get(profile)
            if cached is not None and now < cached[0]:
                cached_tools = cached[1]
                with _METRICS_LOCK:
                    _MCP_TOOLS_LIST_TOTAL[profile] = _MCP_TOOLS_LIST_TOTAL.get(profile, 0) + 1
                    _MCP_TOOLS_LIST_VISIBLE_GAUGE[profile] = len(cached_tools)
                _cache_hit = True
                return list(cached_tools)

            # --- Cache miss: build the list from the underlying registry ---
            all_tools: list[Any] = list(_orig_list_tools())
            # TAP-1985: drop deferred tools from the visible catalog. Empty set
            # for profiles with no deferral annotations → zero-overhead.
            deferred = _deferred_for(profile_registry, profile)

            if profile == default_profile:
                # Fast path for the default ("full") profile: no profile-allow
                # filtering, just the deferred-tool curtain when applicable.
                visible_tools = (
                    [t for t in all_tools if t.name not in deferred] if deferred else all_tools
                )
                with _METRICS_LOCK:
                    _MCP_TOOLS_LIST_TOTAL[profile] = _MCP_TOOLS_LIST_TOTAL.get(profile, 0) + 1
                    _MCP_TOOLS_LIST_VISIBLE_GAUGE[profile] = len(visible_tools)
                # Store in cache (concurrent miss → idempotent overwrite, same result).
                # Return a copy — the cached list is the canonical reference; the
                # caller must not be able to corrupt it via mutation.
                _TOOLS_LIST_CACHE[profile] = (now + _TOOLS_LIST_CACHE_TTL, visible_tools)
                return list(visible_tools)
            try:
                allowed: frozenset[str] = profile_registry.get(profile)
            except Exception:
                # Unknown profile — fail closed (empty tool list). Spoofed or
                # mistyped profiles must not receive the full operator surface.
                logger.warning(
                    "tool_filter.list_tools.unknown_profile",
                    profile=profile,
                    action="fail_closed",
                )
                with _METRICS_LOCK:
                    _MCP_TOOLS_LIST_TOTAL[profile] = _MCP_TOOLS_LIST_TOTAL.get(profile, 0) + 1
                    _MCP_TOOLS_LIST_VISIBLE_GAUGE[profile] = 0
                return []
            filtered = [t for t in all_tools if t.name in allowed and t.name not in deferred]
            with _METRICS_LOCK:
                _MCP_TOOLS_LIST_TOTAL[profile] = _MCP_TOOLS_LIST_TOTAL.get(profile, 0) + 1
                _MCP_TOOLS_LIST_VISIBLE_GAUGE[profile] = len(filtered)
            # Cache the filtered result for this profile.
            # Return a copy — same mutation safety rationale as the full-profile path.
            _TOOLS_LIST_CACHE[profile] = (now + _TOOLS_LIST_CACHE_TTL, filtered)
            return list(filtered)
        finally:
            # TAP-1849: record probe duration regardless of outcome.
            _elapsed = time.monotonic() - _t0
            if _cache_hit:
                _PROBE_HIST_HIT.observe(_elapsed)
            else:
                _PROBE_HIST_MISS.observe(_elapsed)

    # ------------------------------------------------------------------
    # Wrap call_tool
    # ------------------------------------------------------------------

    async def _filtered_call_tool(name: str, arguments: dict[str, Any], **kwargs: Any) -> Any:
        """Pre-check the caller's profile before executing a tool call.

        Raises :class:`mcp.shared.exceptions.McpError` (code ``-32601``) when
        *name* is not in the caller's allowed tool set.  On unknown profile,
        fails closed (denies the call) so a spoofed profile cannot bypass the
        allowlist.
        """
        profile: str = profile_contextvar.get() or default_profile
        if profile != default_profile:
            try:
                allowed = profile_registry.get(profile)
            except Exception:
                from mcp.shared.exceptions import McpError
                from mcp.types import INVALID_PARAMS, ErrorData

                logger.warning(
                    "tool_filter.call_tool.unknown_profile",
                    tool=name,
                    profile=profile,
                    action="fail_closed",
                )
                with _METRICS_LOCK:
                    key = (profile, name, "error")
                    _MCP_TOOLS_CALL_TOTAL[key] = _MCP_TOOLS_CALL_TOTAL.get(key, 0) + 1
                raise McpError(
                    ErrorData(
                        code=INVALID_PARAMS,
                        message=(f"Unknown brain profile {profile!r}; tool {name!r} denied."),
                    )
                ) from None
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
                    # TAP-1972: include `suggested_profile` so clients can
                    # render "switch to profile X" without re-parsing YAML.
                    _suggested = _suggest_profile(profile_registry, name, exclude=profile)
                    raise McpError(
                        ErrorData(
                            code=INVALID_PARAMS,
                            message=(f"Tool {name!r} is not available in profile {profile!r}."),
                            data={
                                "reason": "out_of_profile",
                                "tool": name,
                                "profile": profile,
                                "suggested_profile": _suggested,
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
        from tapps_brain.mcp_server.context import _mcp_tenant_context_for_tool_call

        with _mcp_tenant_context_for_tool_call():
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
    # TAP-2050: expose the pre-filter ``list_tools`` so tests can verify which
    # tools are **registered and callable** without going through the
    # profile / deferred-loading curtain. Production paths must keep using
    # ``list_tools`` so the eager catalog stays correct. Deferred tools are
    # still callable via ``call_tool`` regardless of which list view a
    # caller inspects.
    mcp._tool_manager._unfiltered_list_tools = _orig_list_tools

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
                from mcp.shared.exceptions import McpError
                from mcp.types import INVALID_PARAMS, ErrorData

                # Unknown profile — fail closed (deny). Matches list_tools /
                # call_tool behaviour above.
                logger.warning(
                    "tool_filter.request_handler.unknown_profile",
                    profile=profile,
                    action="fail_closed",
                )
                tool_name = req.params.name
                with _METRICS_LOCK:
                    key = (profile, tool_name, "error")
                    _MCP_TOOLS_CALL_TOTAL[key] = _MCP_TOOLS_CALL_TOTAL.get(key, 0) + 1
                raise McpError(
                    ErrorData(
                        code=INVALID_PARAMS,
                        message=(f"Unknown brain profile {profile!r}; tool {tool_name!r} denied."),
                    )
                ) from None

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
                _suggested = _suggest_profile(profile_registry, tool_name, exclude=profile)
                raise McpError(
                    ErrorData(
                        code=INVALID_PARAMS,
                        message=(f"Tool {tool_name!r} is not available in profile {profile!r}."),
                        data={
                            "reason": "out_of_profile",
                            "tool": tool_name,
                            "profile": profile,
                            "suggested_profile": _suggested,
                        },
                    )
                )
        return await _orig_request_handler(req)

    request_handlers[_mcp_types.CallToolRequest] = _profile_gated_request_handler
