"""Relation-graph methods for :class:`~tapps_brain.store.MemoryStore` (TAP-2833).

Extracted from ``store.py`` as a mixin to shrink the core module.  Covers the
public relation API (count / save / load / get / batch), the relation-graph
health-check helpers, and the BFS / filter query methods.  Behaviour is
identical to the original in-class definitions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tapps_brain._store_base import _MemoryStoreBase

if TYPE_CHECKING:
    from collections.abc import Iterator
    from datetime import datetime

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
        with self._lock:
            self._relations[key] = self._persistence.load_relations(key)

    def load_relations(self, key: str) -> list[dict[str, Any]]:
        """Reload relations for *key* from persistence and refresh the cache.

        Public wrapper for ``_persistence.load_relations`` (TAP-510).
        Use after an external writer has mutated the underlying store and
        the in-memory cache may be stale.  Returns the freshly loaded
        list of relation dicts (same shape as :meth:`get_relations`).
        """
        loaded = self._persistence.load_relations(key)
        with self._lock:
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
        return list(self._relations.get(key, []))

    def get_relations_batch(self, keys: list[str]) -> dict[str, list[dict[str, Any]]]:
        """Return relations for multiple keys in one call (STORY-048.2).

        Args:
            keys: Memory entry keys to look up.

        Returns:
            Dict mapping each requested key to its list of relation dicts.
            Keys with no relations map to an empty list.
        """
        return {key: list(self._relations.get(key, [])) for key in keys}

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
        """Count relation records that reference keys no longer in the store.

        Uses the in-memory ``_relations`` cache (kept in sync with the
        persistence layer by :meth:`save_relations` / :meth:`load_relations`)
        to avoid a Postgres round-trip and to eliminate the TOCTOU window
        that would arise from fetching relations outside ``_lock`` and then
        snapshotting entry keys inside it (TAP-722).

        Returns:
            Number of ``source_entry_keys`` references in any cached relation
            that have no corresponding entry in the in-memory store.  Relations
            are indexed by ``source_entry_key`` in the cache, so a relation
            with two missing source keys contributes 2 to the count — matching
            the semantics of the original per-reference count.
        """
        with self._serialized():
            entry_keys = set(self._entries.keys())
            return sum(
                len(rels) for src_key, rels in self._relations.items() if src_key not in entry_keys
            )

    def count_expired_entries(self, now: datetime | None = None) -> int:
        """Count entries whose ``valid_at`` timestamp lies in the past.

        Uses a proper :class:`~datetime.datetime` comparison instead of
        ISO string lexicographic ordering, so the count is correct even
        for timestamps with varying timezone representations (TAP-722).

        Args:
            now: Reference timestamp (UTC).  Defaults to
                ``datetime.now(UTC)`` when *None*.

        Returns:
            Number of entries whose ``valid_at`` field is non-*None* and
            falls before *now*.
        """
        from datetime import UTC
        from datetime import datetime as _datetime

        _now = now if now is not None else _datetime.now(tz=UTC)

        with self._serialized():
            entries = list(self._entries.values())

        expired = 0
        for entry in entries:
            valid_at_str: str | None = getattr(entry, "valid_at", None)
            if valid_at_str is None:
                continue
            try:
                valid_at_dt = _datetime.fromisoformat(valid_at_str)
                if valid_at_dt.tzinfo is None:
                    valid_at_dt = valid_at_dt.replace(tzinfo=UTC)
            except (ValueError, TypeError):
                continue
            if valid_at_dt < _now:
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
        with self._serialized():
            if key not in self._entries:
                raise KeyError(key)

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
                    # Collect entities from current_key's relations
                    for rel in self._relations.get(current_key, []):
                        for entity in (rel["subject"].lower(), rel["object_entity"].lower()):
                            for neighbor_key in entity_to_keys.get(entity, set()):
                                if neighbor_key not in visited:
                                    visited.add(neighbor_key)
                                    result.append((neighbor_key, hop))
                                    next_frontier.add(neighbor_key)
                frontier = next_frontier

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
