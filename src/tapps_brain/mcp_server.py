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
            "Install it with: uv sync --extra mcp (or --extra dev for tests)\n"
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
                "tier": result.tier.value,
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
                    "tier": e.tier.value,
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
                    "tier": e.tier.value,
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
                "tier": entry.tier.value,
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
                    "tier": e.tier.value,
                    "confidence": e.confidence,
                    "valid_at": e.valid_at,
                    "invalid_at": e.invalid_at,
                    "superseded_by": e.superseded_by,
                }
                for e in chain
            ]
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
