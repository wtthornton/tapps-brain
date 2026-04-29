"""Maintenance service functions (EPIC-070 STORY-070.1).

Exposes memory lifecycle operations (consolidation, GC, stale listing, session
end) via MCP maintenance tools and the HTTP adapter. Delegates to
``auto_consolidation``, ``MemoryStore.gc()``, and ``session_summary``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path


def maintenance_consolidate(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    project_root: Path,
    threshold: float = 0.7,
    min_group_size: int = 3,
    force: bool = True,
) -> dict[str, Any]:
    """Run a periodic consolidation scan, merging similar entries above the similarity threshold."""
    from tapps_brain.auto_consolidation import run_periodic_consolidation_scan

    result = run_periodic_consolidation_scan(
        store=store,
        project_root=project_root,
        threshold=threshold,
        min_group_size=min_group_size,
        force=force,
    )
    return result.to_dict()


def maintenance_gc(
    store: Any, project_id: str, agent_id: str, *, dry_run: bool = False
) -> dict[str, Any]:
    """Archive stale memory entries; in dry-run mode reports candidates without modifying data."""
    raw = store.gc(dry_run=dry_run)
    payload = raw.model_dump(mode="json")
    if dry_run:
        payload["dry_run"] = True
        payload["candidates"] = len(raw.archived_keys)
        payload["candidate_keys"] = raw.archived_keys
    else:
        payload["dry_run"] = False

    # STORY-070.5: sweep expired idempotency keys when feature is enabled.
    if not dry_run:
        from tapps_brain.idempotency import is_idempotency_enabled, sweep_expired_keys

        if is_idempotency_enabled():
            swept = sweep_expired_keys()
            payload["idempotency_keys_swept"] = swept

    return payload  # type: ignore[no-any-return]


def maintenance_stale(store: Any, project_id: str, agent_id: str) -> dict[str, Any]:
    """List entries eligible for GC archival with full detail for review before committing."""
    details = store.list_gc_stale_details()
    return {
        "count": len(details),
        "entries": [d.model_dump(mode="json") for d in details],
    }


def tapps_brain_session_end(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    project_root: Path,
    summary: str,
    tags: list[str] | None = None,
    daily_note: bool = False,
) -> dict[str, Any]:
    """Persist a session summary to disk and optionally append to the daily note."""
    from tapps_brain.session_summary import session_summary_save

    return session_summary_save(
        summary,
        tags=tags,
        project_dir=project_root,
        workspace_dir=project_root,
        daily_note=daily_note,
        source_agent=agent_id,
    )
