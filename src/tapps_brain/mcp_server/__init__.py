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

TAP-605: the package has been split into focused submodules.  Tool bodies
for each family live in ``tools_brain``, ``tools_memory``, ``tools_feedback``,
``tools_resources``, ``tools_maintenance``, ``tools_hive``, and
``tools_agents``.  The per-request plumbing (:class:`_StoreProxy`,
:class:`_StoreCache`, contextvars, idempotency helpers) lives in
:mod:`tapps_brain.mcp_server.context`.  The server skeleton and CLI entry
points live in :mod:`tapps_brain.mcp_server.server`.  Public imports from
``tapps_brain.mcp_server`` remain stable.
"""

from __future__ import annotations

from tapps_brain.mcp_server.context import (
    REQUEST_AGENT_ID,
    REQUEST_GROUP,
    REQUEST_PROFILE,
    REQUEST_PROJECT_ID,
    REQUEST_SCOPE,
    _STORE_CACHE,
    _current_request_agent_id,
    _current_request_group,
    _current_request_idempotency_key,
    _current_request_project_id,
    _current_request_scope,
    _get_store_for_project,
    _raise_project_not_registered,
    _resolve_per_call_agent_id,
    _resolve_project_dir_for_id,
    _safe_close_store,
    _StoreCache,
    _StoreProxy,
    ToolContext,
)
from tapps_brain.mcp_server.server import (
    _build_base_parser,
    _build_transport_security,
    _get_store,
    _lazy_import_mcp,
    _MCP_INSTRUCTIONS,
    _OPERATOR_TOOL_NAMES,
    _resolve_project_dir,
    create_operator_server,
    create_server,
    main,
    main_operator,
)
from tapps_brain.services._common import parse_details_json as _mcp_parse_details_json  # noqa: F401

__all__ = [
    "create_server",
    "create_operator_server",
    "main",
    "main_operator",
    "REQUEST_PROJECT_ID",
    "REQUEST_AGENT_ID",
    "REQUEST_SCOPE",
    "REQUEST_GROUP",
    "REQUEST_PROFILE",
    "ToolContext",
]


if __name__ == "__main__":
    main()
