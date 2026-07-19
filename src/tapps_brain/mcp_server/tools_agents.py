"""Agent registry MCP tool registrations.

Extracted from ``tapps_brain.mcp_server.__init__`` (TAP-605).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tapps_brain.mcp_server.context import ToolContext

from tapps_brain.services import agents_service


def register_agent_tools(mcp: Any, ctx: ToolContext) -> None:  # noqa: ANN401
    """Register the four agent-registry tools on *mcp*."""
    store = ctx.store
    # Distinct name: the tool parameters below are the *target* agent and must
    # not shadow the acting (server) identity passed as the services' third
    # positional — that slot exists for attribution/authorization.
    server_agent_id = ctx.server_agent_id
    _pid = ctx.pid

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
                server_agent_id,
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
                server_agent_id,
                new_agent_id=agent_id,
                profile=profile,
                skills=skills,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def agent_list() -> str:
        """List all registered agents in the Hive."""
        return json.dumps(agents_service.agent_list(store, _pid(), server_agent_id))

    @mcp.tool()  # type: ignore[untyped-decorator]
    def agent_delete(agent_id: str) -> str:
        """Delete a registered agent from the Hive."""
        return json.dumps(
            agents_service.agent_delete(
                store,
                _pid(),
                server_agent_id,
                target_agent_id=agent_id,
            )
        )
