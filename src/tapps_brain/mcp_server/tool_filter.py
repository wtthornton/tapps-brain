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
"""

from __future__ import annotations

import contextvars
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from tapps_brain.mcp_server.profile_registry import ProfileRegistry

logger = structlog.get_logger(__name__)

_DEFAULT_PROFILE = "full"


def install_tool_filter(
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
        """Return tool list filtered to the caller's active profile."""
        all_tools = _orig_list_tools()
        profile: str = profile_contextvar.get() or default_profile  # type: ignore[union-attr]
        if profile == default_profile:
            # Fast path: no filtering for the default ("full") profile.
            return all_tools
        try:
            allowed: frozenset[str] = profile_registry.get(profile)
        except Exception:
            # Unknown profile — fail open for list_tools; return full list.
            logger.warning(
                "tool_filter.list_tools.unknown_profile",
                profile=profile,
                action="fail_open",
            )
            return all_tools
        return [t for t in all_tools if t.name in allowed]

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
        profile: str = profile_contextvar.get() or default_profile  # type: ignore[union-attr]
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
            else:
                if name not in allowed:
                    # Lazy imports: keep the ``mcp`` package optional at
                    # module-load time so this module can be imported in
                    # environments that don't have the [mcp] extra installed.
                    from mcp.shared.exceptions import McpError
                    from mcp.types import METHOD_NOT_FOUND, ErrorData

                    logger.info(
                        "tool_filter.call_tool.denied",
                        tool=name,
                        profile=profile,
                    )
                    raise McpError(
                        ErrorData(
                            code=METHOD_NOT_FOUND,
                            message=(f"Tool {name!r} is not available in profile {profile!r}."),
                            data={
                                "error": "tool_not_in_profile",
                                "tool": name,
                                "profile": profile,
                            },
                        )
                    )
        return await _orig_call_tool(name, arguments, **kwargs)

    # Install wrappers on the tool manager instance (not the class) so only
    # this *mcp* instance is affected.
    mcp._tool_manager.list_tools = _filtered_list_tools
    mcp._tool_manager.call_tool = _filtered_call_tool
