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
    MemoryStatus,
    _utc_now_iso,
)
from tapps_brain.rate_limiter import batch_exempt_scope
from tapps_brain.relations import RelationEntry
from tapps_brain.similarity import compute_similarity_with_embeddings, find_consolidation_groups

if TYPE_CHECKING:
    from tapps_brain.store import MemoryStore

logger = structlog.get_logger(__name__)

CONSOLIDATION_STATE_FILE = ".tapps-brain/memory/consolidation-state.json"
MIN_CONSOLIDATION_ENTRIES = 2
# Page size for audit-log lookups in find_last_consolidation_merge_audit.
_AUDIT_PAGE_LIMIT = 1000

# Minimum fraction of the summed source bytes a merged value must retain.
# ``merge_values`` keeps the newest value verbatim and at most two sentences
# per older source, and the result is hard-capped at 4096 chars — merging
# long-form entries therefore *destroys* content while superseding the
# originals, which then vanish from recall. Below this floor the merge is
# refused outright rather than shipped lossy.
MIN_CONTENT_PRESERVATION_RATIO = 0.6
MERGE_BLOCKED_CONTENT_LOSS_METRIC = "store.consolidate.blocked_content_loss"


class MergeWouldLoseContentError(RuntimeError):
    """Raised when a merge would discard too much of its sources' content.

    Carries :attr:`reason` so callers can surface a stable machine-readable
    outcome instead of parsing the message.
    """

    reason = "merge_would_lose_content"

    def __init__(self, consolidated_key: str, ratio: float) -> None:
        self.consolidated_key = consolidated_key
        self.ratio = ratio
        super().__init__(
            f"merge '{consolidated_key}' would retain only {ratio:.2%} of its sources' "
            f"content (floor {MIN_CONTENT_PRESERVATION_RATIO:.0%}); aborting"
        )


def _content_preservation_ratio(
    merged_value: str,
    source_snapshots: dict[str, MemoryEntry],
) -> float | None:
    """Fraction of the summed source bytes retained by *merged_value*.

    Returns ``None`` when the ratio is undefined (no snapshots, or sources
    with no content at all) so the caller can skip the guard rather than
    treat an unknowable ratio as a violation.
    """
    total = sum(len(e.value) for e in source_snapshots.values())
    if total <= 0:
        return None
    return len(merged_value) / total


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
        # NOTE: a Postgres failure propagates — falling through to JSONL could
        # return a stale/ghost merge record and corrupt undo.
        rows = persistence.query_audit(
            key=consolidated_key,
            event_type="consolidation_merge",
            limit=_AUDIT_PAGE_LIMIT,
        )
        # query_audit orders oldest-first with a LIMIT, so a full page may
        # have truncated the *newest* rows — page forward via the inclusive
        # `since` cursor until the final (partial) page is reached.
        while len(rows) == _AUDIT_PAGE_LIMIT:
            tail_ts = rows[-1].get("timestamp")
            if not tail_ts:
                break
            nxt = persistence.query_audit(
                key=consolidated_key,
                event_type="consolidation_merge",
                since=str(tail_ts),
                limit=_AUDIT_PAGE_LIMIT,
            )
            if not nxt:
                break
            if len(nxt) == _AUDIT_PAGE_LIMIT and nxt[-1].get("timestamp") == tail_ts:
                # Pathological page of identical timestamps — cannot advance.
                rows = nxt
                break
            rows = nxt
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
        stripped = line.strip()
        if not stripped:
            continue
        try:
            rec = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if rec.get("action") != "consolidation_merge":
            continue
        if rec.get("key") != consolidated_key:
            continue
        last = rec
    return last


def _strip_key_from_relations(store: MemoryStore, consolidated_key: str) -> None:
    """Detach *consolidated_key* from relation rows without destroying source linkage.

    ``delete_relations(key)`` removes every row whose ``source_entry_keys``
    *contains* the key — but after a merge those rows also carry the source
    entries' linkage, so outright deletion would permanently orphan the
    sources' relations. Instead, rows still referenced by other entries are
    re-upserted with the consolidated key stripped; only rows referenced
    exclusively by the consolidated entry are deleted.
    """
    rows = store._persistence.load_relations(consolidated_key)
    survivors: list[RelationEntry] = []
    for r in rows:
        remaining = [str(k) for k in r.get("source_entry_keys", []) if k != consolidated_key]
        if remaining:
            confidence = float(r.get("confidence", 0.8))
            survivors.append(
                RelationEntry(
                    subject=r["subject"],
                    predicate=r["predicate"],
                    object_entity=r["object_entity"],
                    source_entry_keys=remaining,
                    confidence=confidence,
                    confidence_history=list(r.get("confidence_history") or [confidence]),
                )
            )
    store._persistence.delete_relations(consolidated_key)
    for rel in survivors:
        # save_relations upserts on the triple; anchoring on the first
        # remaining key is a no-op addition since it is already present.
        store._persistence.save_relations(rel.source_entry_keys[0], [rel])


