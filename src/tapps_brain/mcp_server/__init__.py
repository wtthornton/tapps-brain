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
    _STORE_CACHE,  # noqa: F401
    REQUEST_AGENT_ID,
    REQUEST_GROUP,
    REQUEST_PROFILE,
    REQUEST_PROJECT_ID,
    REQUEST_SCOPE,
    ToolContext,
    _current_request_group,  # noqa: F401
    _current_request_project_id,  # noqa: F401
    _current_request_scope,  # noqa: F401
    _resolve_per_call_agent_id,  # noqa: F401
    _StoreCache,  # noqa: F401
    _StoreProxy,  # noqa: F401
)
from tapps_brain.mcp_server.server import (
    _build_transport_security,  # noqa: F401
    _get_store,  # noqa: F401
    _lazy_import_mcp,  # noqa: F401
    _resolve_project_dir,  # noqa: F401
    create_operator_server,
    create_server,
    main,
    main_operator,
)
from tapps_brain.services._common import parse_details_json as _mcp_parse_details_json  # noqa: F401

__all__ = [
    "REQUEST_AGENT_ID",
    "REQUEST_GROUP",
    "REQUEST_PROFILE",
    "REQUEST_PROJECT_ID",
    "REQUEST_SCOPE",
    "ToolContext",
    "create_operator_server",
    "create_server",
    "main",
    "main_operator",
]


if __name__ == "__main__":
    main()
