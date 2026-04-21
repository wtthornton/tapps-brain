"""Agent Brain MCP tool registrations (EPIC-057).

Extracted from ``tapps_brain.mcp_server.__init__`` (TAP-605).  Each
``@mcp.tool()`` here is a thin wrapper that resolves the per-call store,
delegates to :mod:`tapps_brain.services.memory_service`, and serialises the
result to JSON.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tapps_brain.mcp_server.context import ToolContext

from tapps_brain.services import memory_service


def register_brain_tools(mcp: Any, ctx: ToolContext) -> None:  # noqa: ANN401
    """Register the six Agent Brain tools on *mcp*."""
    _server_aid = ctx.server_agent_id
    _resolve = ctx.resolve_store_for_call
    _pid = ctx.pid
    _rpc = ctx.resolve_per_call_agent_id

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_remember(
        fact: str,
        tier: str = "procedural",
        share: bool = False,
        share_with: str = "",
        agent_id: str = "",
        temporal_sensitivity: str | None = None,
        failed_approaches: list[str] | None = None,
    ) -> str:
        """Save a memory to the agent's brain.

        Use tier='architectural' for lasting decisions, 'pattern' for conventions,
        'procedural' for how-to knowledge. Set share=True to share with all groups,
        or share_with='hive' for org-wide.  Pass ``agent_id`` to override the
        server-level default for this call (STORY-070.7).

        Pass ``temporal_sensitivity='high'`` for facts that change quickly (decays
        4x faster), ``'low'`` for stable facts (decays 4x slower), or omit for the
        tier default.

        Pass ``failed_approaches`` to record dead-end investigation paths so future
        agents don't repeat them (max 5 items).  These are surfaced in brain_recall
        responses when non-empty.
        """
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        return json.dumps(
            memory_service.brain_remember(
                s,
                _pid(),
                eff_aid,
                fact=fact,
                tier=tier,
                share=share,
                share_with=share_with,
                temporal_sensitivity=temporal_sensitivity,
                failed_approaches=failed_approaches,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_recall(query: str, max_results: int = 5, agent_id: str = "") -> str:
        """Recall memories matching a query.

        Pass ``agent_id`` to override the server-level default for this call
        (STORY-070.7).
        """
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        return json.dumps(
            memory_service.brain_recall(
                s,
                _pid(),
                eff_aid,
                query=query,
                max_results=max_results,
            ),
            default=str,
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_forget(key: str, agent_id: str = "") -> str:
        """Archive a memory by key. The memory is not permanently deleted."""
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        return json.dumps(memory_service.brain_forget(s, _pid(), eff_aid, key=key))

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_learn_success(
        task_description: str,
        task_id: str = "",
        agent_id: str = "",
    ) -> str:
        """Record a successful task outcome."""
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        return json.dumps(
            memory_service.brain_learn_success(
                s,
                _pid(),
                eff_aid,
                task_description=task_description,
                task_id=task_id,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_learn_failure(
        description: str,
        task_id: str = "",
        error: str = "",
        agent_id: str = "",
    ) -> str:
        """Record a failed task outcome to avoid repeating mistakes."""
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        return json.dumps(
            memory_service.brain_learn_failure(
                s,
                _pid(),
                eff_aid,
                description=description,
                task_id=task_id,
                error=error,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_status(agent_id: str = "") -> str:
        """Show agent identity, group memberships, store stats, and Hive connectivity.

        The response reflects the effective ``agent_id`` after STORY-070.7
        per-call resolution (call param > contextvar/``_meta`` > server default).
        """
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        return json.dumps(memory_service.brain_status(s, _pid(), eff_aid), default=str)
