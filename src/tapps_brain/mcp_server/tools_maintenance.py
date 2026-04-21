"""Maintenance, health, config, export/import, relay, profile, and session-end MCP tools.

Extracted from ``tapps_brain.mcp_server.__init__`` (TAP-605).  Most of these
tools are **operator-gated** (:func:`ToolContext.require_operator_enabled`).
"""

from __future__ import annotations

import json
from typing import Any

from tapps_brain.mcp_server.context import ToolContext
from tapps_brain.services import (
    diagnostics_service,
    maintenance_service,
    memory_service,
    profile_service,
    relay_service,
)


def register_maintenance_tools(mcp: Any, ctx: ToolContext) -> None:
    """Register maintenance, config, export/import, profile, and session-end tools."""
    store = ctx.store
    agent_id = ctx.server_agent_id
    _pid = ctx.pid
    _require_operator_enabled = ctx.require_operator_enabled
    resolved_dir = ctx.resolved_dir

    # ------------------------------------------------------------------
    # Maintenance tools (STORY-008.5)
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def maintenance_consolidate(
        threshold: float = 0.7,
        min_group_size: int = 3,
        force: bool = True,
    ) -> str:
        """Trigger memory consolidation to merge similar entries."""
        _require_operator_enabled()
        return json.dumps(
            maintenance_service.maintenance_consolidate(
                store,
                _pid(),
                agent_id,
                project_root=resolved_dir,
                threshold=threshold,
                min_group_size=min_group_size,
                force=force,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def maintenance_gc(dry_run: bool = False) -> str:
        """Run garbage collection to archive stale memories."""
        _require_operator_enabled()
        return json.dumps(
            maintenance_service.maintenance_gc(store, _pid(), agent_id, dry_run=dry_run)
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def maintenance_stale() -> str:
        """List GC stale memory candidates with reasons (read-only; GitHub #21)."""
        _require_operator_enabled()
        return json.dumps(maintenance_service.maintenance_stale(store, _pid(), agent_id))

    @mcp.tool()  # type: ignore[untyped-decorator]
    def tapps_brain_health(check_hive: bool = True) -> str:
        """Return a structured health report for tapps-brain (issue #15)."""
        _require_operator_enabled()
        return json.dumps(
            diagnostics_service.tapps_brain_health(
                store,
                _pid(),
                agent_id,
                check_hive=check_hive,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_gc_config() -> str:
        """Return the current garbage collection configuration."""
        _require_operator_enabled()
        return json.dumps(memory_service.memory_gc_config(store, _pid(), agent_id))

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_gc_config_set(
        floor_retention_days: int | None = None,
        session_expiry_days: int | None = None,
        contradicted_threshold: float | None = None,
    ) -> str:
        """Update garbage collection configuration thresholds."""
        _require_operator_enabled()
        return json.dumps(
            memory_service.memory_gc_config_set(
                store,
                _pid(),
                agent_id,
                floor_retention_days=floor_retention_days,
                session_expiry_days=session_expiry_days,
                contradicted_threshold=contradicted_threshold,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_consolidation_config() -> str:
        """Return the current auto-consolidation configuration."""
        _require_operator_enabled()
        return json.dumps(memory_service.memory_consolidation_config(store, _pid(), agent_id))

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_consolidation_config_set(
        enabled: bool | None = None,
        threshold: float | None = None,
        min_entries: int | None = None,
    ) -> str:
        """Update auto-consolidation configuration."""
        _require_operator_enabled()
        return json.dumps(
            memory_service.memory_consolidation_config_set(
                store,
                _pid(),
                agent_id,
                enabled=enabled,
                threshold=threshold,
                min_entries=min_entries,
            )
        )

    # ------------------------------------------------------------------
    # Export / Import tools (STORY-008.5)
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_export(
        tier: str | None = None,
        scope: str | None = None,
        min_confidence: float | None = None,
    ) -> str:
        """Export memory entries as JSON."""
        _require_operator_enabled()
        return json.dumps(
            memory_service.memory_export(
                store,
                _pid(),
                agent_id,
                project_root=str(resolved_dir),
                tier=tier,
                scope=scope,
                min_confidence=min_confidence,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_import(
        memories_json: str,
        overwrite: bool = False,
    ) -> str:
        """Import memory entries from a JSON string."""
        _require_operator_enabled()
        return json.dumps(
            memory_service.memory_import(
                store,
                _pid(),
                agent_id,
                memories_json=memories_json,
                overwrite=overwrite,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def tapps_brain_relay_export(source_agent: str, items_json: str) -> str:
        """Build a memory relay JSON payload for cross-node handoff (GitHub #19)."""
        _require_operator_enabled()
        return json.dumps(
            relay_service.tapps_brain_relay_export(
                store,
                _pid(),
                agent_id,
                source_agent=source_agent,
                items_json=items_json,
            )
        )

    # ------------------------------------------------------------------
    # Profile tools (EPIC-010)
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def profile_info() -> str:
        """Return the active profile name, layers, and scoring config."""
        return json.dumps(profile_service.profile_info(store, _pid(), agent_id))

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_profile_onboarding() -> str:
        """Return Markdown onboarding guidance for the active memory profile (GitHub #45)."""
        return json.dumps(profile_service.memory_profile_onboarding(store, _pid(), agent_id))

    @mcp.tool()  # type: ignore[untyped-decorator]
    def profile_switch(name: str) -> str:
        """Switch to a different built-in memory profile."""
        return json.dumps(profile_service.profile_switch(store, _pid(), agent_id, name=name))

    # ------------------------------------------------------------------
    # Session end tool (Issue #17 — episodic memory capture)
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def tapps_brain_session_end(
        summary: str,
        tags: list[str] | None = None,
        daily_note: bool = False,
    ) -> str:
        """Record an end-of-session episodic memory entry."""
        return json.dumps(
            maintenance_service.tapps_brain_session_end(
                store,
                _pid(),
                agent_id,
                project_root=resolved_dir,
                summary=summary,
                tags=tags,
                daily_note=daily_note,
            ),
            default=str,
        )
