"""FastMCP server skeleton: wires tools, resources, prompts, and CLIs.

Extracted from ``tapps_brain.mcp_server.__init__`` (TAP-605).  The per-call
context machinery lives in :mod:`tapps_brain.mcp_server.context`; each
family of tools lives in ``tools_*`` and is attached to ``mcp`` via a
``register_*`` helper.

This module owns:

- :func:`_lazy_import_mcp`, :func:`_resolve_project_dir`,
  :func:`_build_transport_security`, :func:`_get_store` — low-level server
  plumbing.
- :func:`create_server` — orchestrates tool registration, profile registry,
  operator-tool gate, and returns the configured FastMCP instance.
- :func:`create_operator_server`, :func:`main`, :func:`main_operator`,
  :func:`_build_base_parser` — CLI entry points.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import logging
import os
import sys
from pathlib import Path
from typing import Any

import structlog

from tapps_brain.mcp_server.context import (
    ToolContext,
    _StoreProxy,
    _current_request_project_id,
    _get_store_for_project,
    _raise_project_not_registered,
    _resolve_per_call_agent_id,
)

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


def create_server(
    project_dir: Path | None = None,
    *,
    enable_hive: bool = True,
    agent_id: str = "unknown",
    enable_operator_tools: bool = False,
) -> Any:
    """Create and configure a FastMCP server instance.

    The server skeleton opens a per-project store, creates the
    :class:`_StoreProxy`, builds a :class:`ToolContext`, and then delegates
    tool registration to the ``register_*`` helpers in the ``tools_*``
    modules (TAP-605).  Tool *bodies* themselves live in
    ``tapps_brain.services.*`` (EPIC-070 STORY-070.1).
    """
    fastmcp_cls = _lazy_import_mcp()

    resolved_dir = _resolve_project_dir(str(project_dir) if project_dir else None)
    try:
        default_store = _get_store(resolved_dir, enable_hive=enable_hive, agent_id=agent_id)
    except Exception as exc:
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

    # STORY-070.2: Modern MCP 2025-03-26 Streamable HTTP transport.
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
    async def _mcp_health(_request: Any) -> Any:
        from starlette.responses import PlainTextResponse

        return PlainTextResponse("ok")

    # ------------------------------------------------------------------
    # Per-call helpers exposed to each tool-registration module via
    # ToolContext.  These close over ``store``, ``_server_agent_id``, and
    # ``mcp`` without dragging the register_* helpers into the closure
    # itself.
    # ------------------------------------------------------------------

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

    ctx = ToolContext(
        store=store,
        server_agent_id=_server_agent_id,
        resolve_store_for_call=_resolve_store_for_call,
        hive_for_tools=_hive_for_tools,
        pid=_pid,
        require_operator_enabled=_require_operator_enabled,
        resolved_dir=resolved_dir,
        resolve_per_call_agent_id=_resolve_per_call_agent_id,
    )

    # ------------------------------------------------------------------
    # Register tools, resources, and prompts.
    # ------------------------------------------------------------------
    from tapps_brain.mcp_server.tools_agents import register_agent_tools
    from tapps_brain.mcp_server.tools_brain import register_brain_tools
    from tapps_brain.mcp_server.tools_feedback import register_feedback_tools
    from tapps_brain.mcp_server.tools_hive import register_hive_tools
    from tapps_brain.mcp_server.tools_maintenance import register_maintenance_tools
    from tapps_brain.mcp_server.tools_memory import (
        register_knowledge_tools,
        register_memory_tools,
    )
    from tapps_brain.mcp_server.tools_resources import register_resources_and_prompts

    register_brain_tools(mcp, ctx)
    register_memory_tools(mcp, ctx)
    register_feedback_tools(mcp, ctx)
    register_resources_and_prompts(mcp, ctx)
    register_maintenance_tools(mcp, ctx)
    register_hive_tools(mcp, ctx)
    register_agent_tools(mcp, ctx)
    register_knowledge_tools(mcp, ctx)

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
