"""MCP server exposing tapps-brain via Model Context Protocol.

Uses FastMCP to expose MemoryStore operations as MCP tools, resources,
and prompts over stdio transport. Requires the ``mcp`` optional extra.

Entry point: ``tapps-brain-mcp`` (see pyproject.toml).
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import structlog

# Silence structlog for server mode — MCP uses stdin/stdout for protocol.
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL),
)

logger = structlog.get_logger(__name__)

_MAX_CONFIDENCE_BOOST: float = 0.2  # Maximum allowed confidence_boost for memory_reinforce


def _mcp_parse_details_json(details_json: str | None) -> tuple[dict[str, Any] | None, str | None]:
    """Parse optional JSON object for MCP *details_json* parameters.

    Returns:
        ``(dict, None)`` on success, or ``(None, error_message)`` on failure.
    """
    if details_json is None or not str(details_json).strip():
        return {}, None
    try:
        data = json.loads(details_json)
    except json.JSONDecodeError as exc:
        return None, f"invalid_details_json: {exc}"
    if not isinstance(data, dict):
        return None, "details_json must be a JSON object"
    return data, None


def _lazy_import_mcp() -> Any:  # noqa: ANN401
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


def _get_store(
    project_dir: Path,
    *,
    enable_hive: bool = True,
    agent_id: str = "unknown",
) -> Any:  # noqa: ANN401
    """Open a MemoryStore for the given project directory.

    When *enable_hive* is ``True``, a shared :class:`HiveStore` is
    created and wired into the store together with *agent_id*.
    """
    from tapps_brain.store import MemoryStore

    hive_store = None
    if enable_hive:
        from tapps_brain.hive import HiveStore

        hive_store = HiveStore()

    agent_id_for_store = agent_id if agent_id != "unknown" else None
    return MemoryStore(
        project_dir,
        agent_id=agent_id_for_store,
        hive_store=hive_store,
        hive_agent_id=agent_id,
    )


def create_server(  # noqa: PLR0915
    project_dir: Path | None = None,
    *,
    enable_hive: bool = True,
    agent_id: str = "unknown",
) -> Any:  # noqa: ANN401
    """Create and configure a FastMCP server instance.

    Args:
        project_dir: Project root directory. Defaults to cwd.
        enable_hive: When ``True``, create a shared ``HiveStore`` and
            wire it into the ``MemoryStore``.
        agent_id: Agent identifier passed to the store as
            ``hive_agent_id``.

    Returns:
        A configured FastMCP server instance.
    """
    fastmcp_cls = _lazy_import_mcp()

    resolved_dir = _resolve_project_dir(str(project_dir) if project_dir else None)
    store = _get_store(resolved_dir, enable_hive=enable_hive, agent_id=agent_id)

    mcp = fastmcp_cls(
        "tapps-brain",
        instructions=(
            "tapps-brain is a persistent cross-session memory system. "
            "Use memory tools to save, retrieve, search, and manage "
            "knowledge across coding sessions.\n\n"
            "## Hive (multi-agent memory sharing)\n\n"
            "When Hive is enabled, memories can be shared across agents "
            "using the `agent_scope` parameter on `memory_save`:\n\n"
            "- **private** (default): Only visible to the saving agent. "
            "Use for scratch notes, intermediate reasoning, and "
            "agent-specific context.\n"
            "- **domain**: Visible to all agents sharing the same memory "
            "profile (e.g., all 'repo-brain' agents). Use for conventions, "
            "patterns, and role-specific knowledge.\n"
            "- **hive**: Visible to ALL agents in the Hive regardless of "
            "profile. Use for cross-cutting facts: tech stack decisions, "
            "project architecture, API contracts, and team agreements.\n"
            "- **group:<name>**: Hive namespace *name* for members of that group "
            "(distinct from project-local `group` on saves). Requires Hive group "
            "membership.\n\n"
            "Recall automatically merges local and Hive results. Use "
            "`hive_status` to see registered agents and namespaces, "
            "`hive_search` to query the shared store directly, "
            "`hive_propagate` to manually share an existing local memory, "
            "and `hive_write_revision` / `hive_wait_write` to poll for new "
            "Hive memory writes (lightweight pub-sub)."
        ),
    )

    # ------------------------------------------------------------------
    # Simplified Agent Brain tools (EPIC-057)
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_remember(
        fact: str,
        tier: str = "procedural",
        share: bool = False,
        share_with: str = "",
    ) -> str:
        """Save a memory to the agent's brain.

        Use tier='architectural' for lasting decisions, 'pattern' for conventions,
        'procedural' for how-to knowledge. Set share=True to share with all groups,
        or share_with='hive' for org-wide.
        """
        from tapps_brain.agent_brain import _content_key

        key = _content_key(fact)
        agent_scope = "private"
        if share:
            agent_scope = "group"
        elif share_with == "hive":
            agent_scope = "hive"
        elif share_with:
            agent_scope = f"group:{share_with}"
        result = store.save(key=key, value=fact, tier=tier, agent_scope=agent_scope)
        if isinstance(result, dict) and "error" in result:
            return json.dumps(result)
        return json.dumps({"saved": True, "key": key})

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_recall(query: str, max_results: int = 5) -> str:
        """Recall memories matching a query.

        Searches local agent memory, group knowledge, and org-wide expert knowledge.
        """
        entries = store.search(query)
        results = []
        for entry in entries[:max_results]:
            if isinstance(entry, dict):
                results.append(entry)
            else:
                results.append(
                    {
                        "key": entry.key,
                        "value": entry.value,
                        "tier": str(entry.tier),
                        "confidence": entry.confidence,
                        "tags": list(entry.tags) if entry.tags else [],
                    }
                )
        return json.dumps(results, default=str)

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_forget(key: str) -> str:
        """Archive a memory by key. The memory is not permanently deleted."""
        entry = store.get(key)
        if entry is None:
            return json.dumps({"forgotten": False, "reason": "not_found"})
        store.delete(key)
        return json.dumps({"forgotten": True, "key": key})

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_learn_success(task_description: str, task_id: str = "") -> str:
        """Record a successful task outcome.

        Saves the experience and reinforces any recently recalled memories.
        """
        from tapps_brain.agent_brain import _content_key

        key = _content_key(f"success-{task_description}")
        tags = ["success"]
        if task_id:
            tags.append(f"task:{task_id}")
        store.save(key=key, value=task_description, tier="procedural", tags=tags)
        return json.dumps({"learned": True, "key": key})

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_learn_failure(description: str, task_id: str = "", error: str = "") -> str:
        """Record a failed task outcome to avoid repeating mistakes."""
        from tapps_brain.agent_brain import _content_key

        key = _content_key(f"failure-{description}")
        value = f"{description}\n\nError: {error}" if error else description
        tags = ["failure"]
        if task_id:
            tags.append(f"task:{task_id}")
        store.save(key=key, value=value, tier="procedural", tags=tags)
        return json.dumps({"learned": True, "key": key})

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_status() -> str:
        """Show agent identity, group memberships, store stats, and Hive connectivity."""
        status = {
            "agent_id": getattr(store, "agent_id", None),
            "groups": getattr(store, "groups", []),
            "expert_domains": getattr(store, "expert_domains", []),
            "memory_count": len(store.list_all()),
            "hive_connected": store._hive_store is not None,
        }
        return json.dumps(status, default=str)

    # ------------------------------------------------------------------
    # Tools — model-controlled operations
    # ------------------------------------------------------------------

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
    ) -> str:
        """Save or update a memory entry.

        Args:
            key: Unique identifier for the memory.
            value: Memory content text.
            tier: Memory tier — one of the built-in tiers (architectural, pattern,
                procedural, context) or a layer name defined in the active profile
                (e.g. identity, long-term, short-term, ephemeral for personal-assistant).
                Valid values depend on the active profile; check memory_health for the
                current profile name and its layer names.
            source: Source — one of: human, agent, inferred, system.
            tags: Optional tags for categorization.
            scope: Visibility scope — one of: project, branch, session.
            confidence: Confidence score (0.0-1.0, or -1.0 for auto).
            agent_scope: Hive propagation scope — private, domain, hive, or
                group:<name> (cross-agent group; requires membership in that Hive group).
                Use 'private' (default) for agent-specific notes and reasoning.
                Use 'domain' to share with agents using the same profile
                (e.g., coding conventions shared among all repo-brain agents).
                Use 'hive' to share with ALL agents (e.g., tech stack decisions,
                API contracts, architectural choices).
            source_agent: Agent that produced this memory. Falls back to
                server's --agent-id when empty.
            group: Optional project-local memory partition (GitHub #49). Omit to
                keep an existing entry's group on update; use empty string to
                clear; set to a short label (e.g. team-a) to assign. Not a Hive
                namespace — see memory_groups guide vs agent_scope.
        """
        from tapps_brain.agent_scope import (
            agent_scope_valid_values_for_errors,
            normalize_agent_scope,
        )
        from tapps_brain.memory_group import MEMORY_GROUP_UNSET
        from tapps_brain.models import MemoryTier
        from tapps_brain.tier_normalize import normalize_save_tier

        try:
            agent_scope = normalize_agent_scope(agent_scope)
        except ValueError as exc:
            return json.dumps(
                {
                    "error": "invalid_agent_scope",
                    "message": str(exc),
                    "valid_values": agent_scope_valid_values_for_errors(),
                }
            )

        tier = normalize_save_tier(tier, store.profile)

        # Validate tier against profile layers (or default enum values when no profile)
        _valid_tiers: frozenset[str] = (
            frozenset(store.profile.layer_names)
            if store.profile is not None
            else frozenset(m.value for m in MemoryTier)
        )
        if tier not in _valid_tiers:
            _sorted_valid = sorted(_valid_tiers)
            return json.dumps(
                {
                    "error": "invalid_tier",
                    "message": f"Invalid tier {tier!r}. Valid values: {_sorted_valid}",
                    "valid_values": _sorted_valid,
                }
            )
        _valid_sources = ("human", "agent", "inferred", "system")
        if source not in _valid_sources:
            return json.dumps(
                {
                    "error": "invalid_source",
                    "message": f"Invalid source {source!r}. Valid values: {list(_valid_sources)}",
                    "valid_values": list(_valid_sources),
                }
            )
        resolved_agent = source_agent if source_agent else agent_id
        memory_group_arg: object = MEMORY_GROUP_UNSET if group is None else group
        result = store.save(
            key=key,
            value=value,
            tier=tier,
            source=source,
            tags=tags,
            scope=scope,
            confidence=confidence,
            agent_scope=agent_scope,
            source_agent=resolved_agent,
            memory_group=memory_group_arg,
        )
        if isinstance(result, dict):
            # Error from safety check or write rules
            return json.dumps(result)
        return json.dumps(
            {
                "status": "saved",
                "key": result.key,
                "tier": str(result.tier),
                "confidence": result.confidence,
                "memory_group": result.memory_group,
            }
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_get(key: str) -> str:
        """Retrieve a single memory entry by key.

        Args:
            key: The memory entry key to retrieve.
        """
        entry = store.get(key)
        if entry is None:
            return json.dumps({"error": "not_found", "key": key})
        return json.dumps(entry.model_dump(mode="json"))

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_delete(key: str) -> str:
        """Delete a memory entry by key.

        Args:
            key: The memory entry key to delete.
        """
        deleted = store.delete(key)
        return json.dumps({"deleted": deleted, "key": key})

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
    ) -> str:
        """Search memory entries using full-text search.

        Args:
            query: Search query text.
            tier: Optional tier filter (architectural, pattern, procedural, context).
            scope: Optional scope filter (project, branch, session).
            as_of: Optional ISO-8601 timestamp for point-in-time query.
            group: Optional project-local memory group filter (GitHub #49).
            since: Lower bound on time_field. ISO-8601 string or relative shorthand
                (e.g. "7d" = last 7 days, "2w" = last 2 weeks, "1m" = last month).
            until: Upper bound on time_field. Same format as since.
            time_field: Column to filter on: created_at (default), updated_at,
                or last_accessed (Issue #70).
        """
        if as_of is not None:
            try:
                from datetime import datetime

                datetime.fromisoformat(as_of.replace("Z", "+00:00"))
            except ValueError:
                return json.dumps(
                    {
                        "error": "invalid_as_of",
                        "message": f"as_of must be a valid ISO-8601 timestamp, got {as_of!r}",
                    }
                )
        results = store.search(
            query,
            tier=tier,
            scope=scope,
            as_of=as_of,
            memory_group=group,
            since=since.strip() or None,
            until=until.strip() or None,
            time_field=time_field,
        )
        return json.dumps(
            [
                {
                    "key": e.key,
                    "value": e.value,
                    "tier": str(e.tier),
                    "confidence": e.confidence,
                    "tags": e.tags,
                    "memory_group": e.memory_group,
                }
                for e in results
            ]
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_list(
        tier: str | None = None,
        scope: str | None = None,
        include_superseded: bool = False,
        group: str | None = None,
    ) -> str:
        """List memory entries with optional filters.

        Args:
            tier: Optional tier filter.
            scope: Optional scope filter.
            include_superseded: Whether to include superseded entries.
            group: Optional project-local memory group filter (GitHub #49).
        """
        entries = store.list_all(
            tier=tier,
            scope=scope,
            include_superseded=include_superseded,
            memory_group=group,
        )
        return json.dumps(
            [
                {
                    "key": e.key,
                    "value": e.value[:200],
                    "tier": str(e.tier),
                    "confidence": e.confidence,
                    "tags": e.tags,
                    "scope": e.scope.value,
                    "memory_group": e.memory_group,
                }
                for e in entries
            ]
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_list_groups() -> str:
        """List distinct project-local memory group names (GitHub #49).

        Returns sorted labels used with the ``group`` parameter on save/search/list/recall.
        """
        return json.dumps(store.list_memory_groups())

    # ------------------------------------------------------------------
    # Lifecycle tools — recall, reinforce, ingest, supersede, history
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_recall(message: str, group: str | None = None) -> str:
        """Run auto-recall for a message and return ranked memories.

        Searches the memory store for entries relevant to the given message,
        returning a formatted memory section suitable for prompt injection.

        Args:
            message: The user/agent message to match against stored memories.
            group: Optional project-local group — restricts local retrieval; Hive
                results are still merged (GitHub #49).
        """
        result = store.recall(message, memory_group=group)
        payload: dict[str, Any] = {
            "memory_section": result.memory_section,
            "memory_count": result.memory_count,
            "token_count": result.token_count,
            "recall_time_ms": result.recall_time_ms,
            "truncated": result.truncated,
            "memories": result.memories,
        }
        if result.recall_diagnostics is not None:
            payload["recall_diagnostics"] = result.recall_diagnostics.model_dump(mode="json")
        if result.quality_warning:
            payload["quality_warning"] = result.quality_warning
        return json.dumps(payload)

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_reinforce(key: str, confidence_boost: float = 0.0) -> str:
        """Reinforce a memory entry, boosting its confidence and resetting decay.

        Call this when a memory proved useful during a session to keep it
        fresh and increase its ranking in future recalls.

        Args:
            key: The memory entry key to reinforce.
            confidence_boost: Confidence increase (0.0-0.2). Defaults to 0.0.
        """
        if not (0.0 <= confidence_boost <= _MAX_CONFIDENCE_BOOST):
            return json.dumps(
                {
                    "error": "invalid_confidence_boost",
                    "message": (
                        f"confidence_boost must be in [0.0, {_MAX_CONFIDENCE_BOOST}],"
                        f" got {confidence_boost}"
                    ),
                }
            )
        try:
            entry = store.reinforce(key, confidence_boost=confidence_boost)
        except KeyError:
            return json.dumps({"error": "not_found", "key": key})
        return json.dumps(
            {
                "status": "reinforced",
                "key": entry.key,
                "confidence": entry.confidence,
                "access_count": entry.access_count,
            }
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_ingest(
        context: str,
        source: str = "agent",
        agent_scope: str = "private",
    ) -> str:
        """Extract and store durable facts from conversation context.

        Scans the given text for decision-like statements and saves them
        as new memory entries. Existing keys are skipped.

        Args:
            context: Raw session/transcript text to scan for facts.
            source: Source attribution — one of: human, agent, inferred, system.
            agent_scope: Hive propagation scope for extracted facts — one of:
                'private' (default), 'domain', 'hive', or 'group:<name>'.
        """
        from tapps_brain.agent_scope import normalize_agent_scope

        try:
            agent_scope = normalize_agent_scope(agent_scope)
        except ValueError as exc:
            return json.dumps({"error": "invalid_agent_scope", "message": str(exc)})

        created_keys = store.ingest_context(context, source=source, agent_scope=agent_scope)
        return json.dumps(
            {
                "status": "ingested",
                "created_keys": created_keys,
                "count": len(created_keys),
            }
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_supersede(
        old_key: str,
        new_value: str,
        key: str | None = None,
        tier: str | None = None,
        tags: list[str] | None = None,
    ) -> str:
        """Create a new version of a memory, superseding the old one.

        The old entry is marked with invalid_at and superseded_by.
        A new entry is created with valid_at set to now.

        Args:
            old_key: Key of the existing entry to supersede.
            new_value: Value for the replacement entry.
            key: Optional explicit key for the new entry (auto-generated if omitted).
            tier: Optional tier override for the new entry.
            tags: Optional tags override for the new entry.
        """
        kwargs: dict[str, Any] = {}
        if key is not None:
            kwargs["key"] = key
        if tier is not None:
            kwargs["tier"] = tier
        if tags is not None:
            kwargs["tags"] = tags
        try:
            entry = store.supersede(old_key, new_value, **kwargs)
        except KeyError:
            return json.dumps({"error": "not_found", "key": old_key})
        except ValueError as exc:
            return json.dumps({"error": "already_superseded", "message": str(exc)})
        return json.dumps(
            {
                "status": "superseded",
                "old_key": old_key,
                "new_key": entry.key,
                "tier": str(entry.tier),
                "confidence": entry.confidence,
            }
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_history(key: str) -> str:
        """Show the full version chain for a memory key.

        Follows the superseded_by chain forward and backward to return
        all versions ordered by valid_at.

        Args:
            key: Any key in the version chain.
        """
        try:
            chain = store.history(key)
        except KeyError:
            return json.dumps({"error": "not_found", "key": key})
        if not chain:
            return json.dumps({"error": "not_found", "key": key})
        return json.dumps(
            [
                {
                    "key": e.key,
                    "value": e.value[:200],
                    "tier": str(e.tier),
                    "confidence": e.confidence,
                    "valid_at": e.valid_at,
                    "invalid_at": e.invalid_at,
                    "superseded_by": e.superseded_by,
                }
                for e in chain
            ]
        )

    # ------------------------------------------------------------------
    # Session tools — index, search, capture
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_index_session(
        session_id: str,
        chunks: list[str],
    ) -> str:
        """Index session chunks for future search.

        Save a session summary (as a list of text chunks) so it can be
        searched later with memory_search_sessions.

        Args:
            session_id: Session identifier (e.g. conversation or task ID).
            chunks: List of text chunks — summaries or key facts from the session.
        """
        stored = store.index_session(session_id, chunks)
        return json.dumps(
            {
                "status": "indexed",
                "session_id": session_id,
                "chunks_stored": stored,
            }
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_search_sessions(
        query: str,
        limit: int = 10,
    ) -> str:
        """Search past session summaries.

        Returns matching chunks from previously indexed sessions,
        ranked by relevance.

        Args:
            query: Search query text.
            limit: Maximum number of results to return (default 10).
        """
        results = store.search_sessions(query, limit=limit)
        return json.dumps(
            {
                "results": results,
                "count": len(results),
            }
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_capture(
        response: str,
        source: str = "agent",
        agent_scope: str = "private",
    ) -> str:
        """Extract and persist new facts from an agent response.

        Scans the given response text for decision-like statements and
        saves them as new memory entries. Use this at the end of a session
        to capture what happened.

        Args:
            response: The agent's response text to scan for facts.
            source: Source attribution — one of: human, agent, inferred, system.
            agent_scope: Hive propagation scope — 'private' (default),
                'domain', 'hive', or 'group:<name>' (Hive group namespace).
                Set to 'hive' to share architectural decisions with all agents.
        """
        from tapps_brain.agent_scope import normalize_agent_scope
        from tapps_brain.recall import RecallOrchestrator

        try:
            agent_scope = normalize_agent_scope(agent_scope)
        except ValueError as exc:
            return json.dumps({"error": "invalid_agent_scope", "message": str(exc)})

        orchestrator = RecallOrchestrator(store)
        created_keys = orchestrator.capture(response, source=source, agent_scope=agent_scope)
        return json.dumps(
            {
                "status": "captured",
                "created_keys": created_keys,
                "count": len(created_keys),
            }
        )

    # ------------------------------------------------------------------
    # Feedback tools (EPIC-029 / STORY-029.4)
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def feedback_rate(
        entry_key: str,
        rating: str = "helpful",
        session_id: str = "",
        details_json: str = "",
    ) -> str:
        """Rate a recalled memory entry (creates ``recall_rated`` event).

        Args:
            entry_key: Memory key that was recalled.
            rating: One of: helpful, partial, irrelevant, outdated.
            session_id: Optional session identifier.
            details_json: Optional JSON object string with extra metadata.
        """
        details, err = _mcp_parse_details_json(details_json)
        if err is not None:
            return json.dumps({"error": "parse_error", "message": err})
        try:
            event = store.rate_recall(
                entry_key,
                rating=rating,
                session_id=session_id.strip() or None,
                details=details if details else None,
            )
        except ValueError as exc:
            return json.dumps({"error": "validation_error", "message": str(exc)})
        return json.dumps({"status": "recorded", "event": event.model_dump(mode="json")})

    @mcp.tool()  # type: ignore[untyped-decorator]
    def feedback_gap(
        query: str,
        session_id: str = "",
        details_json: str = "",
    ) -> str:
        """Report a knowledge gap (``gap_reported`` event).

        Args:
            query: Query or topic that was not well served.
            session_id: Optional session identifier.
            details_json: Optional JSON object merged into event details.
        """
        details, err = _mcp_parse_details_json(details_json)
        if err is not None:
            return json.dumps({"error": "parse_error", "message": err})
        event = store.report_gap(
            query,
            session_id=session_id.strip() or None,
            details=details if details else None,
        )
        return json.dumps({"status": "recorded", "event": event.model_dump(mode="json")})

    @mcp.tool()  # type: ignore[untyped-decorator]
    def feedback_issue(
        entry_key: str,
        issue: str,
        session_id: str = "",
        details_json: str = "",
    ) -> str:
        """Flag a quality issue with a memory entry (``issue_flagged``).

        Args:
            entry_key: Affected memory key.
            issue: Human-readable issue description.
            session_id: Optional session identifier.
            details_json: Optional JSON object merged into event details.
        """
        details, err = _mcp_parse_details_json(details_json)
        if err is not None:
            return json.dumps({"error": "parse_error", "message": err})
        event = store.report_issue(
            entry_key,
            issue,
            session_id=session_id.strip() or None,
            details=details if details else None,
        )
        return json.dumps({"status": "recorded", "event": event.model_dump(mode="json")})

    @mcp.tool()  # type: ignore[untyped-decorator]
    def feedback_record(
        event_type: str,
        entry_key: str = "",
        session_id: str = "",
        utility_score: float | None = None,
        details_json: str = "",
    ) -> str:
        """Record a generic feedback event (built-in or custom type).

        Args:
            event_type: Object-Action snake_case name (e.g. ``deploy_completed``).
            entry_key: Optional related memory key.
            session_id: Optional session identifier.
            utility_score: Optional score in [-1.0, 1.0].
            details_json: Optional JSON object for extra metadata.
        """
        details, err = _mcp_parse_details_json(details_json)
        if err is not None:
            return json.dumps({"error": "parse_error", "message": err})
        try:
            event = store.record_feedback(
                event_type,
                entry_key=entry_key.strip() or None,
                session_id=session_id.strip() or None,
                utility_score=utility_score,
                details=details if details else None,
            )
        except ValueError as exc:
            return json.dumps({"error": "validation_error", "message": str(exc)})
        return json.dumps({"status": "recorded", "event": event.model_dump(mode="json")})

    @mcp.tool()  # type: ignore[untyped-decorator]
    def feedback_query(
        event_type: str = "",
        entry_key: str = "",
        session_id: str = "",
        since: str = "",
        until: str = "",
        limit: int = 100,
    ) -> str:
        """Query recorded feedback events with optional filters.

        Args:
            event_type: Filter by exact type, or empty for all.
            entry_key: Filter by memory key, or empty for any.
            session_id: Filter by session, or empty for any.
            since: ISO-8601 inclusive lower bound, or empty.
            until: ISO-8601 inclusive upper bound, or empty.
            limit: Max rows (default 100).
        """
        events = store.query_feedback(
            event_type=event_type.strip() or None,
            entry_key=entry_key.strip() or None,
            session_id=session_id.strip() or None,
            since=since.strip() or None,
            until=until.strip() or None,
            limit=limit,
        )
        return json.dumps(
            {
                "events": [e.model_dump(mode="json") for e in events],
                "count": len(events),
            }
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def diagnostics_report(
        record_history: bool = True,
    ) -> str:
        """Run quality diagnostics (EPIC-030): composite score, dimensions, circuit state."""
        rep = store.diagnostics(record_history=record_history)
        return json.dumps(rep.model_dump(mode="json"))

    @mcp.tool()  # type: ignore[untyped-decorator]
    def diagnostics_history(
        limit: int = 50,
    ) -> str:
        """Return recent persisted diagnostics snapshots."""
        rows = store.diagnostics_history(limit=limit)
        return json.dumps({"records": rows, "count": len(rows)})

    @mcp.tool()  # type: ignore[untyped-decorator]
    def flywheel_process(since: str = "") -> str:
        """Run feedback → confidence pipeline (EPIC-031)."""
        from tapps_brain.flywheel import FeedbackProcessor, FlywheelConfig

        res = FeedbackProcessor(FlywheelConfig()).process_feedback(
            store,
            since=since.strip() or None,
        )
        return json.dumps(res)

    @mcp.tool()  # type: ignore[untyped-decorator]
    def flywheel_gaps(limit: int = 10, semantic: bool = False) -> str:
        """Return top knowledge gaps as JSON."""
        gaps = store.knowledge_gaps(limit=limit, semantic=semantic)
        return json.dumps({"gaps": [g.model_dump(mode="json") for g in gaps], "count": len(gaps)})

    @mcp.tool()  # type: ignore[untyped-decorator]
    def flywheel_report(period_days: int = 7) -> str:
        """Generate quality report (markdown + structured summary)."""
        rep = store.generate_report(period_days=period_days)
        return json.dumps(
            {
                "rendered_text": rep.rendered_text,
                "structured_data": rep.structured_data,
            }
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def flywheel_evaluate(suite_path: str, k: int = 5) -> str:
        """Run BEIR-format directory or YAML suite evaluation."""
        from tapps_brain.evaluation import EvalSuite, evaluate

        p = Path(suite_path).expanduser().resolve()
        if not p.exists():
            return json.dumps({"error": "not_found", "path": str(p)})
        if p.is_dir():
            suite = EvalSuite.load_beir_dir(p)
        elif p.suffix.lower() in (".yaml", ".yml"):
            suite = EvalSuite.load_yaml(p)
        else:
            return json.dumps({"error": "invalid_suite", "message": "Expected directory or YAML"})
        report = evaluate(store, suite, k=k)
        return json.dumps(report.model_dump(mode="json"))

    @mcp.tool()  # type: ignore[untyped-decorator]
    def flywheel_hive_feedback(threshold: int = 3) -> str:
        """Aggregate / apply Hive cross-project feedback penalties."""
        from tapps_brain.flywheel import aggregate_hive_feedback, process_hive_feedback

        hs = getattr(store, "_hive_store", None)
        agg = aggregate_hive_feedback(hs)
        proc = process_hive_feedback(hs, threshold=threshold)
        return json.dumps(
            {
                "aggregate": None if agg is None else agg.model_dump(mode="json"),
                "process": proc,
            }
        )

    # ------------------------------------------------------------------
    # Resources — read-only store views
    # ------------------------------------------------------------------

    @mcp.resource("memory://stats")  # type: ignore[untyped-decorator]
    def stats_resource() -> str:
        """Store stats: counts, tiers, schema, package/profile, optional profile_seed_version."""
        snap = store.snapshot()
        schema_ver = store.get_schema_version()
        h = store.health()
        return json.dumps(
            {
                "project_root": str(snap.project_root),
                "total_entries": snap.total_count,
                "max_entries": store._max_entries,
                "max_entries_per_group": store._max_entries_per_group,
                "schema_version": schema_ver,
                "package_version": h.package_version,
                "profile_name": h.profile_name,
                "profile_seed_version": h.profile_seed_version,
                "tier_distribution": snap.tier_counts,
            }
        )

    @mcp.resource("memory://agent-contract")  # type: ignore[untyped-decorator]
    def agent_contract_resource() -> str:
        """Agent integration snapshot: versions, profile, tiers, recall empty codes."""
        from tapps_brain.models import MemoryTier
        from tapps_brain.recall_diagnostics import (
            RECALL_EMPTY_BELOW_SCORE_THRESHOLD,
            RECALL_EMPTY_ENGAGEMENT_LOW,
            RECALL_EMPTY_GROUP_EMPTY,
            RECALL_EMPTY_NO_RANKED_MATCHES,
            RECALL_EMPTY_POST_FILTER,
            RECALL_EMPTY_RAG_BLOCKED,
            RECALL_EMPTY_SEARCH_FAILED,
            RECALL_EMPTY_STORE_EMPTY,
        )

        h = store.health()
        prof = store.profile
        layers = list(prof.layer_names) if prof is not None else []
        return json.dumps(
            {
                "package_version": h.package_version,
                "schema_version": h.schema_version,
                "profile_name": h.profile_name,
                "profile_layer_names": layers,
                "canonical_memory_tiers": [m.value for m in MemoryTier],
                "recall_empty_reason_codes": sorted(
                    {
                        RECALL_EMPTY_ENGAGEMENT_LOW,
                        RECALL_EMPTY_SEARCH_FAILED,
                        RECALL_EMPTY_STORE_EMPTY,
                        RECALL_EMPTY_GROUP_EMPTY,
                        RECALL_EMPTY_NO_RANKED_MATCHES,
                        RECALL_EMPTY_BELOW_SCORE_THRESHOLD,
                        RECALL_EMPTY_RAG_BLOCKED,
                        RECALL_EMPTY_POST_FILTER,
                    }
                ),
                "write_path_mcp": "memory_save",
                "write_path_cli": "tapps-brain memory save KEY VALUE [options]",
                "read_paths_mcp": ["memory_search", "memory_recall", "memory_list", "memory_get"],
                "operator_docs": "https://github.com/wtthornton/tapps-brain/tree/main/docs/guides",
            },
            indent=2,
        )

    @mcp.resource("memory://health")  # type: ignore[untyped-decorator]
    def health_resource() -> str:
        """Store health report."""
        report = store.health()
        return json.dumps(report.model_dump(mode="json"))

    @mcp.resource("memory://entries/{key}")  # type: ignore[untyped-decorator]
    def entry_resource(key: str) -> str:
        """Full detail view of a single memory entry."""
        entry = store.get(key)
        if entry is None:
            return json.dumps({"error": "not_found", "key": key})
        return json.dumps(entry.model_dump(mode="json"))

    @mcp.resource("memory://metrics")  # type: ignore[untyped-decorator]
    def metrics_resource() -> str:
        """Operation metrics: counters and latency histograms."""
        snapshot = store.get_metrics()
        return json.dumps(snapshot.to_dict())

    @mcp.resource("memory://feedback")  # type: ignore[untyped-decorator]
    def feedback_resource() -> str:
        """Recent feedback events (up to 500), newest-friendly order by query default."""
        events = store.query_feedback(limit=500)
        return json.dumps(
            {
                "events": [e.model_dump(mode="json") for e in events],
                "count": len(events),
            }
        )

    @mcp.resource("memory://diagnostics")  # type: ignore[untyped-decorator]
    def diagnostics_resource() -> str:
        """Latest diagnostics report (does not append history by default)."""
        rep = store.diagnostics(record_history=False)
        return json.dumps(rep.model_dump(mode="json"))

    @mcp.resource("memory://report")  # type: ignore[untyped-decorator]
    def report_resource() -> str:
        """Latest flywheel quality report summary (from last ``generate_report``)."""
        latest = store.latest_quality_report()
        if latest is None:
            rep = store.generate_report(period_days=7)
            payload = rep.structured_data
        else:
            payload = latest.get("structured_data", latest)
        return json.dumps(payload)

    # ------------------------------------------------------------------
    # Prompts — user-invoked workflow templates (STORY-008.6)
    # ------------------------------------------------------------------

    @mcp.prompt()  # type: ignore[untyped-decorator]
    def recall(topic: str) -> list[dict[str, str]]:
        """What do you remember about a topic?

        Runs auto-recall against the memory store and returns relevant memories
        formatted for the AI assistant to review and discuss.

        Args:
            topic: The topic or question to recall memories about.
        """
        result = store.recall(topic)
        if result.memory_count == 0:
            body = f"No memories found about: {topic}"
        else:
            body = (
                f'Here are {result.memory_count} memories about "{topic}":\n\n'
                f"{result.memory_section}"
            )
        return [{"role": "user", "content": body}]

    @mcp.prompt()  # type: ignore[untyped-decorator]
    def store_summary() -> list[dict[str, str]]:
        """Generate a summary of what's in the memory store.

        Returns store statistics, tier distribution, and a sample of recent
        entries so the AI assistant can give the user an overview.
        """
        snap = store.snapshot()
        schema_ver = store.get_schema_version()
        entries = store.list_all()

        lines = [
            f"Memory store summary for: {snap.project_root}",
            f"Total entries: {snap.total_count} / 500",
            f"Schema version: {schema_ver}",
            f"Tier distribution: {json.dumps(snap.tier_counts)}",
            "",
        ]
        preview_len = 80
        if entries:
            lines.append("Recent entries (up to 10):")
            for entry in entries[:10]:
                truncated = entry.value[:preview_len]
                suffix = "…" if len(entry.value) > preview_len else ""
                lines.append(f"  - [{entry.tier!s}] {entry.key}: {truncated}{suffix}")
        else:
            lines.append("The store is empty.")

        return [{"role": "user", "content": "\n".join(lines)}]

    @mcp.prompt()  # type: ignore[untyped-decorator]
    def remember(fact: str) -> list[dict[str, str]]:
        """Remember a fact by saving it to the memory store.

        Guides the AI assistant to save a memory with an appropriate tier
        and tags based on the content of the fact.

        Args:
            fact: The fact, decision, or piece of knowledge to remember.
        """
        body = (
            f"The user wants you to remember the following:\n\n"
            f'"{fact}"\n\n'
            "Please save this to the memory store using the memory_save tool. "
            "Choose an appropriate:\n"
            "- **key**: a short, descriptive kebab-case identifier\n"
            "- **tier**: one of architectural (system-level decisions), "
            "pattern (coding patterns/conventions), "
            "procedural (workflows/processes), "
            "or context (session-specific facts)\n"
            "- **tags**: relevant category tags\n"
            "- **confidence**: 0.7-0.9 for stated facts, 0.5-0.7 for inferences\n\n"
            "Confirm what you saved back to the user."
        )
        return [{"role": "user", "content": body}]

    # ------------------------------------------------------------------
    # Federation tools (STORY-008.5)
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def federation_status() -> str:
        """Show federation hub status: registered projects and subscriptions.

        Returns hub statistics, project list, and active subscriptions.
        """
        from tapps_brain.federation import (
            FederatedStore,
            federated_hub_db_path,
            load_federation_config,
        )

        config = load_federation_config()
        try:
            hub = FederatedStore(db_path=federated_hub_db_path(config))
            try:
                stats = hub.get_stats()
            finally:
                hub.close()
        except Exception:
            logger.warning("federation_hub_unavailable")
            stats = {"error": "hub_unavailable"}

        return json.dumps(
            {
                "projects": [p.model_dump(mode="json") for p in config.projects],
                "subscriptions": [s.model_dump(mode="json") for s in config.subscriptions],
                "hub_stats": stats,
            }
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def federation_subscribe(
        project_id: str,
        sources: list[str] | None = None,
        tag_filter: list[str] | None = None,
        min_confidence: float = 0.5,
    ) -> str:
        """Subscribe a project to receive memories from other federated projects.

        The project must be registered first. If sources is empty, subscribes
        to all other projects.

        Args:
            project_id: The project ID to subscribe.
            sources: Optional list of source project IDs (empty = all).
            tag_filter: Optional tag filter — only import memories with these tags.
            min_confidence: Minimum confidence threshold (0.0-1.0, default 0.5).
        """
        from tapps_brain.federation import add_subscription, register_project

        # Auto-register if not already registered
        register_project(project_id, str(resolved_dir))

        try:
            add_subscription(
                subscriber=project_id,
                sources=sources,
                tag_filter=tag_filter,
                min_confidence=min_confidence,
            )
        except ValueError as exc:
            return json.dumps({"error": str(exc)})

        return json.dumps(
            {
                "status": "subscribed",
                "project_id": project_id,
                "sources": sources or ["all"],
            }
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def federation_unsubscribe(project_id: str) -> str:
        """Remove a project's federation subscription.

        Args:
            project_id: The project ID to unsubscribe.
        """
        from tapps_brain.federation import load_federation_config, save_federation_config

        config = load_federation_config()
        before = len(config.subscriptions)
        config.subscriptions = [s for s in config.subscriptions if s.subscriber != project_id]
        removed = before - len(config.subscriptions)
        save_federation_config(config)

        return json.dumps(
            {
                "status": "unsubscribed",
                "project_id": project_id,
                "subscriptions_removed": removed,
            }
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def federation_publish(
        project_id: str,
        keys: list[str] | None = None,
    ) -> str:
        """Publish shared-scope memories to the federation hub.

        Only memories with scope='shared' are published. If keys are specified,
        only those entries are published.

        Args:
            project_id: This project's federation identifier.
            keys: Optional list of specific keys to publish (default: all shared).
        """
        from tapps_brain.federation import (
            FederatedStore,
            federated_hub_db_path,
            register_project,
            sync_to_hub,
        )

        register_project(project_id, str(resolved_dir))
        hub = FederatedStore(db_path=federated_hub_db_path())
        try:
            result = sync_to_hub(
                store=store,
                federated_store=hub,
                project_id=project_id,
                project_root=str(resolved_dir),
                keys=keys,
            )
        finally:
            hub.close()

        return json.dumps({"status": "published", **result})

    # ------------------------------------------------------------------
    # Maintenance tools (STORY-008.5)
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def maintenance_consolidate(
        threshold: float = 0.7,
        min_group_size: int = 3,
        force: bool = True,
    ) -> str:
        """Trigger memory consolidation to merge similar entries.

        Scans the store for groups of similar memories and merges them
        into consolidated entries.

        Args:
            threshold: Similarity threshold for grouping (0.0-1.0, default 0.7).
            min_group_size: Minimum entries per group to consolidate (default 3).
            force: If True, run regardless of last scan time (default True).
        """
        from tapps_brain.auto_consolidation import run_periodic_consolidation_scan

        result = run_periodic_consolidation_scan(
            store=store,
            project_root=resolved_dir,
            threshold=threshold,
            min_group_size=min_group_size,
            force=force,
        )
        return json.dumps(result.to_dict())

    @mcp.tool()  # type: ignore[untyped-decorator]
    def maintenance_gc(dry_run: bool = False) -> str:
        """Run garbage collection to archive stale memories.

        Identifies memories that have decayed below usefulness and archives
        them. Archived entries are removed from the active store and appended
        to ``archive.jsonl`` under the store memory directory (same as CLI).

        Args:
            dry_run: If True, only identify candidates without archiving (default False).
        """
        raw = store.gc(dry_run=dry_run)
        payload = raw.model_dump(mode="json")
        if dry_run:
            payload["dry_run"] = True
            payload["candidates"] = len(raw.archived_keys)
            payload["candidate_keys"] = raw.archived_keys
        else:
            payload["dry_run"] = False
        return json.dumps(payload)

    @mcp.tool()  # type: ignore[untyped-decorator]
    def maintenance_stale() -> str:
        """List GC stale memory candidates with reasons (read-only; GitHub #21).

        Returns JSON with ``count`` and ``entries`` (each entry includes ``key``,
        ``tier``, ``reasons``, ``effective_confidence``, ``stored_confidence``,
        ``scope``, and optional diagnostic fields).
        """
        details = store.list_gc_stale_details()
        return json.dumps(
            {
                "count": len(details),
                "entries": [d.model_dump(mode="json") for d in details],
            }
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def tapps_brain_health(check_hive: bool = True) -> str:
        """Return a structured health report for tapps-brain (issue #15).

        Runs all health checks — store connectivity, hive status, and
        integrity verification — and returns a single machine-readable
        JSON report.

        The ``status`` field is one of:
        - ``"ok"``    — all green
        - ``"warn"``  — degraded but functional (check ``warnings`` list)
        - ``"error"`` — action required (check ``errors`` list)

        Use this tool in monitoring cron jobs instead of chaining multiple
        commands. A healthy instance returns ``{"status": "ok", ...}``.

        Args:
            check_hive: Whether to include Hive connectivity in the report
                        (default True). Set to False for faster checks.
        """
        try:
            from tapps_brain.health_check import run_health_check

            root = getattr(store, "_project_root", None)
            report = run_health_check(
                project_root=root,
                check_hive=check_hive,
                store=store,
            )
            return json.dumps(report.model_dump(mode="json"))
        except Exception as exc:
            import traceback

            return json.dumps(
                {
                    "status": "error",
                    "errors": [str(exc)],
                    "warnings": [],
                    "traceback": traceback.format_exc(),
                }
            )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_gc_config() -> str:
        """Return the current garbage collection configuration.

        Returns the active GC thresholds: floor_retention_days,
        session_expiry_days, and contradicted_threshold.
        """
        gc_cfg = store.get_gc_config()
        return json.dumps(gc_cfg.to_dict())

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_gc_config_set(
        floor_retention_days: int | None = None,
        session_expiry_days: int | None = None,
        contradicted_threshold: float | None = None,
    ) -> str:
        """Update garbage collection configuration thresholds.

        Only provided parameters are updated; omitted parameters keep their
        current values.

        Args:
            floor_retention_days: Days a memory stays at floor confidence before
                archival (default 30).
            session_expiry_days: Days after session end before session-scoped
                memories are archived (default 7).
            contradicted_threshold: Confidence threshold below which contradicted
                memories are archived (default 0.2).
        """
        from tapps_brain.gc import GCConfig

        current = store.get_gc_config()
        new_cfg = GCConfig(
            floor_retention_days=(
                floor_retention_days
                if floor_retention_days is not None
                else current.floor_retention_days
            ),
            session_expiry_days=(
                session_expiry_days
                if session_expiry_days is not None
                else current.session_expiry_days
            ),
            contradicted_threshold=(
                contradicted_threshold
                if contradicted_threshold is not None
                else current.contradicted_threshold
            ),
        )
        store.set_gc_config(new_cfg)
        return json.dumps({"status": "updated", **new_cfg.to_dict()})

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_consolidation_config() -> str:
        """Return the current auto-consolidation configuration.

        Returns the active consolidation settings: enabled, threshold, and
        min_entries.
        """
        cfg = store.get_consolidation_config()
        return json.dumps(cfg.to_dict())

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_consolidation_config_set(
        enabled: bool | None = None,
        threshold: float | None = None,
        min_entries: int | None = None,
    ) -> str:
        """Update auto-consolidation configuration.

        Only provided parameters are updated; omitted parameters keep their
        current values.

        Args:
            enabled: Whether auto-consolidation runs on save (default False).
            threshold: Similarity threshold for merging entries (default 0.7).
            min_entries: Minimum entries required before consolidation runs
                (default 3).
        """
        from tapps_brain.store import ConsolidationConfig

        current = store.get_consolidation_config()
        new_cfg = ConsolidationConfig(
            enabled=enabled if enabled is not None else current.enabled,
            threshold=threshold if threshold is not None else current.threshold,
            min_entries=min_entries if min_entries is not None else current.min_entries,
        )
        store.set_consolidation_config(new_cfg)
        return json.dumps({"status": "updated", **new_cfg.to_dict()})

    # ------------------------------------------------------------------
    # Export / Import tools (STORY-008.5)
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_export(
        tier: str | None = None,
        scope: str | None = None,
        min_confidence: float | None = None,
    ) -> str:
        """Export memory entries as JSON.

        Returns a JSON string containing all matching entries. Use filters
        to export a subset.

        Args:
            tier: Optional tier filter (architectural, pattern, procedural, context).
            scope: Optional scope filter (project, branch, session).
            min_confidence: Optional minimum confidence threshold.
        """
        entries = store.list_all(tier=tier, scope=scope)

        if min_confidence is not None:
            entries = [e for e in entries if e.confidence >= min_confidence]

        return json.dumps(
            {
                "memories": [e.model_dump(mode="json") for e in entries],
                "entry_count": len(entries),
                "project_root": str(resolved_dir),
            }
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_import(
        memories_json: str,
        overwrite: bool = False,
    ) -> str:
        """Import memory entries from a JSON string.

        Expects a JSON string with a 'memories' array. Each memory object
        should have at least 'key' and 'value' fields.

        Args:
            memories_json: JSON string with a 'memories' array of entry objects.
            overwrite: If True, overwrite existing keys (default False: skip).
        """
        try:
            data = json.loads(memories_json)
        except json.JSONDecodeError as exc:
            return json.dumps({"error": "invalid_json", "message": str(exc)})

        if not isinstance(data, dict) or "memories" not in data:
            return json.dumps(
                {"error": "invalid_format", "message": "Expected {'memories': [...]}"}
            )

        memories = data["memories"]
        if not isinstance(memories, list):
            return json.dumps({"error": "invalid_format", "message": "'memories' must be a list"})

        imported = 0
        skipped = 0
        errors = 0

        for mem in memories:
            if not isinstance(mem, dict) or "key" not in mem or "value" not in mem:
                errors += 1
                continue

            key = mem["key"]
            existing = store.get(key)
            if existing is not None and not overwrite:
                skipped += 1
                continue

            try:
                result = store.save(
                    key=key,
                    value=mem["value"],
                    tier=mem.get("tier", "pattern"),
                    source=mem.get("source", "system"),
                    tags=mem.get("tags"),
                    scope=mem.get("scope", "project"),
                )
            except ValueError as exc:
                logger.warning("memory_import_save_error", key=key, error=str(exc))
                errors += 1
                continue
            if isinstance(result, dict):
                errors += 1
            else:
                imported += 1

        return json.dumps(
            {
                "status": "imported",
                "imported": imported,
                "skipped": skipped,
                "errors": errors,
            }
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def tapps_brain_relay_export(source_agent: str, items_json: str) -> str:
        """Build a memory relay JSON payload for cross-node handoff (GitHub #19).

        Sub-agents without a local store pass the returned string to the primary
        for ``tapps-brain relay import``.

        Args:
            source_agent: Identifier for the sending agent (e.g. builder-agent).
            items_json: JSON array of item objects. Each object should include at
                least ``key`` and ``value``. Optional: ``tier``, ``scope`` (memory
                scope or agent_scope disambiguated per docs/guides/memory-relay.md),
                ``visibility``, ``agent_scope``, ``tags``, ``source``, ``confidence``,
                ``branch``, ``memory_group`` or ``group`` (project-local partition).
        """
        from tapps_brain.memory_relay import RELAY_VERSION, build_relay_json

        try:
            parsed = json.loads(items_json)
        except json.JSONDecodeError as exc:
            return json.dumps({"error": "invalid_json", "message": str(exc)})

        if not isinstance(parsed, list):
            return json.dumps(
                {"error": "invalid_format", "message": "items_json must be a JSON array"}
            )

        for i, row in enumerate(parsed):
            if not isinstance(row, dict):
                return json.dumps(
                    {
                        "error": "invalid_item",
                        "message": f"items[{i}] must be an object",
                    }
                )

        payload = build_relay_json(
            source_agent=source_agent,
            items=parsed,
            relay_version=RELAY_VERSION,
        )
        return json.dumps(
            {
                "relay_version": RELAY_VERSION,
                "payload": payload,
                "item_count": len(parsed),
            }
        )

    # ------------------------------------------------------------------
    # Profile tools (EPIC-010)
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def profile_info() -> str:
        """Return the active profile name, layers, and scoring config."""
        profile = store.profile
        if profile is None:
            return json.dumps({"error": "no_profile", "message": "No profile loaded."})

        return json.dumps(
            {
                "name": profile.name,
                "description": profile.description,
                "version": profile.version,
                "layers": [
                    {
                        "name": la.name,
                        "half_life_days": la.half_life_days,
                        "decay_model": la.decay_model,
                        "confidence_floor": la.confidence_floor,
                    }
                    for la in profile.layers
                ],
                "scoring": {
                    "relevance": profile.scoring.relevance,
                    "confidence": profile.scoring.confidence,
                    "recency": profile.scoring.recency,
                    "frequency": profile.scoring.frequency,
                },
            }
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_profile_onboarding() -> str:
        """Return Markdown onboarding guidance for the active memory profile (GitHub #45).

        Summarizes tiers, scoring weights, recall defaults, limits, Hive hints, and
        operational conventions so agents can use tapps-brain consistently.
        """
        profile = store.profile
        if profile is None:
            return json.dumps({"error": "no_profile", "message": "No profile loaded."})
        from tapps_brain.onboarding import render_agent_onboarding

        return json.dumps({"format": "markdown", "content": render_agent_onboarding(profile)})

    @mcp.tool()  # type: ignore[untyped-decorator]
    def profile_switch(name: str) -> str:
        """Switch to a different built-in memory profile.

        Args:
            name: Name of the built-in profile (e.g. 'personal-assistant').
        """
        try:
            from tapps_brain.profile import get_builtin_profile

            profile = get_builtin_profile(name)
            # Note: This only takes effect for future operations within this session.
            # For permanent change, use the CLI: tapps-brain profile set <name>
            store._profile = profile
            return json.dumps(
                {
                    "switched": True,
                    "profile": profile.name,
                    "layer_count": len(profile.layers),
                }
            )
        except FileNotFoundError:
            from tapps_brain.profile import list_builtin_profiles

            return json.dumps(
                {
                    "error": "profile_not_found",
                    "message": f"No built-in profile '{name}'.",
                    "available": list_builtin_profiles(),
                }
            )
        except Exception as exc:
            logger.exception("profile_switch_error", profile=name)
            return json.dumps({"error": "profile_switch_error", "message": str(exc)})

    # ------------------------------------------------------------------
    # Hive tools (EPIC-011)
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def hive_status() -> str:
        """Return Hive status: namespaces, entry counts, and registered agents.

        Use this to discover what other agents exist, which profiles they
        use, and how many shared memories are in each namespace.
        """
        try:
            from tapps_brain.hive import AgentRegistry, HiveStore

            shared = getattr(store, "_hive_store", None)
            _should_close = shared is None
            hive: HiveStore = shared if shared is not None else HiveStore()
            try:
                ns_counts = hive.count_by_namespace()
                agent_counts = hive.count_by_agent()

                registry = AgentRegistry()
                agents = [
                    {
                        "id": a.id,
                        "profile": a.profile,
                        "skills": a.skills,
                        # Count entries contributed by this agent across all namespaces.
                        # Fix for issue #22: previously used ns_counts.get(a.profile, 0)
                        # which always returned 0 because entries are saved to "universal"
                        # or a domain namespace, not a namespace named after the agent ID.
                        "entries_contributed": agent_counts.get(a.id, 0),
                    }
                    for a in registry.list_agents()
                ]
            finally:
                if _should_close:
                    hive.close()
            return json.dumps(
                {
                    "namespaces": ns_counts,
                    "total_entries": sum(ns_counts.values()),
                    "agents": agents,
                }
            )
        except Exception as exc:
            logger.exception("hive_tool_error", tool="hive_status")
            return json.dumps({"error": "hive_error", "message": str(exc)})

    @mcp.tool()  # type: ignore[untyped-decorator]
    def hive_search(query: str, namespace: str | None = None) -> str:
        """Search the shared Hive for memories from other agents.

        The Hive contains memories saved with agent_scope 'domain' or 'hive'.
        Use this to find knowledge shared by other agents — architectural
        decisions, conventions, or cross-cutting facts.

        Args:
            query: Full-text search query.
            namespace: Optional namespace filter. Namespaces correspond to
                profile names (e.g. 'repo-brain', 'code-review') for
                domain-scoped memories, or 'universal' for hive-scoped ones.
        """
        try:
            from tapps_brain.hive import HiveStore

            shared = getattr(store, "_hive_store", None)
            _should_close = shared is None
            hive: HiveStore = shared if shared is not None else HiveStore()
            try:
                ns_list = [namespace] if namespace else None
                results = hive.search(query, namespaces=ns_list, limit=20)
            finally:
                if _should_close:
                    hive.close()
            return json.dumps({"results": results, "count": len(results)})
        except Exception as exc:
            logger.exception("hive_tool_error", tool="hive_search")
            return json.dumps({"error": "hive_error", "message": str(exc)})

    @mcp.tool()  # type: ignore[untyped-decorator]
    def hive_propagate(
        key: str,
        agent_scope: str = "hive",
        force: bool = False,
        dry_run: bool = False,
    ) -> str:
        """Manually propagate a local memory to the Hive shared store.

        Use this to share an existing local memory with other agents after
        saving it. Memories with 'domain' scope are visible to agents using
        the same profile; 'hive' scope makes them visible to all agents.

        Args:
            key: Key of the local memory to propagate.
            agent_scope: Propagation scope — 'domain', 'hive' (default),
                or 'group:<name>' (Hive group; requires membership).
            force: When True, ignore profile private_tiers / auto_propagate rules
                so *agent_scope* always applies (GitHub #18).
            dry_run: When True, do not write to Hive; report target namespace only.
        """
        entry = store.get(key)
        if entry is None:
            return json.dumps({"error": "not_found", "message": f"Key '{key}' not found."})

        try:
            from tapps_brain.agent_scope import (
                agent_scope_valid_values_for_errors,
                normalize_agent_scope,
            )
            from tapps_brain.hive import HiveStore, PropagationEngine

            try:
                agent_scope = normalize_agent_scope(agent_scope)
            except ValueError as exc:
                return json.dumps(
                    {
                        "error": "invalid_agent_scope",
                        "message": str(exc),
                        "valid_values": agent_scope_valid_values_for_errors(),
                    }
                )

            shared = getattr(store, "_hive_store", None)
            _should_close = shared is None
            hive: HiveStore = shared if shared is not None else HiveStore()
            agent_id = getattr(store, "_hive_agent_id", "mcp-user")
            profile_name = "repo-brain"
            auto_propagate: list[str] | None = None
            private_tiers: list[str] | None = None
            if store.profile is not None:
                profile_name = getattr(store.profile, "name", "repo-brain")
                hc = getattr(store.profile, "hive", None)
                if hc is not None:
                    auto_propagate = hc.auto_propagate_tiers
                    private_tiers = hc.private_tiers

            tier_val = entry.tier.value if hasattr(entry.tier, "value") else str(entry.tier)
            try:
                result = PropagationEngine.propagate(
                    key=entry.key,
                    value=entry.value,
                    agent_scope=agent_scope,
                    agent_id=agent_id,
                    agent_profile=profile_name,
                    tier=tier_val,
                    confidence=entry.confidence,
                    source=entry.source.value,
                    tags=entry.tags,
                    hive_store=hive,
                    auto_propagate_tiers=auto_propagate,
                    private_tiers=private_tiers,
                    bypass_profile_hive_rules=force,
                    dry_run=dry_run,
                )
            finally:
                if _should_close:
                    hive.close()
            if result is None:
                return json.dumps({"propagated": False, "reason": "scope is private"})
            return json.dumps({"propagated": True, **result})
        except Exception as exc:
            logger.exception("hive_tool_error", tool="hive_propagate")
            return json.dumps({"error": "hive_error", "message": str(exc)})

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
        """Batch-promote local project memories to the Hive (GitHub #18).

        Select entries with *push_all*, comma-separated *tags* / *keys*, or
        *tier*. *keys* wins if non-empty (other filters ignored). Otherwise
        require *push_all* or at least one of *tags* / *tier*.

        Args:
            agent_scope: ``domain``, ``hive``, or ``group:<name>`` (not ``private``).
            push_all: Include all non-superseded entries (optionally narrowed by
                *tier* / *tags*).
            tags: Comma-separated tags; entry matches if it has any listed tag.
            tier: Filter by memory tier (e.g. architectural).
            keys: Comma-separated local keys to push.
            dry_run: Preview without writing to Hive.
            force: Ignore profile private_tiers / auto_propagate rules.
        """
        try:
            from tapps_brain.agent_scope import (
                agent_scope_valid_values_for_errors,
                normalize_agent_scope,
            )
            from tapps_brain.hive import (
                HiveStore,
                push_memory_entries_to_hive,
                select_local_entries_for_hive_push,
            )

            try:
                agent_scope = normalize_agent_scope(agent_scope)
            except ValueError as exc:
                return json.dumps(
                    {
                        "error": "invalid_agent_scope",
                        "message": str(exc),
                        "valid_values": agent_scope_valid_values_for_errors(),
                    }
                )
            if agent_scope == "private":
                return json.dumps(
                    {
                        "error": "invalid_agent_scope",
                        "message": "hive_push requires domain, hive, or group:<name>.",
                        "valid_values": agent_scope_valid_values_for_errors(),
                    }
                )

            key_list = [k.strip() for k in keys.split(",") if k.strip()]
            tag_list = [t.strip() for t in tags.split(",") if t.strip()] or None
            try:
                entries = select_local_entries_for_hive_push(
                    store,
                    push_all=push_all,
                    tags=tag_list,
                    tier=tier,
                    keys=key_list or None,
                    include_superseded=False,
                )
            except ValueError as ve:
                return json.dumps({"error": "invalid_args", "message": str(ve)})

            shared = getattr(store, "_hive_store", None)
            _should_close = shared is None
            hive: HiveStore = shared if shared is not None else HiveStore()
            agent_id = getattr(store, "_hive_agent_id", "mcp-user")
            profile_name = "repo-brain"
            auto_propagate: list[str] | None = None
            private_tiers: list[str] | None = None
            if store.profile is not None:
                profile_name = getattr(store.profile, "name", "repo-brain")
                hc = getattr(store.profile, "hive", None)
                if hc is not None:
                    auto_propagate = hc.auto_propagate_tiers
                    private_tiers = hc.private_tiers

            try:
                report = push_memory_entries_to_hive(
                    entries,
                    hive_store=hive,
                    agent_id=agent_id,
                    agent_profile=profile_name,
                    agent_scope=agent_scope,
                    auto_propagate_tiers=auto_propagate,
                    private_tiers=private_tiers,
                    bypass_profile_hive_rules=force,
                    dry_run=dry_run,
                )
            finally:
                if _should_close:
                    hive.close()
            return json.dumps(report)
        except Exception as exc:
            logger.exception("hive_tool_error", tool="hive_push")
            return json.dumps({"error": "hive_error", "message": str(exc)})

    @mcp.tool()  # type: ignore[untyped-decorator]
    def hive_write_revision() -> str:
        """Return the Hive write notification revision (GitHub #12).

        Each successful save to ``hive_memories`` (and confidence patch)
        increments a monotonic integer. Agents poll this to detect new shared
        memories without scanning the full store. A sidecar file
        ``~/.tapps-brain/hive/.hive_write_notify`` is updated for shell
        watchers.

        Returns:
            JSON with ``revision`` (int) and ``updated_at`` (ISO timestamp).
        """
        try:
            from tapps_brain.hive import HiveStore

            shared = getattr(store, "_hive_store", None)
            _should_close = shared is None
            hive: HiveStore = shared if shared is not None else HiveStore()
            try:
                state = hive.get_write_notify_state()
            finally:
                if _should_close:
                    hive.close()
            return json.dumps(state)
        except Exception as exc:
            logger.exception("hive_tool_error", tool="hive_write_revision")
            return json.dumps({"error": "hive_error", "message": str(exc)})

    @mcp.tool()  # type: ignore[untyped-decorator]
    def hive_wait_write(since_revision: int = 0, timeout_seconds: float = 10.0) -> str:
        """Wait until the Hive write revision exceeds *since_revision* or timeout.

        Lightweight long-poll for near-real-time awareness of new Hive
        memories. *timeout_seconds* is capped at 60.

        Args:
            since_revision: Return immediately if current revision is already
                greater than this value.
            timeout_seconds: Max seconds to block (default 10, max 60).

        Returns:
            JSON with ``revision``, ``updated_at``, ``changed`` (bool), and
            ``timed_out`` (bool).
        """
        try:
            from tapps_brain.hive import HiveStore

            shared = getattr(store, "_hive_store", None)
            _should_close = shared is None
            hive: HiveStore = shared if shared is not None else HiveStore()
            try:
                cap = min(60.0, max(0.0, float(timeout_seconds)))
                state = hive.get_write_notify_state()
                if state["revision"] > since_revision:
                    return json.dumps({**state, "changed": True, "timed_out": False})
                result = hive.wait_for_write_notify(
                    since_revision=since_revision,
                    timeout_sec=cap,
                    poll_interval_sec=0.25,
                )
            finally:
                if _should_close:
                    hive.close()
            return json.dumps(result)
        except Exception as exc:
            logger.exception("hive_tool_error", tool="hive_wait_write")
            return json.dumps({"error": "hive_error", "message": str(exc)})

    @mcp.tool()  # type: ignore[untyped-decorator]
    def agent_register(
        agent_id: str,
        profile: str = "repo-brain",
        skills: str = "",
    ) -> str:
        """Register an agent in the Hive.

        Args:
            agent_id: Unique agent identifier.
            profile: Memory profile name (determines domain namespace).
            skills: Comma-separated list of skills.
        """
        if not agent_id or not agent_id.strip():
            return json.dumps(
                {"error": "invalid_agent_id", "message": "agent_id must not be empty"}
            )
        try:
            from tapps_brain.hive import AgentRegistration, AgentRegistry

            registry = AgentRegistry()
            skill_list = [s.strip() for s in skills.split(",") if s.strip()]
            agent = AgentRegistration(id=agent_id, profile=profile, skills=skill_list)
            registry.register(agent)
            return json.dumps(
                {
                    "registered": True,
                    "agent_id": agent_id,
                    "profile": profile,
                    "skills": skill_list,
                }
            )
        except Exception as exc:
            logger.exception("hive_tool_error", tool="agent_register")
            return json.dumps({"error": "registry_error", "message": str(exc)})

    @mcp.tool()  # type: ignore[untyped-decorator]
    def agent_create(
        agent_id: str,
        profile: str = "repo-brain",
        skills: str = "",
    ) -> str:
        """Create an agent: register in the Hive with a validated profile.

        Combines agent registration with profile validation. Returns
        namespace assignment and profile summary on success. Returns an
        error with available profiles listed when the profile is invalid.

        Args:
            agent_id: Unique agent identifier (slug).
            profile: Memory profile name (must be a valid built-in or project profile).
            skills: Comma-separated list of skills.
        """
        if not agent_id or not agent_id.strip():
            return json.dumps(
                {"error": "invalid_agent_id", "message": "agent_id must not be empty"}
            )
        try:
            from tapps_brain.hive import AgentRegistration, AgentRegistry
            from tapps_brain.profile import get_builtin_profile, list_builtin_profiles

            # Validate profile exists
            try:
                prof = get_builtin_profile(profile)
            except FileNotFoundError:
                available = list_builtin_profiles()
                return json.dumps(
                    {
                        "error": "invalid_profile",
                        "message": f"Profile '{profile}' not found.",
                        "available_profiles": available,
                    }
                )

            # Register agent
            skill_list = [s.strip() for s in skills.split(",") if s.strip()]
            agent = AgentRegistration(id=agent_id, profile=profile, skills=skill_list)
            registry = AgentRegistry()
            registry.register(agent)

            # Derive namespace (same logic as PropagationEngine)
            namespace = profile

            # Build profile summary
            layer_names = [layer.name for layer in prof.layers]
            profile_summary = {
                "name": prof.name,
                "version": prof.version,
                "layers": layer_names,
                "description": prof.description,
            }

            return json.dumps(
                {
                    "created": True,
                    "agent_id": agent_id,
                    "profile": profile,
                    "namespace": namespace,
                    "skills": skill_list,
                    "profile_summary": profile_summary,
                }
            )
        except Exception as exc:
            logger.exception("hive_tool_error", tool="agent_create")
            return json.dumps({"error": "agent_create_error", "message": str(exc)})

    @mcp.tool()  # type: ignore[untyped-decorator]
    def agent_list() -> str:
        """List all registered agents in the Hive."""
        try:
            from tapps_brain.hive import AgentRegistry

            registry = AgentRegistry()
            agents = [a.model_dump(mode="json") for a in registry.list_agents()]
            return json.dumps({"agents": agents, "count": len(agents)})
        except Exception as exc:
            logger.exception("hive_tool_error", tool="agent_list")
            return json.dumps({"error": "registry_error", "message": str(exc)})

    @mcp.tool()  # type: ignore[untyped-decorator]
    def agent_delete(agent_id: str) -> str:
        """Delete a registered agent from the Hive.

        Args:
            agent_id: Unique agent identifier to remove.
        """
        try:
            from tapps_brain.hive import AgentRegistry

            registry = AgentRegistry()
            removed = registry.unregister(agent_id)
            if removed:
                return json.dumps({"deleted": True, "agent_id": agent_id})
            return json.dumps(
                {
                    "deleted": False,
                    "agent_id": agent_id,
                    "message": f"Agent '{agent_id}' not found.",
                }
            )
        except Exception as exc:
            logger.exception("hive_tool_error", tool="agent_delete")
            return json.dumps({"error": "registry_error", "message": str(exc)})

    # ------------------------------------------------------------------
    # Knowledge graph tools (EPIC-015)
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_relations(key: str) -> str:
        """Return all relations associated with a memory entry key.

        Args:
            key: The memory entry key to look up relations for.
        """
        relations = store.get_relations(key)
        return json.dumps({"key": key, "relations": relations, "count": len(relations)})

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_relations_get_batch(keys_json: str) -> str:
        """Return relations for multiple memory keys in one call (STORY-048.2).

        Args:
            keys_json: JSON array of memory entry keys, e.g. '["key1","key2"]'.
        """
        try:
            keys = json.loads(keys_json)
        except (json.JSONDecodeError, ValueError) as exc:
            return json.dumps({"error": "invalid_keys_json", "message": str(exc)})
        if not isinstance(keys, list):
            return json.dumps(
                {"error": "invalid_keys_json", "message": "Expected a JSON array of strings."}
            )
        results = store.get_relations_batch([str(k) for k in keys])
        total = sum(len(v) for v in results.values())
        return json.dumps({"results": results, "total_count": total})

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_find_related(key: str, max_hops: int = 2) -> str:
        """Find entries related to a key via BFS traversal of the relation graph.

        Args:
            key: Starting entry key.
            max_hops: Maximum traversal depth (default 2, must be >= 1).
        """
        if max_hops < 1:
            return json.dumps({"error": "invalid_max_hops", "message": "max_hops must be >= 1"})
        try:
            results = store.find_related(key, max_hops=max_hops)
            return json.dumps(
                {
                    "key": key,
                    "max_hops": max_hops,
                    "related": [{"key": k, "hops": h} for k, h in results],
                    "count": len(results),
                }
            )
        except KeyError:
            return json.dumps({"error": "not_found", "message": f"Entry '{key}' not found."})

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_query_relations(
        subject: str = "",
        predicate: str = "",
        object_entity: str = "",
    ) -> str:
        """Filter relations by subject, predicate, and/or object_entity.

        All filters use case-insensitive matching and are combined with AND logic.
        Omit a field (or pass empty string) to skip that filter.

        Args:
            subject: Filter by subject entity (optional).
            predicate: Filter by predicate/relationship type (optional).
            object_entity: Filter by object entity (optional).
        """
        matches = store.query_relations(
            subject=subject or None,
            predicate=predicate or None,
            object_entity=object_entity or None,
        )
        return json.dumps({"relations": matches, "count": len(matches)})

    # ------------------------------------------------------------------
    # Audit trail tools (EPIC-015)
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_audit(
        key: str = "",
        event_type: str = "",
        since: str = "",
        until: str = "",
        limit: int = 50,
    ) -> str:
        """Query the audit trail for memory events.

        Returns a JSON array of matching audit events from the append-only
        JSONL audit log. All filters are optional and combined with AND logic.

        Args:
            key: Filter by memory entry key (optional).
            event_type: Filter by action, e.g. "save", "delete",
                "consolidation_merge", "consolidation_source",
                "consolidation_merge_undo" (optional).
            since: ISO-8601 lower bound, inclusive (optional).
            until: ISO-8601 upper bound, inclusive (optional).
            limit: Maximum number of events to return (default 50, must be >= 1).
        """
        if limit < 1:
            return json.dumps({"error": "invalid_limit", "message": "limit must be >= 1"})
        entries = store.audit(
            key=key or None,
            event_type=event_type or None,
            since=since or None,
            until=until or None,
            limit=limit,
        )
        return json.dumps(
            {
                "events": [e.model_dump(mode="json") for e in entries],
                "count": len(entries),
            }
        )

    # ------------------------------------------------------------------
    # Tag management tools (EPIC-015)
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_list_tags() -> str:
        """List all tags used in the memory store with their usage counts.

        Returns a JSON object with a ``tags`` list (each item has ``tag`` and
        ``count`` fields) sorted by count descending, and a ``total`` field
        with the number of distinct tags.
        """
        counts = store.list_tags()
        tags_list = sorted(
            [{"tag": t, "count": c} for t, c in counts.items()],
            key=lambda x: (-x["count"], x["tag"]),
        )
        return json.dumps({"tags": tags_list, "total": len(tags_list)})

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_update_tags(
        key: str,
        add: list[str] | None = None,
        remove: list[str] | None = None,
    ) -> str:
        """Atomically add and/or remove tags on an existing memory entry.

        Tags are deduplicated. The operation respects the 10-tag maximum.
        Removing a non-existent tag is a no-op. Adding an already-present
        tag is a no-op.

        Args:
            key: The memory entry key to update.
            add: List of tags to add (optional).
            remove: List of tags to remove (optional).
        """
        result = store.update_tags(key, add=add, remove=remove)
        if isinstance(result, dict):
            return json.dumps(result)
        return json.dumps(
            {
                "status": "updated",
                "key": result.key,
                "tags": result.tags,
            }
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_entries_by_tag(
        tag: str,
        tier: str = "",
    ) -> str:
        """Return all memory entries that carry a specific tag.

        Args:
            tag: The tag to filter by.
            tier: Optional tier filter (architectural, pattern, procedural, context).
                  Pass empty string to skip tier filtering.
        """
        entries = store.entries_by_tag(tag, tier=tier or None)
        return json.dumps(
            {
                "tag": tag,
                "entries": [
                    {
                        "key": e.key,
                        "value": e.value,
                        "tier": str(e.tier),
                        "confidence": e.confidence,
                        "tags": e.tags,
                    }
                    for e in entries
                ],
                "count": len(entries),
            }
        )

    # ------------------------------------------------------------------
    # Session end tool (Issue #17 — episodic memory capture)
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def tapps_brain_session_end(
        summary: str,
        tags: list[str] | None = None,
        daily_note: bool = False,
    ) -> str:
        """Record an end-of-session episodic memory entry.

        Saves a short-term episodic memory tagged with ``date``,
        ``session``, and ``episodic``.  Call this at the end of a
        session to preserve a concise record of what happened.

        Args:
            summary: Concise summary of session work, decisions, and outcomes.
            tags: Optional extra tags to attach alongside the default
                ``date``, ``session``, and ``episodic`` tags.
            daily_note: When ``True``, also appends the summary to today's
                ``memory/YYYY-MM-DD.md`` daily note in the project root.

        Returns:
            JSON object with ``key``, ``status``, ``tags``, ``tier``, and
            ``scope`` on success, or an ``error`` field on failure.
        """
        from tapps_brain.session_summary import session_summary_save

        result = session_summary_save(
            summary,
            tags=tags,
            project_dir=resolved_dir,
            workspace_dir=resolved_dir,
            daily_note=daily_note,
            source_agent=agent_id,
        )
        return json.dumps(result, default=str)

    # ------------------------------------------------------------------
    # Attach store and Hive metadata to server for testing / tool access
    # ------------------------------------------------------------------
    mcp._tapps_store = store
    mcp._tapps_agent_id = agent_id
    mcp._tapps_hive_enabled = enable_hive
    # Expose the shared HiveStore (if any) so Hive tools can reuse it
    mcp._tapps_hive_store = getattr(store, "_hive_store", None)

    return mcp


def main() -> None:
    """Entry point for ``tapps-brain-mcp`` command."""
    try:
        pkg_ver = importlib.metadata.version("tapps-brain")
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover
        pkg_ver = "0.0.0-dev"
    parser = argparse.ArgumentParser(
        prog="tapps-brain-mcp",
        description=(
            "Run the tapps-brain MCP server (stdio transport). "
            "Version matches the installed tapps-brain package."
        ),
    )
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
    args = parser.parse_args()

    effective_agent_id = args.agent_id
    if effective_agent_id == "unknown":
        effective_agent_id = os.environ.get("TAPPS_BRAIN_AGENT_ID", "unknown")

    project_dir = Path(args.project_dir) if args.project_dir else None
    server = create_server(
        project_dir,
        enable_hive=args.enable_hive,
        agent_id=effective_agent_id,
    )
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
