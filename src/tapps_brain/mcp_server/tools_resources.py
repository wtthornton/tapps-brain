"""MCP resource and prompt registrations (read-only store views + workflow templates).

Extracted from ``tapps_brain.mcp_server.__init__`` (TAP-605).
"""

from __future__ import annotations

import json
from typing import Any

from tapps_brain.mcp_server.context import ToolContext


def register_resources_and_prompts(mcp: Any, ctx: ToolContext) -> None:
    """Register ``memory://*`` resources and user-invoked prompts."""
    store = ctx.store

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
        """What do you remember about a topic?"""
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
        """Generate a summary of what's in the memory store."""
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
        """Remember a fact by saving it to the memory store."""
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
