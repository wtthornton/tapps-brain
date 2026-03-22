"""Auto-consolidation triggers for memory subsystem (Epic 58, Story 58.3).

Provides automatic consolidation of similar memory entries:
- On save: Check if new entry should be consolidated with existing entries
- On session start: Periodic scan to find and consolidate related entries
"""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime
from pathlib import Path  # noqa: TC003 - Used at runtime for Path operations
from typing import TYPE_CHECKING, Any

import structlog

from tapps_brain.consolidation import (
    consolidate,
    detect_consolidation_reason,
    merge_entry_relations,
    should_consolidate,
)
from tapps_brain.models import (
    ConsolidatedEntry,
    MemoryEntry,
    _utc_now_iso,
)
from tapps_brain.similarity import find_consolidation_groups

if TYPE_CHECKING:
    from tapps_brain.store import MemoryStore

logger = structlog.get_logger(__name__)

CONSOLIDATION_STATE_FILE = ".tapps-brain/memory/consolidation-state.json"
MIN_CONSOLIDATION_ENTRIES = 2


class ConsolidationResult:
    """Result of a consolidation operation."""

    def __init__(
        self,
        *,
        triggered: bool = False,
        consolidated_entry: ConsolidatedEntry | None = None,
        source_keys: list[str] | None = None,
        reason: str = "",
    ) -> None:
        self.triggered = triggered
        self.consolidated_entry = consolidated_entry
        self.source_keys = source_keys or []
        self.reason = reason

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "triggered": self.triggered,
            "consolidated_key": (self.consolidated_entry.key if self.consolidated_entry else None),
            "source_keys": self.source_keys,
            "reason": self.reason,
        }


class PeriodicScanResult:
    """Result of a periodic consolidation scan."""

    def __init__(
        self,
        *,
        scanned: bool = False,
        groups_found: int = 0,
        entries_consolidated: int = 0,
        consolidated_entries: list[str] | None = None,
        skipped_reason: str = "",
    ) -> None:
        self.scanned = scanned
        self.groups_found = groups_found
        self.entries_consolidated = entries_consolidated
        self.consolidated_entries = consolidated_entries or []
        self.skipped_reason = skipped_reason

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "scanned": self.scanned,
            "groups_found": self.groups_found,
            "entries_consolidated": self.entries_consolidated,
            "consolidated_entries": self.consolidated_entries,
            "skipped_reason": self.skipped_reason,
        }


def check_consolidation_on_save(
    entry: MemoryEntry,
    store: MemoryStore,
    *,
    threshold: float = 0.7,
    min_entries: int = 3,
) -> ConsolidationResult:
    """Check if a newly saved entry should trigger consolidation.

    Called after saving a new entry. If the entry is similar to existing
    entries (above threshold), consolidates them into a single entry.

    Args:
        entry: The newly saved/updated entry.
        store: The memory store to check against.
        threshold: Similarity threshold for consolidation.
        min_entries: Minimum entries (including new one) to trigger consolidation.

    Returns:
        ConsolidationResult with details of the operation.
    """
    min_entries = max(min_entries, MIN_CONSOLIDATION_ENTRIES)

    all_entries = store.list_all()
    candidates = [e for e in all_entries if e.key != entry.key]

    if len(candidates) < min_entries - 1:
        return ConsolidationResult(
            triggered=False,
            reason="not_enough_candidates",
        )

    matches = should_consolidate(entry, candidates, threshold=threshold)

    if len(matches) < min_entries - 1:
        return ConsolidationResult(
            triggered=False,
            reason="no_similar_entries",
        )

    entries_to_consolidate = [entry, *matches[: min_entries - 1]]
    reason = detect_consolidation_reason(entry, matches)

    try:
        consolidated = consolidate(entries_to_consolidate, reason=reason)
    except ValueError as exc:
        logger.debug(
            "consolidation_failed",
            error=str(exc),
            entry_key=entry.key,
        )
        return ConsolidationResult(
            triggered=False,
            reason=f"consolidation_error: {exc}",
        )

    source_keys = [e.key for e in entries_to_consolidate]

    _persist_consolidated_entry(store, consolidated, source_keys)

    logger.info(
        "auto_consolidation_triggered",
        new_key=consolidated.key,
        source_keys=source_keys,
        reason=reason.value,
        threshold=threshold,
    )

    return ConsolidationResult(
        triggered=True,
        consolidated_entry=consolidated,
        source_keys=source_keys,
        reason=reason.value,
    )


def _get_enum_value(obj: object) -> str:
    """Extract string value from an enum or return str() for non-enums."""
    return obj.value if hasattr(obj, "value") else str(obj)


