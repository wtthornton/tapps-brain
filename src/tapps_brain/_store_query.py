"""Read / query methods for :class:`~tapps_brain.store.MemoryStore` (TAP-2833).

Extracted from ``store.py`` as a mixin.  Covers point reads (``get``),
filtered listing (``list_all`` / ``list_memory_groups``), deletion, the
relative-time parser, the FTS search path and its post-filter / group-namespace
helpers.  Behaviour is identical to the original in-class definitions.
"""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime, timedelta
from typing import cast

import structlog

from tapps_brain._store_base import _MemoryStoreBase
from tapps_brain.metrics import MetricsTimer
from tapps_brain.models import MemoryEntry, _utc_now_iso
from tapps_brain.otel_tracer import (
    ATTR_LATENCY_MS,
    ATTR_ROWS_RETURNED,
    GEN_AI_DATA_SOURCE_ID,
    GEN_AI_OPERATION_EXECUTE_TOOL,
    SPAN_DELETE,
    SPAN_HIVE_SEARCH,
    SPAN_SEARCH,
    rm_add_recall_latency_ms,
    rm_increment_recall_total,
    start_span,
)

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

#: TAP-5677 — pool size for the stage-3 semantic fallback.  Deliberately far
#: below the FTS LIMIT 100: nearest-neighbour results without a lexical match
#: are speculative, and downstream consumers (``brain_recall`` max_results,
#: flywheel top-3) never read more than a handful.
_KNN_FALLBACK_K = 10


def _filter_memory_entries(
    entries: list[MemoryEntry],
    *,
    tier: str | None = None,
    scope: str | None = None,
    tags: list[str] | None = None,
    memory_group: str | None = None,
    include_superseded: bool = True,
) -> list[MemoryEntry]:
    """Apply optional list filters shared by ``list_all`` and search post-filters."""
    if tier is not None:
        entries = [e for e in entries if e.tier == tier]
    if scope is not None:
        entries = [e for e in entries if e.scope == scope]
    if memory_group is not None:
        entries = [e for e in entries if e.memory_group == memory_group]
    if tags:
        tag_set = set(tags)
        entries = [e for e in entries if tag_set.intersection(e.tags)]
    if not include_superseded:
        entries = [e for e in entries if e.is_temporally_valid()]
    return entries


