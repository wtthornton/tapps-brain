"""Auto-consolidation triggers for memory subsystem (Epic 58, Story 58.3).

Provides automatic consolidation of similar memory entries:
- On save: Check if new entry should be consolidated with existing entries
- On session start: Periodic scan to find and consolidate related entries

EPIC-044 STORY-044.4: successful merges append JSONL audit actions
``consolidation_merge`` and ``consolidation_source`` (see ``_append_consolidation_audit``).
Deterministic **undo** reverts one merge via ``undo_consolidation_merge`` using the last
matching ``consolidation_merge`` row and strict validation on source rows.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path  # noqa: TC003 - Used at runtime for Path operations
from typing import TYPE_CHECKING, Any, Literal

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
from tapps_brain.rate_limiter import batch_exempt_scope
from tapps_brain.similarity import compute_similarity_with_embeddings, find_consolidation_groups

if TYPE_CHECKING:
    from tapps_brain.store import MemoryStore

logger = structlog.get_logger(__name__)

CONSOLIDATION_STATE_FILE = ".tapps-brain/memory/consolidation-state.json"
MIN_CONSOLIDATION_ENTRIES = 2


@dataclass(frozen=True)
class ConsolidationUndoResult:
    """Outcome of ``undo_consolidation_merge`` (EPIC-044 STORY-044.4)."""

    ok: bool
    reason: str
    consolidated_key: str
    source_keys: tuple[str, ...] = ()
    trigger: str | None = None
    threshold: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "consolidated_key": self.consolidated_key,
            "source_keys": list(self.source_keys),
            "trigger": self.trigger,
            "threshold": self.threshold,
        }


def find_last_consolidation_merge_audit(
    audit_path: Path,
    consolidated_key: str,
    persistence: Any = None,
) -> dict[str, Any] | None:
    """Return the last ``consolidation_merge`` audit record for *consolidated_key*.

    Prefers ``persistence.query_audit()`` (Postgres ``audit_log`` table) when
    available; falls back to the JSONL ``audit_path`` for in-memory/test
    backends.  Returns ``None`` when no matching row is found.
    """
    if persistence is not None and hasattr(persistence, "query_audit"):
        try:
            rows = persistence.query_audit(
                key=consolidated_key,
                event_type="consolidation_merge",
                limit=1000,
            )
        except Exception:
            rows = []
        if rows:
            last_row = rows[-1]
            details = last_row.get("details") or {}
            rec: dict[str, Any] = {
                "action": last_row.get("event_type"),
                "key": last_row.get("key"),
                **details,
            }
            return rec
        # fall through to JSONL when Postgres returned nothing — the
        # in-memory fake still writes JSONL.

    if not audit_path.is_file():
        return None
    last: dict[str, Any] | None = None
    try:
        text = audit_path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("action") != "consolidation_merge":
            continue
        if rec.get("key") != consolidated_key:
            continue
        last = rec
    return last


def undo_consolidation_merge(  # noqa: PLR0911
    store: MemoryStore,
    consolidated_key: str,
) -> ConsolidationUndoResult:
    """Revert one auto-consolidation merge (restore sources, delete consolidated row).

    Uses the **last** ``consolidation_merge`` audit row for *consolidated_key*. Each
    source row must still be ``contradicted``, ``superseded_by`` the consolidated key,
    and ``contradiction_reason`` exactly ``consolidated into <key>`` (same string
    auto-consolidation writes). On success, appends ``consolidation_merge_undo`` to
    the JSONL audit log and removes relations tied to the consolidated key.

    The store serialization lock is held for the full in-memory + SQLite sequence so
    concurrent saves do not interleave with undo.
    """
    merge_rec = find_last_consolidation_merge_audit(
        store._persistence.audit_path,
        consolidated_key,
        persistence=store._persistence,
    )
    if merge_rec is None:
        return ConsolidationUndoResult(
            ok=False,
            reason="no_consolidation_merge_audit",
            consolidated_key=consolidated_key,
        )

    raw_sources = merge_rec.get("source_keys")
    if not isinstance(raw_sources, list) or len(raw_sources) < MIN_CONSOLIDATION_ENTRIES:
        return ConsolidationUndoResult(
            ok=False,
            reason="invalid_audit_source_keys",
            consolidated_key=consolidated_key,
        )
    source_keys = tuple(str(x) for x in raw_sources)

    trigger_raw = merge_rec.get("trigger")
    trigger = str(trigger_raw) if isinstance(trigger_raw, str) else None
    threshold_val = merge_rec.get("threshold")
    threshold = float(threshold_val) if isinstance(threshold_val, (int, float)) else None

    expected_reason = f"consolidated into {consolidated_key}"

    with store._serialized():
        if consolidated_key not in store._entries:
            return ConsolidationUndoResult(
                ok=False,
                reason="consolidated_entry_missing",
                consolidated_key=consolidated_key,
                source_keys=source_keys,
                trigger=trigger,
                threshold=threshold,
            )

        backup_consolidated = store._entries[consolidated_key]
        backup_sources: dict[str, MemoryEntry] = {}
        restored: dict[str, MemoryEntry] = {}

        for sk in source_keys:
            e = store._entries.get(sk)
            if e is None:
                return ConsolidationUndoResult(
                    ok=False,
                    reason=f"source_entry_missing:{sk}",
                    consolidated_key=consolidated_key,
                    source_keys=source_keys,
                    trigger=trigger,
                    threshold=threshold,
                )
            if not e.contradicted:
                return ConsolidationUndoResult(
                    ok=False,
                    reason=f"source_not_contradicted:{sk}",
                    consolidated_key=consolidated_key,
                    source_keys=source_keys,
                    trigger=trigger,
                    threshold=threshold,
                )
            if e.superseded_by != consolidated_key:
                return ConsolidationUndoResult(
                    ok=False,
                    reason=f"source_superseded_by_mismatch:{sk}",
                    consolidated_key=consolidated_key,
                    source_keys=source_keys,
                    trigger=trigger,
                    threshold=threshold,
                )
            if e.contradiction_reason != expected_reason:
                return ConsolidationUndoResult(
                    ok=False,
                    reason=f"source_contradiction_reason_mismatch:{sk}",
                    consolidated_key=consolidated_key,
                    source_keys=source_keys,
                    trigger=trigger,
                    threshold=threshold,
                )
            backup_sources[sk] = e
            now = _utc_now_iso()
            restored[sk] = e.model_copy(
                update={
                    "contradicted": False,
                    "contradiction_reason": None,
                    "invalid_at": None,
                    "superseded_by": None,
                    "updated_at": now,
                }
            )

        store._entries.pop(consolidated_key, None)
        for sk, re in restored.items():
            store._entries[sk] = re

        try:
            for sk in source_keys:
                store._persistence.save(store._entries[sk])
            store._persistence.delete_relations(consolidated_key)  # type: ignore[attr-defined]
            deleted = store._persistence.delete(consolidated_key)
            if not deleted:
                msg = "consolidated_row_delete_failed"
                raise RuntimeError(msg)
        except Exception:
            store._entries[consolidated_key] = backup_consolidated
            for sk, old in backup_sources.items():
                store._entries[sk] = old
            try:
                for _sk, old in backup_sources.items():
                    store._persistence.save(old)
            except Exception:
                logger.warning("undo_consolidation_merge_rollback_failed", exc_info=True)
            raise

        store._relations.pop(consolidated_key, None)
        for sk in source_keys:
            store._relations[sk] = store._persistence.load_relations(sk)

        store._persistence.append_audit(
            "consolidation_merge_undo",
            consolidated_key,
            extra={
                "source_keys": list(source_keys),
                "trigger": trigger,
                "threshold": threshold,
            },
        )
        store._metrics.increment("store.consolidation_merge_undo")

    return ConsolidationUndoResult(
        ok=True,
        reason="ok",
        consolidated_key=consolidated_key,
        source_keys=source_keys,
        trigger=trigger,
        threshold=threshold,
    )


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

    # Compute similarity signal for audit provenance (STORY-SC03 / TAP-559).
    sim_result = compute_similarity_with_embeddings(entry, entries_to_consolidate[1])
    audit_similarity_score: float | None = sim_result.combined_score
    audit_merge_rule = "embedding_cosine" if sim_result.used_embeddings else "text_similarity"

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

    _persist_consolidated_entry(
        store,
        consolidated,
        source_keys,
        audit_trigger="save",
        audit_threshold=threshold,
        audit_similarity_score=audit_similarity_score,
        audit_merge_rule=audit_merge_rule,
    )

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


def _consolidation_reason_str(consolidated: ConsolidatedEntry) -> str:
    r = consolidated.consolidation_reason
    return r.value if hasattr(r, "value") else str(r)


def _append_consolidation_audit(
    store: MemoryStore,
    *,
    consolidated_key: str,
    source_keys: list[str],
    trigger: Literal["save", "periodic_scan"],
    threshold: float,
    consolidation_reason: str,
    similarity_score: float | None = None,
    merge_rule: str = "text_similarity",
) -> None:
    """Append merge + per-source audit rows for operator traceability (EPIC-044 STORY-044.4).

    STORY-SC03 (TAP-559): *similarity_score* and *merge_rule* are now recorded so
    ``maintenance consolidation-diff <key>`` can surface the exact merge signal used.
    """
    persistence = getattr(store, "_persistence", None)
    if persistence is None:
        return
    merge_extra: dict[str, Any] = {
        "consolidated_key": consolidated_key,
        "source_keys": list(source_keys),
        "trigger": trigger,
        "threshold": threshold,
        "consolidation_reason": consolidation_reason,
        "similarity_score": similarity_score,
        "merge_rule": merge_rule,
    }
    persistence.append_audit("consolidation_merge", consolidated_key, extra=merge_extra)
    for sk in source_keys:
        if sk != consolidated_key:
            persistence.append_audit(
                "consolidation_source",
                sk,
                extra={
                    "superseded_by": consolidated_key,
                    "trigger": trigger,
                    "threshold": threshold,
                },
            )


def _persist_consolidated_entry(
    store: MemoryStore,
    consolidated: ConsolidatedEntry,
    source_keys: list[str],
    *,
    audit_trigger: Literal["save", "periodic_scan"] | None = None,
    audit_threshold: float | None = None,
    audit_similarity_score: float | None = None,
    audit_merge_rule: str = "text_similarity",
) -> None:
    """Persist the consolidated entry and mark sources as consolidated.

    Saves the new consolidated entry and marks source entries by updating
    their metadata. Source entries are NOT deleted (retained for provenance).

    STORY-SC03 (TAP-559): *audit_similarity_score* and *audit_merge_rule* are
    forwarded to :func:`_append_consolidation_audit` for operator traceability.
    """
    with batch_exempt_scope("consolidate"):
        store.save(
            key=consolidated.key,
            value=consolidated.value,
            tier=_get_enum_value(consolidated.tier),
            source=_get_enum_value(consolidated.source),
            source_agent=consolidated.source_agent,
            scope=_get_enum_value(consolidated.scope),
            tags=consolidated.tags,
            confidence=consolidated.confidence,
            skip_consolidation=True,
        )

    # Merge relations from all source entries onto the consolidated entry.
    relation_lists = [store.get_relations(k) for k in source_keys]
    merged_relations = merge_entry_relations(relation_lists, consolidated.key)
    if merged_relations:
        store.save_relations(consolidated.key, merged_relations)

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

    if audit_trigger is not None and audit_threshold is not None:
        _append_consolidation_audit(
            store,
            consolidated_key=consolidated.key,
            source_keys=source_keys,
            trigger=audit_trigger,
            threshold=audit_threshold,
            consolidation_reason=_consolidation_reason_str(consolidated),
            similarity_score=audit_similarity_score,
            merge_rule=audit_merge_rule,
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

    # Use isinstance for type-safe consolidated-entry detection. Note: entries
    # saved via store.save() are always MemoryEntry instances (not ConsolidatedEntry
    # subclass), so the isinstance check is a forward-compat guard only — filtering
    # on ``contradicted`` is what actually excludes processed source entries.
    active_entries = [
        e for e in all_entries if not isinstance(e, ConsolidatedEntry) and not e.contradicted
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

        # Compute similarity signal for audit provenance (STORY-SC03 / TAP-559).
        sim_result = compute_similarity_with_embeddings(group_entries[0], group_entries[1])
        periodic_sim_score: float | None = sim_result.combined_score
        periodic_merge_rule = (
            "embedding_cosine" if sim_result.used_embeddings else "text_similarity"
        )

        try:
            consolidated = consolidate(group_entries, reason=reason)
        except ValueError:
            logger.debug(
                "periodic_consolidation_group_failed",
                group_keys=group_keys,
                exc_info=True,
            )
            continue

        _persist_consolidated_entry(
            store,
            consolidated,
            group_keys,
            audit_trigger="periodic_scan",
            audit_threshold=threshold,
            audit_similarity_score=periodic_sim_score,
            audit_merge_rule=periodic_merge_rule,
        )
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
            dt = datetime.fromisoformat(last_scan_str)
            # Guard against naive datetimes written by older versions of this code.
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt

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
        project_root: Reserved for future per-project config file lookup. Currently unused.
        auto_consolidate: Whether auto-consolidation is enabled.

    Returns:
        True if auto-consolidation should run.
    """
    return auto_consolidate
