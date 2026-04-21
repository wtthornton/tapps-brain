"""Memory, knowledge-graph, audit, and tag MCP tool registrations.

Extracted from ``tapps_brain.mcp_server.__init__`` (TAP-605).  Each tool
is a thin wrapper around :mod:`tapps_brain.services.memory_service`.
"""

from __future__ import annotations

import json
import os
from typing import Any

from tapps_brain.mcp_server.context import (
    ToolContext,
    _current_request_idempotency_key,
)
from tapps_brain.services import memory_service


def register_memory_tools(mcp: Any, ctx: ToolContext) -> None:
    """Register core ``memory_*`` tools + bulk helpers + session tools."""
    _server_aid = ctx.server_agent_id
    _resolve = ctx.resolve_store_for_call
    _pid = ctx.pid
    _rpc = ctx.resolve_per_call_agent_id

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_save(
        key: str,
        value: str,
        tier: str = "pattern",
        source: str = "agent",
        tags: list[str] | None = None,
        scope: str = "project",
        confidence: float = -1.0,
        agent_scope: str = "private",
        source_agent: str = "",
        group: str | None = None,
        agent_id: str = "",
    ) -> str:
        """Save or update a memory entry.

        When ``TAPPS_BRAIN_IDEMPOTENCY=1``, pass ``_meta.idempotency_key`` (UUID)
        in the JSON-RPC envelope for duplicate-safe writes.

        Pass ``agent_id`` to override the server-level default for this
        call (STORY-070.7).
        """
        from tapps_brain.idempotency import IdempotencyStore, is_idempotency_enabled

        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)

        ikey = _current_request_idempotency_key()
        project_id = _pid()
        dsn = os.environ.get("TAPPS_BRAIN_DATABASE_URL", "").strip()

        if ikey and is_idempotency_enabled() and dsn and project_id:
            istore = IdempotencyStore(dsn)
            try:
                cached = istore.check(project_id, ikey)
                if cached is not None:
                    _status, body = cached
                    return json.dumps(body)
            finally:
                istore.close()

        result = memory_service.memory_save(
            s,
            project_id,
            eff_aid,
            key=key,
            value=value,
            tier=tier,
            source=source,
            tags=tags,
            scope=scope,
            confidence=confidence,
            agent_scope=agent_scope,
            source_agent=source_agent,
            group=group,
        )

        if ikey and is_idempotency_enabled() and dsn and project_id:
            status_code = 400 if (isinstance(result, dict) and "error" in result) else 200
            istore2 = IdempotencyStore(dsn)
            try:
                istore2.save(project_id, ikey, status_code, result)
            finally:
                istore2.close()

        return json.dumps(result)

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_get(key: str, agent_id: str = "") -> str:
        """Retrieve a single memory entry by key."""
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        return json.dumps(memory_service.memory_get(s, _pid(), eff_aid, key=key))

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_delete(key: str, agent_id: str = "") -> str:
        """Delete a memory entry by key."""
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        return json.dumps(memory_service.memory_delete(s, _pid(), eff_aid, key=key))

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_search(
        query: str,
        tier: str | None = None,
        scope: str | None = None,
        as_of: str | None = None,
        group: str | None = None,
        since: str = "",
        until: str = "",
        time_field: str = "created_at",
        agent_id: str = "",
    ) -> str:
        """Search memory entries using full-text search."""
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        return json.dumps(
            memory_service.memory_search(
                s,
                _pid(),
                eff_aid,
                query=query,
                tier=tier,
                scope=scope,
                as_of=as_of,
                group=group,
                since=since,
                until=until,
                time_field=time_field,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_list(
        tier: str | None = None,
        scope: str | None = None,
        include_superseded: bool = False,
        group: str | None = None,
        agent_id: str = "",
    ) -> str:
        """List memory entries with optional filters."""
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        return json.dumps(
            memory_service.memory_list(
                s,
                _pid(),
                eff_aid,
                tier=tier,
                scope=scope,
                include_superseded=include_superseded,
                group=group,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_list_groups(agent_id: str = "") -> str:
        """List distinct project-local memory group names (GitHub #49)."""
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        return json.dumps(memory_service.memory_list_groups(s, _pid(), eff_aid))

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_recall(
        message: str,
        group: str | None = None,
        agent_id: str = "",
    ) -> str:
        """Run auto-recall for a message and return ranked memories."""
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        return json.dumps(
            memory_service.memory_recall(s, _pid(), eff_aid, message=message, group=group)
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_reinforce(
        key: str,
        confidence_boost: float = 0.0,
        agent_id: str = "",
    ) -> str:
        """Reinforce a memory entry, boosting its confidence and resetting decay.

        When ``TAPPS_BRAIN_IDEMPOTENCY=1``, pass ``_meta.idempotency_key`` (UUID)
        in the JSON-RPC envelope for duplicate-safe writes.

        Pass ``agent_id`` to override the server-level default (STORY-070.7).
        """
        from tapps_brain.idempotency import IdempotencyStore, is_idempotency_enabled

        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)

        ikey = _current_request_idempotency_key()
        project_id = _pid()
        dsn = os.environ.get("TAPPS_BRAIN_DATABASE_URL", "").strip()

        if ikey and is_idempotency_enabled() and dsn and project_id:
            istore = IdempotencyStore(dsn)
            try:
                cached = istore.check(project_id, ikey)
                if cached is not None:
                    _status, body = cached
                    return json.dumps(body)
            finally:
                istore.close()

        result = memory_service.memory_reinforce(
            s,
            project_id,
            eff_aid,
            key=key,
            confidence_boost=confidence_boost,
        )

        if ikey and is_idempotency_enabled() and dsn and project_id:
            status_code = 400 if (isinstance(result, dict) and "error" in result) else 200
            istore2 = IdempotencyStore(dsn)
            try:
                istore2.save(project_id, ikey, status_code, result)
            finally:
                istore2.close()

        return json.dumps(result)

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_ingest(
        context: str,
        source: str = "agent",
        agent_scope: str = "private",
        agent_id: str = "",
    ) -> str:
        """Extract and store durable facts from conversation context."""
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        return json.dumps(
            memory_service.memory_ingest(
                s,
                _pid(),
                eff_aid,
                context=context,
                source=source,
                agent_scope=agent_scope,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_supersede(
        old_key: str,
        new_value: str,
        key: str | None = None,
        tier: str | None = None,
        tags: list[str] | None = None,
        agent_id: str = "",
    ) -> str:
        """Create a new version of a memory, superseding the old one."""
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        return json.dumps(
            memory_service.memory_supersede(
                s,
                _pid(),
                eff_aid,
                old_key=old_key,
                new_value=new_value,
                key=key,
                tier=tier,
                tags=tags,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_history(key: str, agent_id: str = "") -> str:
        """Show the full version chain for a memory key."""
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        return json.dumps(memory_service.memory_history(s, _pid(), eff_aid, key=key))

    # ------------------------------------------------------------------
    # Bulk tools (STORY-070.6)
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_save_many(
        entries: list[dict[str, str | float | list[str] | None]],
        agent_id: str = "",
    ) -> str:
        """Save multiple memory entries in a single call.

        Each entry must be a dict with at least ``key`` and ``value``.  Optional
        fields: ``tier``, ``source``, ``tags``, ``scope``, ``confidence``,
        ``agent_scope``, ``group``.

        Batch size is capped by ``TAPPS_BRAIN_MAX_BATCH_SIZE`` (default 100).

        Returns::

            {
                "results": [<per-item save result>, ...],
                "saved_count": int,
                "error_count": int,
            }
        """
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        return json.dumps(
            memory_service.memory_save_many(
                s,
                _pid(),
                eff_aid,
                entries=list(entries),
            ),
            default=str,
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_recall_many(queries: list[str], agent_id: str = "") -> str:
        """Run recall against multiple queries in a single call.

        Each query is a plain string message.  Batch size is capped by
        ``TAPPS_BRAIN_MAX_BATCH_SIZE`` (default 50 reads).

        Returns::

            {
                "results": [<per-query recall result>, ...],
                "query_count": int,
            }
        """
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        return json.dumps(
            memory_service.memory_recall_many(
                s,
                _pid(),
                eff_aid,
                queries=list(queries),
            ),
            default=str,
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_reinforce_many(
        entries: list[dict[str, str | float]],
        agent_id: str = "",
    ) -> str:
        """Reinforce multiple memory entries in a single call.

        Each entry must be a dict with at least ``key``.  Optional field:
        ``confidence_boost`` (float, default 0.0).

        Batch size is capped by ``TAPPS_BRAIN_MAX_BATCH_SIZE`` (default 100).

        Returns::

            {
                "results": [<per-item reinforce result>, ...],
                "reinforced_count": int,
                "error_count": int,
            }
        """
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        return json.dumps(
            memory_service.memory_reinforce_many(
                s,
                _pid(),
                eff_aid,
                entries=list(entries),
            ),
            default=str,
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_index_session(
        session_id: str,
        chunks: list[str],
        agent_id: str = "",
    ) -> str:
        """Index session chunks for future search."""
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        return json.dumps(
            memory_service.memory_index_session(
                s,
                _pid(),
                eff_aid,
                session_id=session_id,
                chunks=chunks,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_search_sessions(
        query: str,
        limit: int = 10,
        agent_id: str = "",
    ) -> str:
        """Search past session summaries."""
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        return json.dumps(
            memory_service.memory_search_sessions(
                s,
                _pid(),
                eff_aid,
                query=query,
                limit=limit,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_capture(
        response: str,
        source: str = "agent",
        agent_scope: str = "private",
        agent_id: str = "",
    ) -> str:
        """Extract and persist new facts from an agent response."""
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        return json.dumps(
            memory_service.memory_capture(
                s,
                _pid(),
                eff_aid,
                response=response,
                source=source,
                agent_scope=agent_scope,
            )
        )


def register_knowledge_tools(mcp: Any, ctx: ToolContext) -> None:
    """Register knowledge-graph, audit trail, and tag-management tools (EPIC-015)."""
    _server_aid = ctx.server_agent_id
    _resolve = ctx.resolve_store_for_call
    _pid = ctx.pid
    _rpc = ctx.resolve_per_call_agent_id

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_relations(key: str, agent_id: str = "") -> str:
        """Return all relations associated with a memory entry key."""
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        return json.dumps(memory_service.memory_relations(s, _pid(), eff_aid, key=key))

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_relations_get_batch(keys_json: str, agent_id: str = "") -> str:
        """Return relations for multiple memory keys in one call (STORY-048.2)."""
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        return json.dumps(
            memory_service.memory_relations_get_batch(
                s,
                _pid(),
                eff_aid,
                keys_json=keys_json,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_find_related(key: str, max_hops: int = 2, agent_id: str = "") -> str:
        """Find entries related to a key via BFS traversal of the relation graph."""
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        return json.dumps(
            memory_service.memory_find_related(
                s,
                _pid(),
                eff_aid,
                key=key,
                max_hops=max_hops,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_query_relations(
        subject: str = "",
        predicate: str = "",
        object_entity: str = "",
        agent_id: str = "",
    ) -> str:
        """Filter relations by subject, predicate, and/or object_entity."""
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        return json.dumps(
            memory_service.memory_query_relations(
                s,
                _pid(),
                eff_aid,
                subject=subject,
                predicate=predicate,
                object_entity=object_entity,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_audit(
        key: str = "",
        event_type: str = "",
        since: str = "",
        until: str = "",
        limit: int = 50,
        agent_id: str = "",
    ) -> str:
        """Query the audit trail for memory events."""
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        return json.dumps(
            memory_service.memory_audit(
                s,
                _pid(),
                eff_aid,
                key=key,
                event_type=event_type,
                since=since,
                until=until,
                limit=limit,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_list_tags(agent_id: str = "") -> str:
        """List all tags used in the memory store with their usage counts."""
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        return json.dumps(memory_service.memory_list_tags(s, _pid(), eff_aid))

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_update_tags(
        key: str,
        add: list[str] | None = None,
        remove: list[str] | None = None,
        agent_id: str = "",
    ) -> str:
        """Atomically add and/or remove tags on an existing memory entry."""
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        return json.dumps(
            memory_service.memory_update_tags(
                s,
                _pid(),
                eff_aid,
                key=key,
                add=add,
                remove=remove,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_entries_by_tag(
        tag: str,
        tier: str = "",
        agent_id: str = "",
    ) -> str:
        """Return all memory entries that carry a specific tag."""
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        return json.dumps(
            memory_service.memory_entries_by_tag(
                s,
                _pid(),
                eff_aid,
                tag=tag,
                tier=tier,
            )
        )