def _persist_consolidated_entry(
    store: MemoryStore,
    consolidated: ConsolidatedEntry,
    source_keys: list[str],
) -> None:
    """Persist the consolidated entry and mark sources as consolidated.

    Saves the new consolidated entry and marks source entries by updating
    their metadata. Source entries are NOT deleted (retained for provenance).
    """
    store.save(
        key=consolidated.key,
        value=consolidated.value,
        tier=_get_enum_value(consolidated.tier),
        source=_get_enum_value(consolidated.source),
        source_agent=consolidated.source_agent,
        scope=_get_enum_value(consolidated.scope),
        tags=consolidated.tags,
        confidence=consolidated.confidence,
        batch_context="consolidate",
    )

    # Merge relations from all source entries onto the consolidated entry
    relation_lists = [store.get_relations(k) for k in source_keys]
    merged_relations = merge_entry_relations(relation_lists, consolidated.key)
    if merged_relations:
        store._persistence.save_relations(consolidated.key, merged_relations)
        with store._lock:
            store._relations[consolidated.key] = store._persistence.load_relations(consolidated.key)

    now = _utc_now_iso()
    for key in source_keys:
        if key != consolidated.key:
            store.update_fields(
                key,
                contradicted=True,
                contradiction_reason=f"consolidated into {consolidated.key}",
                # EPIC-004: set temporal fields for bi-temporal versioning
                invalid_at=now,
                superseded_by=consolidated.key,
            )


def run_periodic_consolidation_scan(
    store: MemoryStore,
    project_root: Path,
    *,
    threshold: float = 0.7,
    min_group_size: int = 3,
    scan_interval_days: int = 7,
    force: bool = False,
) -> PeriodicScanResult:
    """Run periodic scan for memory consolidation.

    Called at session start. Checks if enough time has passed since the
    last scan, finds consolidation groups, and consolidates them.

    Args:
        store: The memory store to scan.
        project_root: Project root for state file persistence.
        threshold: Similarity threshold for grouping.
        min_group_size: Minimum entries per group to consolidate.
        scan_interval_days: Minimum days between scans.
        force: If True, run scan regardless of last scan time.

    Returns:
        PeriodicScanResult with details of the operation.
    """
    if not force:
        last_scan = _get_last_scan_time(project_root)
        if last_scan is not None:
            days_since = (datetime.now(tz=UTC) - last_scan).days
            if days_since < scan_interval_days:
                return PeriodicScanResult(
                    scanned=False,
                    skipped_reason=f"last_scan_{days_since}_days_ago",
                )

    all_entries = store.list_all()

    active_entries = [
        e for e in all_entries if not getattr(e, "is_consolidated", False) and not e.contradicted
    ]

    if len(active_entries) < min_group_size:
        _update_last_scan_time(project_root)
        return PeriodicScanResult(
            scanned=True,
            groups_found=0,
            skipped_reason="not_enough_active_entries",
        )

    groups = find_consolidation_groups(
        active_entries,
        threshold=threshold,
        min_group_size=min_group_size,
    )

    if not groups:
        _update_last_scan_time(project_root)
        return PeriodicScanResult(
            scanned=True,
            groups_found=0,
        )

    consolidated_keys: list[str] = []
    total_entries_consolidated = 0

    entry_by_key = {e.key: e for e in active_entries}

    for group_keys in groups:
        group_entries = [entry_by_key[k] for k in group_keys if k in entry_by_key]

        if len(group_entries) < MIN_CONSOLIDATION_ENTRIES:
            continue

        reason = detect_consolidation_reason(group_entries[0], group_entries[1:])

        try:
            consolidated = consolidate(group_entries, reason=reason)
        except ValueError:
            continue

        _persist_consolidated_entry(store, consolidated, group_keys)
        consolidated_keys.append(consolidated.key)
        total_entries_consolidated += len(group_entries)

        logger.info(
            "periodic_consolidation_group",
            new_key=consolidated.key,
            source_count=len(group_entries),
            reason=reason.value,
        )

    _update_last_scan_time(project_root)

    logger.info(
        "periodic_consolidation_scan_complete",
        groups_found=len(groups),
        groups_consolidated=len(consolidated_keys),
        entries_consolidated=total_entries_consolidated,
    )

    return PeriodicScanResult(
        scanned=True,
        groups_found=len(groups),
        entries_consolidated=total_entries_consolidated,
        consolidated_entries=consolidated_keys,
    )


def _get_last_scan_time(project_root: Path) -> datetime | None:
    """Get the timestamp of the last consolidation scan."""
    state_path = project_root / CONSOLIDATION_STATE_FILE
    if not state_path.exists():
        return None

    with contextlib.suppress(json.JSONDecodeError, ValueError, OSError):
        data = json.loads(state_path.read_text(encoding="utf-8"))
        last_scan_str = data.get("last_scan")
        if last_scan_str:
            return datetime.fromisoformat(last_scan_str)

    return None


def _update_last_scan_time(project_root: Path) -> None:
    """Update the timestamp of the last consolidation scan."""
    state_path = project_root / CONSOLIDATION_STATE_FILE

    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)

        data: dict[str, Any] = {}
        with contextlib.suppress(json.JSONDecodeError, ValueError):
            if state_path.exists():
                data = json.loads(state_path.read_text(encoding="utf-8"))

        data["last_scan"] = datetime.now(tz=UTC).isoformat()
        state_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        logger.debug("consolidation_state_write_failed", exc_info=True)


def should_run_auto_consolidation(
    project_root: Path,
    *,
    auto_consolidate: bool = True,
) -> bool:
    """Check if auto-consolidation should run.

    Helper to check configuration before running consolidation.

    Args:
        project_root: Project root for configuration.
        auto_consolidate: Whether auto-consolidation is enabled.

    Returns:
        True if auto-consolidation should run.
    """
    return auto_consolidate
