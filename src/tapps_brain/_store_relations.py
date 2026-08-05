"""Relation-graph methods for :class:`~tapps_brain.store.MemoryStore` (TAP-2833).

Extracted from ``store.py`` as a mixin to shrink the core module.  Covers the
public relation API (count / save / load / get / batch), the relation-graph
health-check helpers, and the BFS / filter query methods.  Behaviour is
identical to the original in-class definitions.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from tapps_brain._store_base import _MemoryStoreBase

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tapps_brain.models import MemoryEntry
    from tapps_brain.relations import RelationEntry


class RelationsMixin(_MemoryStoreBase):
    """Relation-graph persistence + query methods (TAP-2833)."""

    def count_relations(self) -> int:
        """Return the total number of stored relation triples."""
        return self._persistence.count_relations()

    def save_relations(self, key: str, relations: list[RelationEntry]) -> None:
        """Persist *relations* for *key* and refresh the in-memory cache.

        Public wrapper for ``_persistence.save_relations`` (TAP-510) so
        callers (auto-consolidation, future graph rebuilders) don't have
        to reach into ``_persistence`` / ``_lock`` / ``_relations``
        directly.

        Accepts a list of :class:`~tapps_brain.relations.RelationEntry`
        — the same type produced by extraction and merging.  The
        in-memory cache is rebuilt from the persistence layer under
        ``_lock`` so concurrent readers see the old or new set, never a
        partial write.
        """
        self._persistence.save_relations(key, relations)
        with self._serialized():
            self._relations[key] = self._persistence.load_relations(key)

    def load_relations(self, key: str) -> list[dict[str, Any]]:
        """Reload relations for *key* from persistence and refresh the cache.

        Public wrapper for ``_persistence.load_relations`` (TAP-510).
        Use after an external writer has mutated the underlying store and
        the in-memory cache may be stale.  Returns the freshly loaded
        list of relation dicts (same shape as :meth:`get_relations`).
        """
        loaded = self._persistence.load_relations(key)
        with self._serialized():
            self._relations[key] = list(loaded)
        return list(loaded)

    def get_relations(self, key: str) -> list[dict[str, Any]]:
        """Return all relations associated with a memory entry key.

        Args:
            key: The memory entry key.

        Returns:
            List of relation dicts with subject, predicate, object_entity,
            source_entry_keys, confidence, and created_at.
        """
        with self._serialized():
            cached = self._relations.get(key)
            if cached is not None:
                return list(cached)
        loaded = self._persistence.load_relations(key)
        with self._serialized():
            self._relations[key] = list(loaded)
            return list(loaded)

    def get_relations_batch(self, keys: list[str]) -> dict[str, list[dict[str, Any]]]:
        """Return relations for multiple keys in one call (STORY-048.2).

        Args:
            keys: Memory entry keys to look up.

        Returns:
            Dict mapping each requested key to its list of relation dicts.
            Keys with no relations map to an empty list.
        """
        return {key: self.get_relations(key) for key in keys}

    def _rebuild_relations_cache_from_durable(self) -> None:
        """Rebuild the in-memory relations cache from the durable store.

        No-op when the backend does not expose ``list_relations``.  The cache
        is *replaced*, not merged — every cached relation is write-through
        from the durable store, so merging would only preserve stale buckets
        for durably deleted relations (double-counting them as orphans and
        resurrecting deleted graph edges).
        """
        list_rels = getattr(self._persistence, "list_relations", None)
        if not callable(list_rels):
            return
        all_relations = list_rels()
        with self._serialized():
            rebuilt: dict[str, list[dict[str, Any]]] = {}
            for rel in all_relations:
                for src_key in rel.get("source_entry_keys") or []:
                    rebuilt.setdefault(str(src_key), []).append(rel)
            self._relations.clear()
            self._relations.update(rebuilt)

    # ------------------------------------------------------------------
    # Health-check helpers (TAP-722)
    # ------------------------------------------------------------------

    def iter_active_entries(self) -> Iterator[MemoryEntry]:
        """Yield a thread-safe snapshot of every current entry (TAP-722).

        Acquires the internal lock for the minimum time needed to copy the
        entry dict, then yields from the snapshot so callers never hold a
        reference to mutable internal state.

        This is the preferred public alternative to accessing ``_entries``
        directly from outside the store.
        """
        with self._serialized():
            entries = list(self._entries.values())
        yield from entries

    def count_orphaned_relations(self) -> int:
        """Count ``source_entry_keys`` refs that point at missing store entries.

        Scans every cached relation's ``source_entry_keys`` list so a single
        relation that names two missing keys contributes 2 (documented
        per-reference semantics).
        """
        # Reconciliation counter: must see the durable set, not the capped
        # cache view, or over-cap rows are miscounted as missing (TAP-5633).
        self._merge_durable_entries(allow_over_cap=True)
        self._rebuild_relations_cache_from_durable()

        with self._serialized():
            entry_keys = set(self._entries.keys())
            orphaned = 0
            seen_rel_ids: set[int] = set()
            for rels in self._relations.values():
                for rel in rels:
                    rid = id(rel)
                    if rid in seen_rel_ids:
                        continue
                    seen_rel_ids.add(rid)
                    for src in rel.get("source_entry_keys", []):
                        if src not in entry_keys:
                            orphaned += 1
            return orphaned

    def count_expired_entries(self, now: datetime | None = None) -> int:
        """Count entries whose validity window has ended.

        An entry is expired when ``invalid_at`` (or ``valid_until``) is set and
        lies at or before *now*.  ``valid_at`` / ``valid_from`` are start-of-
        truth fields and must not be treated as expiry.
        """
        _now = now if now is not None else datetime.now(tz=UTC)

        # Reconciliation counter over the durable set (TAP-5633).
        self._merge_durable_entries(allow_over_cap=True)
        with self._serialized():
            entries = list(self._entries.values())

        expired = 0
        for entry in entries:
            end_str: str | None = getattr(entry, "invalid_at", None)
            if not end_str:
                end_str = getattr(entry, "valid_until", None) or None
                if end_str == "":
                    end_str = None
            if end_str is None:
                continue
            try:
                end_dt = datetime.fromisoformat(end_str)
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=UTC)
            except (ValueError, TypeError):
                continue
            if end_dt <= _now:
                expired += 1
        return expired

    def find_related(
        self,
        key: str,
        *,
        max_hops: int = 2,
    ) -> list[tuple[str, int]]:
        """Find entries related to *key* via BFS traversal of the relation graph.

        Two entries are considered connected when they share an entity
        (subject or object_entity) in their extracted relations.

        Args:
            key: Starting entry key.
            max_hops: Maximum traversal depth (default 2).

        Returns:
            List of ``(entry_key, hop_distance)`` tuples, ordered by hop
            distance (ascending) then key name.  The starting key is
            **not** included in the results.

        Raises:
            KeyError: If *key* does not exist in the store.
        """
        if self._ensure_entry_cached(key) is None:
            raise KeyError(key)

        # Refresh relation graph from durable store so cold/missed edges participate.
        self._rebuild_relations_cache_from_durable()

        with self._serialized():
            # Build entity -> set[entry_key] index from all relations
            entity_to_keys: dict[str, set[str]] = {}
            for entry_key, rels in self._relations.items():
                for rel in rels:
                    for entity in (rel["subject"].lower(), rel["object_entity"].lower()):
                        entity_to_keys.setdefault(entity, set()).add(entry_key)

            # BFS
            visited: set[str] = {key}
            result: list[tuple[str, int]] = []
            frontier: set[str] = {key}

            for hop in range(1, max_hops + 1):
                next_frontier: set[str] = set()
                for current_key in frontier:
                    for rel in self._relations.get(current_key, []):
                        for entity in (rel["subject"].lower(), rel["object_entity"].lower()):
                            for neighbor_key in entity_to_keys.get(entity, set()):
                                if neighbor_key not in visited:
                                    visited.add(neighbor_key)
                                    result.append((neighbor_key, hop))
                                    next_frontier.add(neighbor_key)
                frontier = next_frontier
                if not frontier:
                    break

        # Sort by hop distance, then key name for determinism
        result.sort(key=lambda t: (t[1], t[0]))
        return result

    def query_relations(
        self,
        *,
        subject: str | None = None,
        predicate: str | None = None,
        object_entity: str | None = None,
    ) -> list[dict[str, Any]]:
        """Filter relations by subject, predicate, and/or object_entity.

        All filters use case-insensitive matching.  When multiple filters are
        provided they are combined with AND logic.  Passing no filters returns
        all relations.

        Args:
            subject: Filter by subject entity.
            predicate: Filter by predicate/relationship type.
            object_entity: Filter by object entity.

        Returns:
            List of matching relation dicts.
        """
        with self._serialized():
            matches: list[dict[str, Any]] = []
            for rels in self._relations.values():
                for rel in rels:
                    if subject is not None and rel["subject"].lower() != subject.lower():
                        continue
                    if predicate is not None and rel["predicate"].lower() != predicate.lower():
                        continue
                    if (
                        object_entity is not None
                        and rel["object_entity"].lower() != object_entity.lower()
                    ):
                        continue
                    matches.append(dict(rel))
            # Deduplicate by (subject, predicate, object_entity) triple
            seen: set[tuple[str, str, str]] = set()
            deduped: list[dict[str, Any]] = []
            for m in matches:
                triple = (m["subject"].lower(), m["predicate"].lower(), m["object_entity"].lower())
                if triple not in seen:
                    seen.add(triple)
                    deduped.append(m)
        return deduped
