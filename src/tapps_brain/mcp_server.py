"""MCP server exposing tapps-brain via Model Context Protocol.

Uses FastMCP to expose MemoryStore operations as MCP tools, resources,
and prompts over stdio transport. Requires the ``mcp`` optional extra.

Entry point: ``tapps-brain-mcp`` (see pyproject.toml).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

import structlog

# Silence structlog for server mode — MCP uses stdin/stdout for protocol.
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL),
)


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


def _get_store(project_dir: Path) -> Any:  # noqa: ANN401
    """Open a MemoryStore for the given project directory."""
    from tapps_brain.store import MemoryStore

    return MemoryStore(project_dir)


def create_server(project_dir: Path | None = None) -> Any:  # noqa: ANN401, PLR0915
    """Create and configure a FastMCP server instance.

    Args:
        project_dir: Project root directory. Defaults to cwd.

    Returns:
        A configured FastMCP server instance.
    """
    fastmcp_cls = _lazy_import_mcp()

    resolved_dir = _resolve_project_dir(str(project_dir) if project_dir else None)
    store = _get_store(resolved_dir)

    mcp = fastmcp_cls(
        "tapps-brain",
        instructions=(
            "tapps-brain is a persistent cross-session memory system. "
            "Use memory tools to save, retrieve, search, and manage "
            "knowledge across coding sessions."
        ),
    )

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
    ) -> str:
        """Save or update a memory entry.

        Args:
            key: Unique identifier for the memory.
            value: Memory content text.
            tier: Memory tier — one of: architectural, pattern, procedural, context.
            source: Source — one of: human, agent, inferred, system.
            tags: Optional tags for categorization.
            scope: Visibility scope — one of: project, branch, session.
            confidence: Confidence score (0.0-1.0, or -1.0 for auto).
        """
        result = store.save(
            key=key,
            value=value,
            tier=tier,
            source=source,
            tags=tags,
            scope=scope,
            confidence=confidence,
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
    ) -> str:
        """Search memory entries using full-text search.

        Args:
            query: Search query text.
            tier: Optional tier filter (architectural, pattern, procedural, context).
            scope: Optional scope filter (project, branch, session).
            as_of: Optional ISO-8601 timestamp for point-in-time query.
        """
        results = store.search(query, tier=tier, scope=scope, as_of=as_of)
        return json.dumps(
            [
                {
                    "key": e.key,
                    "value": e.value,
                    "tier": str(e.tier),
                    "confidence": e.confidence,
                    "tags": e.tags,
                }
                for e in results
            ]
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_list(
        tier: str | None = None,
        scope: str | None = None,
        include_superseded: bool = False,
    ) -> str:
        """List memory entries with optional filters.

        Args:
            tier: Optional tier filter.
            scope: Optional scope filter.
            include_superseded: Whether to include superseded entries.
        """
        entries = store.list_all(
            tier=tier,
            scope=scope,
            include_superseded=include_superseded,
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
                }
                for e in entries
            ]
        )

    # ------------------------------------------------------------------
    # Lifecycle tools — recall, reinforce, ingest, supersede, history
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_recall(message: str) -> str:
        """Run auto-recall for a message and return ranked memories.

        Searches the memory store for entries relevant to the given message,
        returning a formatted memory section suitable for prompt injection.

        Args:
            message: The user/agent message to match against stored memories.
        """
        result = store.recall(message)
        return json.dumps(
            {
                "memory_section": result.memory_section,
                "memory_count": result.memory_count,
                "token_count": result.token_count,
                "recall_time_ms": result.recall_time_ms,
                "truncated": result.truncated,
                "memories": result.memories,
            }
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_reinforce(key: str, confidence_boost: float = 0.0) -> str:
        """Reinforce a memory entry, boosting its confidence and resetting decay.

        Call this when a memory proved useful during a session to keep it
        fresh and increase its ranking in future recalls.

        Args:
            key: The memory entry key to reinforce.
            confidence_boost: Confidence increase (0.0-0.2). Defaults to 0.0.
        """
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
    def memory_ingest(context: str, source: str = "agent") -> str:
        """Extract and store durable facts from conversation context.

        Scans the given text for decision-like statements and saves them
        as new memory entries. Existing keys are skipped.

        Args:
            context: Raw session/transcript text to scan for facts.
            source: Source attribution — one of: human, agent, inferred, system.
        """
        created_keys = store.ingest_context(context, source=source)
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
    ) -> str:
        """Extract and persist new facts from an agent response.

        Scans the given response text for decision-like statements and
        saves them as new memory entries. Use this at the end of a session
        to capture what happened.

        Args:
            response: The agent's response text to scan for facts.
            source: Source attribution — one of: human, agent, inferred, system.
        """
        from tapps_brain.recall import RecallOrchestrator

        orchestrator = RecallOrchestrator(store)
        created_keys = orchestrator.capture(response, source=source)
        return json.dumps(
            {
                "status": "captured",
                "created_keys": created_keys,
                "count": len(created_keys),
            }
        )

    # ------------------------------------------------------------------
    # Resources — read-only store views
    # ------------------------------------------------------------------

    @mcp.resource("memory://stats")  # type: ignore[untyped-decorator]
    def stats_resource() -> str:
        """Store statistics: entry count, tier distribution, schema version."""
        snap = store.snapshot()
        schema_ver = store._persistence.get_schema_version()
        return json.dumps(
            {
                "project_root": str(snap.project_root),
                "total_entries": snap.total_count,
                "max_entries": 500,
                "schema_version": schema_ver,
                "tier_distribution": snap.tier_counts,
            }
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
        schema_ver = store._persistence.get_schema_version()
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
            load_federation_config,
        )

        config = load_federation_config()
        try:
            hub = FederatedStore()
            stats = hub.get_stats()
            hub.close()
        except Exception:
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
            register_project,
            sync_to_hub,
        )

        register_project(project_id, str(resolved_dir))
        hub = FederatedStore()
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
        to the archive JSONL file.

        Args:
            dry_run: If True, only identify candidates without archiving (default False).
        """
        from tapps_brain.gc import GCResult, MemoryGarbageCollector

        gc = MemoryGarbageCollector()
        all_entries = store.list_all()
        candidates = gc.identify_candidates(all_entries)

        if dry_run:
            return json.dumps(
                {
                    "dry_run": True,
                    "candidates": len(candidates),
                    "candidate_keys": [e.key for e in candidates],
                }
            )

        # Archive and delete candidates
        if candidates:
            archive_path = resolved_dir / "memory" / "archive.jsonl"
            gc.append_to_archive(candidates, archive_path)
            for entry in candidates:
                store.delete(entry.key)

        remaining = store.count()
        result = GCResult(
            archived_count=len(candidates),
            remaining_count=remaining,
            archived_keys=[e.key for e in candidates],
        )
        return json.dumps(result.model_dump(mode="json"))

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

            result = store.save(
                key=key,
                value=mem["value"],
                tier=mem.get("tier", "pattern"),
                source=mem.get("source", "system"),
                tags=mem.get("tags"),
                scope=mem.get("scope", "project"),
            )
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

    # ------------------------------------------------------------------
    # Profile tools (EPIC-010)
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def profile_info() -> str:
        """Return the active profile name, layers, and scoring config."""
        profile = store.profile
        if profile is None:
            return json.dumps({"error": "no_profile", "message": "No profile loaded."})

        return json.dumps({
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
        })

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
            return json.dumps({
                "switched": True,
                "profile": profile.name,
                "layer_count": len(profile.layers),
            })
        except FileNotFoundError:
            from tapps_brain.profile import list_builtin_profiles

            return json.dumps({
                "error": "profile_not_found",
                "message": f"No built-in profile '{name}'.",
                "available": list_builtin_profiles(),
            })

    # ------------------------------------------------------------------
    # Hive tools (EPIC-011)
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def hive_status() -> str:
        """Return Hive status: namespaces, entry counts, registered agents."""
        try:
            from tapps_brain.hive import AgentRegistry, HiveStore

            hive = HiveStore()
            namespaces = hive.list_namespaces()
            ns_counts: dict[str, int] = {}
            for ns in namespaces:
                rows = hive._conn.execute(
                    "SELECT COUNT(*) FROM hive_memories WHERE namespace = ?",
                    (ns,),
                ).fetchone()
                ns_counts[ns] = rows[0] if rows else 0

            registry = AgentRegistry()
            agents = [
                {"id": a.id, "profile": a.profile, "skills": a.skills}
                for a in registry.list_agents()
            ]
            hive.close()
            return json.dumps({
                "namespaces": ns_counts,
                "total_entries": sum(ns_counts.values()),
                "agents": agents,
            })
        except Exception as exc:
            return json.dumps({"error": "hive_error", "message": str(exc)})

    @mcp.tool()  # type: ignore[untyped-decorator]
    def hive_search(query: str, namespace: str | None = None) -> str:
        """Search the Hive shared brain.

        Args:
            query: Full-text search query.
            namespace: Optional namespace filter (e.g. 'universal', 'repo-brain').
        """
        try:
            from tapps_brain.hive import HiveStore

            hive = HiveStore()
            ns_list = [namespace] if namespace else None
            results = hive.search(query, namespaces=ns_list, limit=20)
            hive.close()
            return json.dumps({"results": results, "count": len(results)})
        except Exception as exc:
            return json.dumps({"error": "hive_error", "message": str(exc)})

    @mcp.tool()  # type: ignore[untyped-decorator]
    def hive_propagate(key: str, agent_scope: str = "hive") -> str:
        """Manually propagate a local memory to the Hive.

        Args:
            key: Key of the local memory to propagate.
            agent_scope: Scope: 'domain' or 'hive' (default).
        """
        entry = store.get(key)
        if entry is None:
            return json.dumps({"error": "not_found", "message": f"Key '{key}' not found."})

        try:
            from tapps_brain.hive import HiveStore, PropagationEngine

            hive = HiveStore()
            profile_name = "repo-brain"
            if store.profile is not None:
                profile_name = getattr(store.profile, "name", "repo-brain")

            tier_val = (
                entry.tier.value if hasattr(entry.tier, "value") else str(entry.tier)
            )
            result = PropagationEngine.propagate(
                key=entry.key,
                value=entry.value,
                agent_scope=agent_scope,
                agent_id="mcp-user",
                agent_profile=profile_name,
                tier=tier_val,
                confidence=entry.confidence,
                source=entry.source.value,
                tags=entry.tags,
                hive_store=hive,
            )
            hive.close()
            if result is None:
                return json.dumps({"propagated": False, "reason": "scope is private"})
            return json.dumps({"propagated": True, **result})
        except Exception as exc:
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
        try:
            from tapps_brain.hive import AgentRegistration, AgentRegistry

            registry = AgentRegistry()
            skill_list = [s.strip() for s in skills.split(",") if s.strip()]
            agent = AgentRegistration(
                id=agent_id, profile=profile, skills=skill_list
            )
            registry.register(agent)
            return json.dumps({
                "registered": True,
                "agent_id": agent_id,
                "profile": profile,
                "skills": skill_list,
            })
        except Exception as exc:
            return json.dumps({"error": "registry_error", "message": str(exc)})

    @mcp.tool()  # type: ignore[untyped-decorator]
    def agent_list() -> str:
        """List all registered agents in the Hive."""
        try:
            from tapps_brain.hive import AgentRegistry

            registry = AgentRegistry()
            agents = [
                a.model_dump(mode="json") for a in registry.list_agents()
            ]
            return json.dumps({"agents": agents, "count": len(agents)})
        except Exception as exc:
            return json.dumps({"error": "registry_error", "message": str(exc)})

    # ------------------------------------------------------------------
    # Attach store to server for testing access
    # ------------------------------------------------------------------
    mcp._tapps_store = store

    return mcp


def main() -> None:
    """Entry point for ``tapps-brain-mcp`` command."""
    parser = argparse.ArgumentParser(
        prog="tapps-brain-mcp",
        description="Run the tapps-brain MCP server (stdio transport).",
    )
    parser.add_argument(
        "--project-dir",
        type=str,
        default=None,
        help="Project root directory (defaults to cwd).",
    )
    args = parser.parse_args()

    project_dir = Path(args.project_dir) if args.project_dir else None
    server = create_server(project_dir)
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