class QueryMixin(_MemoryStoreBase):
    """Read, list, delete, and search methods (TAP-2833)."""

    def get(
        self,
        key: str,
        scope: str | None = None,
        branch: str | None = None,
    ) -> MemoryEntry | None:
        """Retrieve a memory entry by key.

        When *scope* and *branch* are provided, applies scope resolution:
        session > branch > project (most specific wins).

        Updates ``last_accessed`` and ``access_count`` on read.
        """
        self._metrics.increment("store.get")
        with MetricsTimer(self._metrics, "store.get_ms"):
            # Always hydrate first so scoped resolution sees durable rows.
            self._ensure_entry_cached(key)
            with self._serialized():
                if scope is not None and branch is not None:
                    entry = self._resolve_scope(key, scope, branch)
                else:
                    entry = self._entries.get(key)

                if entry is None:
                    self._metrics.increment("store.get.miss")
                    return None

                # Update access metadata
                now = _utc_now_iso()
                updated = entry.model_copy(
                    update={
                        "last_accessed": now,
                        "access_count": entry.access_count + 1,
                    }
                )
                self._entries[updated.key] = updated

            self._metrics.increment("store.get.hit")
            # Persist access metadata — rollback on failure.  Identity-guarded
            # so a concurrent writer that replaced the slot in the failure
            # window is not clobbered with the stale pre-image.
            try:
                self._persistence.save(updated)
            except Exception:
                with self._serialized():
                    if self._entries.get(updated.key) is updated:
                        self._entries[updated.key] = entry
                raise
            self._drop_if_concurrently_removed(updated.key)
            return updated

    def list_all(
        self,
        tier: str | None = None,
        scope: str | None = None,
        tags: list[str] | None = None,
        memory_group: str | None = None,
        include_superseded: bool = True,
    ) -> list[MemoryEntry]:
        """List entries with optional filters.

        Args:
            tier: Filter by tier.
            scope: Filter by scope.
            tags: Filter by tags.
            memory_group: When set, only entries in this project-local group
                (``None`` on entry means ungrouped; omit this arg to list all).
            include_superseded: When ``False``, exclude temporally invalid
                (superseded/expired) entries. Default ``True`` for backward
                compatibility.
        """
        self._merge_durable_entries()
        with self._serialized():
            entries = list(self._entries.values())

        return _filter_memory_entries(
            entries,
            tier=tier,
            scope=scope,
            tags=tags,
            memory_group=memory_group,
            include_superseded=include_superseded,
        )

    def list_memory_groups(self) -> list[str]:
        """Return sorted distinct project-local ``memory_group`` values (GitHub #49)."""
        self._merge_durable_entries()
        with self._serialized():
            names = {e.memory_group for e in self._entries.values() if e.memory_group}
        return sorted(names)

    def _ensure_entry_cached(self, key: str) -> MemoryEntry | None:
        """Return *key* from cache, hydrating from durable store on miss.

        Experience-path and other out-of-band upserts write Postgres without
        updating ``_entries``.  Mutating APIs must hydrate before treating a
        miss as "does not exist".
        """
        with self._serialized():
            entry = self._entries.get(key)
            if entry is not None:
                return entry
            snapshot_epoch = self._removal_epoch
        load_one = getattr(self._persistence, "load_one", None)
        if not callable(load_one):
            return None
        entry = cast("MemoryEntry | None", load_one(key))
        if entry is None:
            return None
        with self._serialized():
            existing = self._entries.get(key)
            if existing is not None:
                return existing
            # Mirror-image of the evict/persist race: a delete/evict may have
            # landed while load_one ran outside the lock — re-inserting the
            # stale row would resurrect it in the cache.
            if self._removed_at.get(key, 0) > snapshot_epoch:
                return None
            self._entries[key] = entry
            return entry

    def delete(self, key: str) -> bool:
        """Delete a memory entry by key. Returns True if deleted."""
        with start_span(SPAN_DELETE, {"gen_ai.operation.name": GEN_AI_OPERATION_EXECUTE_TOOL}):
            removed = self._ensure_entry_cached(key)
            if removed is None:
                return False
            with self._serialized():
                # Re-pop under lock in case another writer raced.
                removed = self._entries.pop(key, removed)

            # Persist deletion — rollback in-memory cache on failure to
            # maintain write-through consistency.
            try:
                self._persistence.delete(key)
            except Exception:
                with self._serialized():
                    self._entries[key] = removed
                raise

            # Drop relations that referenced this key (cache + durable store).
            try:
                self._persistence.delete_relations(key)
            except Exception:
                # Restore durable entry + in-memory row so callers see failure.
                with self._serialized():
                    self._entries[key] = removed
                try:
                    self._persistence.save(removed)
                except Exception:
                    logger.warning("delete_relations_rollback_failed", key=key, exc_info=True)
                raise
            with self._serialized():
                self._relations.pop(key, None)
                # The durable delete removes every relation row whose
                # source_entry_keys contains *key*; purge cached copies that
                # live under sibling source-key buckets too, or query_relations
                # keeps returning rows that no longer exist durably.
                for other_key, rels in list(self._relations.items()):
                    kept = [r for r in rels if key not in (r.get("source_entry_keys") or [])]
                    if len(kept) != len(rels):
                        self._relations[other_key] = kept
                self._note_removed_locked(key)

            # Remove from entity index (TAP-734) — under the lock: the helper
            # iterates and replaces token sets, and races unlocked with the
            # save path's _refresh_entity_index otherwise.
            with self._serialized():
                self._remove_entry_entities(key)

            # Audit (best-effort).
            self._persistence.append_audit(
                action="delete",
                key=key,
                extra={"tier": str(removed.tier)},
            )
            return True

    @staticmethod
    def _parse_relative_time(value: str) -> str:
        """Expand a relative time shorthand to an ISO-8601 UTC string.

        Accepts shorthands of the form ``Nd`` (days), ``Nw`` (weeks), or
        ``Nm`` (months, approximated as 30 days each).  Any other string is
        returned unchanged so that callers can pass ISO-8601 strings through
        transparently.

        Examples::

            "7d"  -> ISO string 7 days ago
            "2w"  -> ISO string 14 days ago
            "1m"  -> ISO string 30 days ago
            "2026-01-01T00:00:00Z" -> "2026-01-01T00:00:00Z" (passthrough)
        """
        m = re.fullmatch(r"(\d+)([dwm])", value.strip())
        if m is None:
            return value
        n, unit = int(m.group(1)), m.group(2)
        days = n if unit == "d" else n * 7 if unit == "w" else n * 30
        return (datetime.now(UTC) - timedelta(days=days)).isoformat()

    def _apply_search_filters(
        self,
        results: list[MemoryEntry],
        *,
        tags: list[str] | None,
        tier: str | None,
        scope: str | None,
        memory_group: str | None,
        include_historical: bool,
        as_of: str | None,
        include_contradicted: bool = False,
    ) -> list[MemoryEntry]:
        """Apply post-FTS attribute filters and temporal filtering.

        Extracted from ``search()`` to reduce its cyclomatic complexity.
        All filters are optional; unset parameters are skipped.
        """
        if tier is not None:
            results = [r for r in results if r.tier == tier]
        if scope is not None:
            results = [r for r in results if r.scope == scope]
        if memory_group is not None:
            results = [r for r in results if r.memory_group == memory_group]
        if tags:
            tag_set = set(tags)
            results = [r for r in results if tag_set.intersection(r.tags)]
        # Temporal filtering (EPIC-004 + GitHub #29)
        if not include_historical:
            results = [r for r in results if r.is_temporally_valid(as_of)]
        # Filter contradicted entries unless explicitly requested (TAP-5783).
        # This matches MemoryRetriever.search default behavior.
        if not include_contradicted:
            results = [r for r in results if not r.contradicted]
        return results

    def _append_group_memories(
        self,
        results: list[MemoryEntry],
        query: str,
        max_group_results: int,
    ) -> None:
        """Extend *results* in-place with Hive group-namespace matches (STORY-056.5).

        Extracted from ``search()`` to reduce its cyclomatic complexity and nesting.
        No-ops when ``self._groups`` is empty or ``self._hive_store`` is None.
        """
        if not self._groups or self._hive_store is None:
            return
        seen_keys = {r.key for r in results}
        for group_name in self._groups:
            try:
                with start_span(
                    SPAN_HIVE_SEARCH,
                    {"hive.group": group_name, "hive.namespace": group_name},
                ):
                    # Bare group name is canonical (matches PropagationEngine +
                    # search_with_groups). Also probe legacy ``group:<name>``
                    # rows written before the namespace unification fix.
                    group_results = self._hive_store.search(
                        query,
                        namespaces=[group_name, f"group:{group_name}"],
                        limit=max_group_results,
                    )
                for gr in group_results:
                    gk = gr.get("key", "")
                    if not gk or gk in seen_keys:
                        continue
                    seen_keys.add(gk)
                    # Convert hive dict to MemoryEntry for uniform return
                    try:
                        results.append(
                            MemoryEntry(
                                key=gk,
                                value=gr.get("value", ""),
                                tier=gr.get("tier", "pattern"),
                                confidence=gr.get("confidence", 0.5),
                                source=gr.get("source", "agent"),
                                source_agent=gr.get("source_agent", "unknown"),
                                tags=gr.get("tags", []) if isinstance(gr.get("tags"), list) else [],
                                agent_scope=f"group:{group_name}",
                            )
                        )
                    except Exception:
                        logger.warning(
                            "group_search_entry_convert_failed",
                            group=group_name,
                            key=gk,
                            exc_info=True,
                        )
            except Exception:
                logger.warning(
                    "group_search_failed",
                    group=group_name,
                    exc_info=True,
                )

    def search(
        self,
        query: str,
        tags: list[str] | None = None,
        tier: str | None = None,
        scope: str | None = None,
        memory_group: str | None = None,
        as_of: str | None = None,
        include_historical: bool = False,
        since: str | None = None,
        until: str | None = None,
        time_field: str = "created_at",
        include_group_memories: bool = False,
        max_group_results: int = 20,
        memory_class: str | None = None,
        include_contradicted: bool = False,
    ) -> list[MemoryEntry]:
        """Search via the Postgres FTS backend, with optional post-filters.

        Args:
            query: Search query string.
            tags: Filter by tags.
            tier: Filter by tier.
            scope: Filter by scope.
            memory_group: When set, restrict to this project-local group (GitHub #49).
            as_of: ISO-8601 timestamp for point-in-time temporal filtering.
                When set, only entries valid at that time are returned.
                When ``None`` (default), temporally invalid entries are excluded
                using the current time.
            include_historical: When True, include expired/superseded entries
                (GitHub #29, task 040.3). When False (default), entries whose
                ``invalid_at`` or ``valid_until`` is in the past are excluded.
            since: ISO-8601 UTC lower bound (inclusive) on *time_field* (Issue #70).
            until: ISO-8601 UTC upper bound (exclusive) on *time_field* (Issue #70).
            time_field: Column to filter on — ``created_at``, ``updated_at``,
                or ``last_accessed``. Defaults to ``created_at``.
            include_group_memories: When True and the store has declared groups,
                also search group namespaces in the Hive (STORY-056.5).
            max_group_results: Maximum results per group namespace (STORY-056.5).
            memory_class: TAP-733 — when set, restrict to entries with this semantic
                class (``"incident"``, ``"guidance"``, ``"decision"``, ``"convention"``).
                Pushed into the SQL WHERE clause for DB-level pre-filtering.
            include_contradicted: When True, include entries marked as contradicted
                by save-time conflict detection. When False (default), contradicted
                entries are excluded. Matches the default behavior of
                ``MemoryRetriever.search`` (TAP-5783).
        """
        self._metrics.increment("store.search")
        rm_increment_recall_total()
        _search_t0 = time.monotonic()
        with (
            start_span(SPAN_SEARCH) as _search_span,
            MetricsTimer(self._metrics, "store.search_ms"),
        ):
            # Expand relative shorthands ("7d", "2w", "1m") to ISO-8601 strings.
            if since is not None:
                since = self._parse_relative_time(since)
            if until is not None:
                until = self._parse_relative_time(until)
            results = self._persistence.search(
                query,
                memory_group=memory_group,
                since=since,
                until=until,
                time_field=time_field,
                as_of=as_of,
                memory_class=memory_class,
                # TAP-4586: exclude expired/superseded rows at the SQL layer so
                # they do not consume the top-K budget.  When the caller wants
                # historical rows, fetch them too and let the Python filter decide.
                include_expired=include_historical,
            )

            # TAP-5677 stage 3: lexical stages (AND, then the backend's OR
            # retry) found nothing — fall back to vector KNN when available.
            # Stands down for since/until/memory_class queries: those filters
            # live in the FTS SQL only, and a speculative semantic match must
            # never resurface rows a precision filter excluded.
            if not results and since is None and until is None and memory_class is None:
                results = self._knn_fallback_entries(
                    query, include_expired=include_historical, as_of=as_of
                )

            results = self._apply_search_filters(
                results,
                tags=tags,
                tier=tier,
                scope=scope,
                memory_group=memory_group,
                include_historical=include_historical,
                as_of=as_of,
                include_contradicted=include_contradicted,
            )

            # STORY-056.5: Group-aware recall — search group namespaces in Hive
            if include_group_memories:
                self._append_group_memories(results, query, max_group_results)

            self._metrics.increment("store.search.results", len(results))
            _search_elapsed_ms = (time.monotonic() - _search_t0) * 1000.0
            if _search_span is not None:
                _search_span.set_attribute("search.result_count", len(results))
                # STORY-070.12: standardised per-operation attributes
                _search_span.set_attribute(ATTR_ROWS_RETURNED, len(results))
                _search_span.set_attribute(ATTR_LATENCY_MS, _search_elapsed_ms)
                # TAP-2170: GenAI semconv v1.40.0 data source identity
                _search_span.set_attribute("gen_ai.data_source.id", GEN_AI_DATA_SOURCE_ID)
            rm_add_recall_latency_ms(_search_elapsed_ms)
            return results

    def _knn_fallback_entries(
        self,
        query: str,
        *,
        include_expired: bool,
        as_of: str | None,
    ) -> list[MemoryEntry]:
        """Semantic fallback for lexically unmatched queries (TAP-5677).

        Embeds *query* with the store's embedding provider and returns the
        nearest :data:`_KNN_FALLBACK_K` entries by cosine distance, closest
        first.  Requires both a configured embedding provider and a backend
        with ``knn_search`` (the Postgres backend); otherwise — or when
        embedding/KNN fails — returns ``[]`` so search degrades to its
        previous empty-result behaviour rather than raising.  Failures are
        logged; ``knn_search`` degradation flags stay the backend's concern.
        """
        knn = getattr(self._persistence, "knn_search", None)
        load_one = getattr(self._persistence, "load_one", None)
        if (
            not query.strip()
            or self._embedding_provider is None
            or not callable(knn)
            or not callable(load_one)
        ):
            return []
        try:
            embedding = self._embedding_provider.embed(query)
        except Exception:
            logger.warning("search.knn_fallback.embed_failed", exc_info=True)
            return []
        if not embedding:
            return []
        try:
            pairs = knn(
                list(embedding),
                _KNN_FALLBACK_K,
                include_expired=include_expired,
                as_of=as_of,
            )
        except Exception:
            logger.warning("search.knn_fallback.knn_failed", exc_info=True)
            return []
        entries: list[MemoryEntry] = []
        for key, _distance in pairs:
            entry = cast("MemoryEntry | None", load_one(key))
            if entry is not None:
                entries.append(entry)
        if entries:
            self._metrics.increment("store.search.knn_fallback")
        return entries
