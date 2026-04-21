"""Hive MCP tool registrations (EPIC-011).

Extracted from ``tapps_brain.mcp_server.__init__`` (TAP-605).
"""

from __future__ import annotations

import json
from typing import Any

from tapps_brain.mcp_server.context import ToolContext
from tapps_brain.services import hive_service


def register_hive_tools(mcp: Any, ctx: ToolContext) -> None:
    """Register the six Hive tools on *mcp*."""
    store = ctx.store
    agent_id = ctx.server_agent_id
    _pid = ctx.pid
    _hive_for_tools = ctx.hive_for_tools

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