def _strip_key_from_relations_best_effort(store: MemoryStore, consolidated_key: str) -> None:
    """Strip relations after the durable entry delete already succeeded.

    A failure here leaves only a dangling consolidated key inside relation
    rows (nothing resolves it), so log rather than roll the undo back into a
    ghost state where the entry is durably gone but reported as restored.
    """
    try:
        _strip_key_from_relations(store, consolidated_key)
    except Exception:
        logger.warning(
            "undo_consolidation_strip_relations_failed",
            consolidated_key=consolidated_key,
            exc_info=True,
        )


def undo_consolidation_merge(  # noqa: PLR0911
    store: MemoryStore,
    consolidated_key: str,
) -> ConsolidationUndoResult:
    """Revert one auto-consolidation merge (restore sources, delete consolidated row).

    Uses the **last** ``consolidation_merge`` audit row for *consolidated_key*. Each
    source row must still be ``contradicted``, ``superseded_by`` the consolidated key,
    and ``contradiction_reason`` exactly ``consolidated into <key>`` (same string
    auto-consolidation writes). On success, appends ``consolidation_merge_undo`` to
    the audit trail via ``append_audit`` (the Postgres ``audit_log`` table in
    production; a JSONL file only under the test fake) and removes relations
    tied to the consolidated key.

    The store serialization lock (``store._serialized()``) is held for the full
    in-memory + persistence sequence so concurrent saves do not interleave with undo
    and no partial state is observable by other threads.
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

    if store._ensure_entry_cached(consolidated_key) is None:
        return ConsolidationUndoResult(
            ok=False,
            reason="consolidated_entry_missing",
            consolidated_key=consolidated_key,
            source_keys=source_keys,
            trigger=trigger,
            threshold=threshold,
        )
    for sk in source_keys:
        if store._ensure_entry_cached(sk) is None:
            return ConsolidationUndoResult(
                ok=False,
                reason=f"source_entry_missing:{sk}",
                consolidated_key=consolidated_key,
                source_keys=source_keys,
                trigger=trigger,
                threshold=threshold,
            )

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
                    # TAP-6697: undo the status write close_validity made, or the
                    # restored source is invisible to the live-row predicate.
                    "status": MemoryStatus.active,
                    "updated_at": now,
                }
            )

        store._entries.pop(consolidated_key, None)
        for sk, re in restored.items():
            store._entries[sk] = re

        try:
            for sk in source_keys:
                store._persistence.save(store._entries[sk])
            deleted = store._persistence.delete(consolidated_key)
            if not deleted:
                msg = "consolidated_row_delete_failed"
                raise RuntimeError(msg)
            # Strip only AFTER the durable delete succeeds: stripping first
            # meant a failed delete left the merge in place with its
            # knowledge-graph edges already gone — and never restored.
            _strip_key_from_relations_best_effort(store, consolidated_key)
        except Exception:
            store._entries[consolidated_key] = backup_consolidated
            for sk, old in backup_sources.items():
                store._entries[sk] = old
            try:
                for old in backup_sources.values():
                    store._persistence.save(old)
            except Exception:
                logger.warning("undo_consolidation_merge_rollback_failed", exc_info=True)
            raise

        # Coherence steps store.delete would have performed (bypassed here to
        # avoid its wholesale delete_relations): the removal tombstone stops
        # a concurrent _merge_durable_entries (list_all/count/gc) whose
        # pre-undo load_all snapshot still contains the consolidated row from
        # resurrecting it as a cache-only ghost, and the entity-index cleanup
        # stops graph-centrality scoring from counting the deleted key.
        store._note_removed_locked(consolidated_key)
        store._remove_entry_entities(consolidated_key)

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
        blocked_content_loss: int = 0,
    ) -> None:
        self.scanned = scanned
        self.groups_found = groups_found
        self.entries_consolidated = entries_consolidated
        self.consolidated_entries = consolidated_entries or []
        self.skipped_reason = skipped_reason
        #: Groups the content-preservation floor refused (not failures).
        self.blocked_content_loss = blocked_content_loss

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "scanned": self.scanned,
            "groups_found": self.groups_found,
            "entries_consolidated": self.entries_consolidated,
            "consolidated_entries": self.consolidated_entries,
            "skipped_reason": self.skipped_reason,
            "blocked_content_loss": self.blocked_content_loss,
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

    try:
        _persist_consolidated_entry(
            store,
            consolidated,
            source_keys,
            audit_trigger="save",
            audit_threshold=threshold,
            audit_similarity_score=audit_similarity_score,
            audit_merge_rule=audit_merge_rule,
            source_snapshots={e.key: e for e in entries_to_consolidate},
        )
    except MergeWouldLoseContentError as exc:
        # A refused merge is a normal, expected outcome — not a failure to
        # log-and-swallow upstream. Report it as an untriggered result so the
        # reason reaches operators via ConsolidationResult.
        return ConsolidationResult(
            triggered=False,
            reason=exc.reason,
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


def _enforce_content_preservation(
    store: MemoryStore,
    consolidated: ConsolidatedEntry,
    source_keys: list[str],
    source_snapshots: dict[str, MemoryEntry],
) -> None:
    """Refuse a merge that would retain too little of its sources' content.

    Raises:
        MergeWouldLoseContentError: When the ratio is below the floor.
    """
    ratio = _content_preservation_ratio(consolidated.value, source_snapshots)
    if ratio is None or ratio >= MIN_CONTENT_PRESERVATION_RATIO:
        return
    store._metrics.increment(MERGE_BLOCKED_CONTENT_LOSS_METRIC)
    logger.warning(
        "consolidation_blocked_content_loss",
        consolidated_key=consolidated.key,
        source_keys=source_keys,
        preservation_ratio=round(ratio, 4),
        floor=MIN_CONTENT_PRESERVATION_RATIO,
    )
    raise MergeWouldLoseContentError(consolidated.key, ratio)


def _persist_consolidated_entry(
    store: MemoryStore,
    consolidated: ConsolidatedEntry,
    source_keys: list[str],
    *,
    audit_trigger: Literal["save", "periodic_scan"] | None = None,
    audit_threshold: float | None = None,
    audit_similarity_score: float | None = None,
    audit_merge_rule: str = "text_similarity",
    source_snapshots: dict[str, MemoryEntry],
) -> None:
    """Persist the consolidated entry and mark sources as consolidated.

    Saves the new consolidated entry and marks source entries by updating
    their metadata. Source entries are NOT deleted (retained for provenance).

    STORY-SC03 (TAP-559): *audit_similarity_score* and *audit_merge_rule* are
    forwarded to :func:`_append_consolidation_audit` for operator traceability.

    *source_snapshots* carries the entries the merged value was computed
    from, and is **required** — it backs both the content-preservation floor
    and the lost-update guard, and a default of ``None`` would let a new call
    site silently opt out of both.  Before a source is superseded its live
    ``value`` is compared against the snapshot — a concurrent ``save()``
    landing new content between snapshot and marking means the fresh write is
    absent from the merged value, and superseding the source anyway would hide
    it from recall (lost update).  On mismatch the merge is aborted and rolled
    back.  (``value`` is compared rather than ``updated_at`` because save-path
    metadata re-stamps — access counts, embeddings, reinforcement — touch
    ``updated_at`` without invalidating the merged content.)

    Raises:
        MergeWouldLoseContentError: When the merged value would retain less
            than :data:`MIN_CONTENT_PRESERVATION_RATIO` of the summed source
            bytes.  Raised before any write, so nothing needs rolling back.
    """
    consolidated_saved = False
    try:
        # Content-preservation floor. Raised *before* any write so the
        # existing rollback below is a no-op: no merge row is created and no
        # source is superseded.
        _enforce_content_preservation(store, consolidated, source_keys, source_snapshots)

        with batch_exempt_scope("consolidate"):
            saved = store.save(
                key=consolidated.key,
                value=consolidated.value,
                tier=_get_enum_value(consolidated.tier),
                source=_get_enum_value(consolidated.source),
                source_agent=consolidated.source_agent,
                scope=_get_enum_value(consolidated.scope),
                tags=consolidated.tags,
                confidence=consolidated.confidence,
                # GitHub #49: carry the sources' project-local group onto the
                # merged row — omitting this would default a *new* key to
                # ungrouped (None), silently dropping the partition.
                memory_group=consolidated.memory_group,
                skip_consolidation=True,
                # The merged value frequently equals the newest source's value
                # verbatim; the dedup fast-path would then return that existing
                # entry WITHOUT creating the merge row, and conflict_check could
                # collaterally invalidate similar neighbors. Both must be off so
                # the row the sources are about to be superseded by actually exists.
                dedup=False,
                conflict_check=False,
            )
        if isinstance(saved, dict) or saved.key != consolidated.key:
            # Write policy or another save-path guard rejected/short-circuited
            # the row. Abort BEFORE marking sources superseded by a key that
            # was never created (phantom merge with no undo path).
            detail = saved.get("error", saved) if isinstance(saved, dict) else saved.key
            msg = f"consolidated entry save did not create '{consolidated.key}': {detail}"
            raise RuntimeError(msg)
        consolidated_saved = True

        # Merge relations from all source entries onto the consolidated entry.
        relation_lists = [store.get_relations(k) for k in source_keys]
        merged_relations = merge_entry_relations(relation_lists, consolidated.key)
        if merged_relations:
            store.save_relations(consolidated.key, merged_relations)

        for key in source_keys:
            if key != consolidated.key:
                if source_snapshots is not None:
                    snap = source_snapshots.get(key)
                    # Hydrate from durable store — a cold-cache miss must not
                    # skip the lost-update guard (that would supersede a fresh
                    # write that never lived in ``_entries``).
                    current = store._ensure_entry_cached(key)
                    if snap is not None and current is None:
                        msg = (
                            f"source '{key}' missing from store while consolidating "
                            f"into '{consolidated.key}'; aborting merge"
                        )
                        raise RuntimeError(msg)
                    if snap is not None and current is not None and current.value != snap.value:
                        msg = (
                            f"source '{key}' content changed concurrently since the "
                            f"merge snapshot; aborting merge '{consolidated.key}' "
                            f"to avoid superseding the fresh write"
                        )
                        raise RuntimeError(msg)
                # TAP-6697: one helper closes validity.  Before this the merge
                # wrote invalid_at + contradicted but left status='active', so a
                # consolidated source was live on the status axis and dead on the
                # temporal one (corrections-log #3).
                updated = store.close_validity(
                    key,
                    reason="consolidation",
                    superseded_by=consolidated.key,
                    detail=f"consolidated into {consolidated.key}",
                )
                if updated is None:
                    msg = (
                        f"Failed to mark source '{key}' as consolidated into "
                        f"'{consolidated.key}' (entry missing from store)"
                    )
                    raise KeyError(msg)

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
    except Exception:
        if consolidated_saved:
            # Roll back source marks + consolidated row so we do not leave a
            # split-brain where the merge exists or sources stay invalidated.
            try:
                for key in source_keys:
                    if key == consolidated.key:
                        continue
                    try:
                        store.update_fields(
                            key,
                            contradicted=False,
                            contradiction_reason=None,
                            invalid_at=None,
                            superseded_by=None,
                            # TAP-6697: close_validity set status too; reopen it
                            # or the rolled-back source stays out of recall.
                            status=MemoryStatus.active,
                        )
                    except Exception:
                        logger.warning(
                            "consolidation_source_unmark_failed",
                            source_key=key,
                            consolidated_key=consolidated.key,
                            exc_info=True,
                        )
                # Detach the merge key from shared relation rows BEFORE
                # store.delete: delete_relations removes every row whose
                # source_entry_keys *contains* the key, and after
                # save_relations above those rows also carry the sources'
                # linkage — deleting them outright would permanently orphan
                # the sources' graph edges (same hazard the undo path guards
                # against via _strip_key_from_relations).
                _strip_key_from_relations(store, consolidated.key)
                store.delete(consolidated.key)
            except Exception:
                logger.warning(
                    "consolidation_rollback_failed",
                    consolidated_key=consolidated.key,
                    source_keys=source_keys,
                    exc_info=True,
                )
        raise


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
    # TAP-732: also exclude retired lifecycle rows.  ``contradicted`` alone
    # misses entries retired via the supersession flow (status=superseded,
    # contradicted=False) and status=stale rows — merging those would
    # resurface content retrieval deliberately hides, and marking them
    # ``superseded_by=<merge key>`` would clobber the original supersession
    # pointer the status flow exists to preserve.
    active_entries = [
        e
        for e in all_entries
        if not isinstance(e, ConsolidatedEntry)
        and not e.contradicted
        and e.superseded_by is None
        and getattr(e, "status", "active") not in ("stale", "superseded", "archived")
    ]

    if len(active_entries) < min_group_size:
        _update_last_scan_time(project_root)
        return PeriodicScanResult(
            scanned=True,
            groups_found=0,
            skipped_reason="not_enough_active_entries",
        )

    # GitHub #49: partition by project-local memory_group before grouping —
    # the on-save path (should_consolidate) never merges across groups, and
    # a cross-group merge would silently move knowledge out of its partition
    # (consolidate() assigns the newest entry's group to the merged row).
    by_group: dict[str | None, list[MemoryEntry]] = {}
    for e in active_entries:
        by_group.setdefault(e.memory_group, []).append(e)

    groups: list[list[str]] = []
    for _group_name in sorted(by_group, key=lambda g: (g is not None, g or "")):
        groups.extend(
            find_consolidation_groups(
                by_group[_group_name],
                threshold=threshold,
                min_group_size=min_group_size,
            )
        )

    if not groups:
        _update_last_scan_time(project_root)
        return PeriodicScanResult(
            scanned=True,
            groups_found=0,
        )

    consolidated_keys: list[str] = []
    total_entries_consolidated = 0
    blocked_content_loss = 0

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

        try:
            _persist_consolidated_entry(
                store,
                consolidated,
                group_keys,
                audit_trigger="periodic_scan",
                audit_threshold=threshold,
                audit_similarity_score=periodic_sim_score,
                audit_merge_rule=periodic_merge_rule,
                source_snapshots={e.key: e for e in group_entries},
            )
        except MergeWouldLoseContentError as exc:
            # A refused merge is a deliberate outcome, not a failure. Logging
            # it as ``..._persist_failed`` would send operators hunting for a
            # broken write; the guard already emitted its own warning + metric.
            logger.info(
                "periodic_consolidation_group_blocked_content_loss",
                group_keys=group_keys,
                reason=exc.reason,
                preservation_ratio=round(exc.ratio, 4),
            )
            blocked_content_loss += 1
            continue
        except Exception:
            # One failing group (write-policy rejection, vanished source,
            # profile value-length limit, concurrent-modification abort) must
            # not abort the whole scan: _persist rolled itself back, so log
            # and move on.  Previously the exception escaped the loop,
            # skipping remaining groups, losing the PeriodicScanResult for
            # merges already applied, and never advancing the scan timestamp
            # — deterministic failures then re-ran on every session start.
            logger.warning(
                "periodic_consolidation_group_persist_failed",
                group_keys=group_keys,
                consolidated_key=consolidated.key,
                exc_info=True,
            )
            continue
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
        blocked_content_loss=blocked_content_loss,
    )

    return PeriodicScanResult(
        scanned=True,
        groups_found=len(groups),
        entries_consolidated=total_entries_consolidated,
        consolidated_entries=consolidated_keys,
        blocked_content_loss=blocked_content_loss,
    )


def _get_last_scan_time(project_root: Path) -> datetime | None:
    """Get the timestamp of the last consolidation scan."""
    state_path = project_root / CONSOLIDATION_STATE_FILE
    if not state_path.exists():
        return None

    with contextlib.suppress(json.JSONDecodeError, ValueError, OSError):
        data = json.loads(state_path.read_text(encoding="utf-8"))
        # Valid-but-non-dict JSON (null, [], "x") parses fine and would raise
        # AttributeError on .get — outside the suppressed set — crashing the
        # scan this helper is documented to shield from state-file corruption.
        if not isinstance(data, dict):
            return None
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
                loaded = json.loads(state_path.read_text(encoding="utf-8"))
                # Non-dict JSON would raise TypeError on item assignment
                # below — treat it as corrupt state and start fresh.
                if isinstance(loaded, dict):
                    data = loaded

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
