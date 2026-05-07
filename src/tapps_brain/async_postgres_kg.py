"""PostgreSQL async backend for the first-class Knowledge Graph store.

EPIC-075 — KnowledgeGraphStore API + Entity Resolver (async variant).

Mirrors :mod:`tapps_brain.postgres_kg` exactly in behaviour, using async
psycopg3 cursors.  Both backends share the same SQL constants from
:mod:`tapps_brain._postgres_kg_sql`.  Parity is tested in
``tests/integration/test_kg_store.py``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog

from tapps_brain import _postgres_kg_sql as _sql
from tapps_brain.postgres_kg import (
    _INFERRED_CONFIDENCE_CAP,
    _EdgeDecayAdapter,
    _is_uuid,
    _row_to_dict,
)

if TYPE_CHECKING:
    from tapps_brain.postgres_connection import PostgresConnectionManager

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


class AsyncPostgresKnowledgeGraphStore:
    """Async PostgreSQL-backed Knowledge Graph store.

    Parity with :class:`~tapps_brain.postgres_kg.PostgresKnowledgeGraphStore`;
    every method has an identical signature and semantics.
    Uses async psycopg3 cursors via the
    :class:`~tapps_brain.postgres_connection.PostgresConnectionManager`
    async connection pool.

    Args:
        connection_manager: Active connection manager (must support async).
        project_id: Tenant identifier for RLS.
        brain_id: Logical brain identity.
        evidence_required: Same semantics as the sync backend.
    """

    def __init__(
        self,
        connection_manager: PostgresConnectionManager,
        *,
        project_id: str,
        brain_id: str,
        evidence_required: bool = True,
    ) -> None:
        self._cm = connection_manager
        self._project_id = project_id
        self._brain_id = brain_id
        self._evidence_required = evidence_required

    # ------------------------------------------------------------------
    # Connection helper
    # ------------------------------------------------------------------

    async def _scoped_conn(self) -> Any:
        """Return an async connection context bound to this store's project_id."""
        apc = getattr(self._cm, "async_project_context", None)
        if apc is not None:
            return apc(self._project_id)
        agc = getattr(self._cm, "async_get_connection", None)
        if agc is not None:
            return agc()
        msg = (
            "AsyncPostgresKnowledgeGraphStore requires a connection manager "
            "that exposes async_project_context() or async_get_connection()."
        )
        raise RuntimeError(msg)

    # ------------------------------------------------------------------
    # Entity operations
    # ------------------------------------------------------------------

    async def upsert_entity(
        self,
        *,
        entity_type: str,
        canonical_name: str,
        aliases: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        confidence: float = 0.6,
        source: str = "agent",
        source_agent: str = "unknown",
    ) -> str:
        """Async variant of :meth:`PostgresKnowledgeGraphStore.upsert_entity`."""
        aliases_json = json.dumps([a.lower() for a in (aliases or [])])
        metadata_json = json.dumps(metadata or {})

        async with await self._scoped_conn() as conn, conn.cursor() as cur:
            await cur.execute(
                _sql.UPSERT_ENTITY_SQL,
                (
                    self._project_id,
                    self._brain_id,
                    self._project_id,
                    entity_type,
                    canonical_name,
                    aliases_json,
                    metadata_json,
                    confidence,
                    source,
                    source_agent,
                ),
            )
            row = await cur.fetchone()
            entity_id = str(row[0])

        logger.debug(
            "kg.async.entity.upserted",
            entity_type=entity_type,
            canonical_name=canonical_name,
            entity_id=entity_id,
        )
        return entity_id

    # ------------------------------------------------------------------
    # Edge operations
    # ------------------------------------------------------------------

    async def upsert_edge(
        self,
        *,
        subject_entity_id: str,
        predicate: str,
        object_entity_id: str,
        evidence_id: str | None = None,
        edge_class: str | None = None,
        layer: str | None = None,
        profile_name: str | None = None,
        confidence: float = 0.6,
        source: str = "agent",
        source_agent: str = "unknown",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Async variant of :meth:`PostgresKnowledgeGraphStore.upsert_edge`."""
        if self._evidence_required and evidence_id is None:
            msg = (
                "upsert_edge: evidence_id is required when evidence_required=True. "
                "Call attach_evidence() first, then pass the returned ID here."
            )
            raise ValueError(msg)

        if evidence_id is None:
            confidence = min(confidence, _INFERRED_CONFIDENCE_CAP)

        metadata_json = json.dumps(metadata or {})

        async with await self._scoped_conn() as conn, conn.cursor() as cur:
            await cur.execute(
                _sql.GET_ACTIVE_EDGE_SQL,
                (self._brain_id, subject_entity_id, predicate, object_entity_id),
            )
            existing = await cur.fetchone()

            if existing is not None:
                # GET_ACTIVE_EDGE_SQL column order:
                # [0]=id [1]=confidence [2]=stability [3]=difficulty
                # [4]=last_reinforced [5]=reinforce_count [6]=source_agent
                # [7]=created_at [8]=updated_at
                edge_id = str(existing[0])
                new_s, new_d = self._compute_fsrs(
                    stability=float(existing[2] or 0.0),
                    difficulty=float(existing[3] or 0.0),
                    layer=layer,
                    last_reinforced=existing[4],
                    updated_at=existing[8],
                    was_useful=True,
                )
                await cur.execute(
                    _sql.REINFORCE_EDGE_SQL,
                    (new_s, new_d, edge_id, self._brain_id),
                )
            else:
                await cur.execute(
                    _sql.INSERT_EDGE_SQL,
                    (
                        self._project_id,
                        self._brain_id,
                        self._project_id,
                        subject_entity_id,
                        predicate,
                        object_entity_id,
                        edge_class,
                        layer,
                        profile_name,
                        confidence,
                        source,
                        source_agent,
                        source_agent,
                        metadata_json,
                    ),
                )
                row = await cur.fetchone()
                edge_id = str(row[0])

        logger.debug(
            "kg.async.edge.upserted",
            edge_id=edge_id,
            predicate=predicate,
            new_edge=(existing is None),
        )
        return edge_id

    # ------------------------------------------------------------------
    # Evidence
    # ------------------------------------------------------------------

    async def attach_evidence(
        self,
        *,
        edge_id: str | None = None,
        entity_id: str | None = None,
        source_type: str = "agent",
        source_id: str | None = None,
        source_key: str | None = None,
        source_uri: str | None = None,
        source_hash: str | None = None,
        source_span: str | None = None,
        quote: str | None = None,
        metadata: dict[str, Any] | None = None,
        source_agent: str = "unknown",
        confidence: float = 1.0,
        utility_score: float | None = None,
    ) -> str:
        """Async variant of :meth:`PostgresKnowledgeGraphStore.attach_evidence`."""
        if (edge_id is None) == (entity_id is None):
            msg = "attach_evidence: exactly one of edge_id or entity_id must be given."
            raise ValueError(msg)

        metadata_json = json.dumps(metadata or {})

        async with await self._scoped_conn() as conn, conn.cursor() as cur:
            await cur.execute(
                _sql.ATTACH_EVIDENCE_SQL,
                (
                    self._project_id,
                    self._brain_id,
                    self._project_id,
                    edge_id,
                    entity_id,
                    source_type,
                    source_id,
                    source_key,
                    source_uri,
                    source_hash,
                    source_span,
                    quote,
                    metadata_json,
                    source_agent,
                    confidence,
                    utility_score,
                ),
            )
            row = await cur.fetchone()
            evidence_id = str(row[0])

        logger.debug("kg.async.evidence.attached", evidence_id=evidence_id)
        return evidence_id

    # ------------------------------------------------------------------
    # Entity resolution
    # ------------------------------------------------------------------

    async def resolve_entity(
        self,
        entity_type: str,
        name: str,
    ) -> tuple[str | None, float, str]:
        """Async variant of :meth:`PostgresKnowledgeGraphStore.resolve_entity`."""
        if _is_uuid(name):
            async with await self._scoped_conn() as conn, conn.cursor() as cur:
                await cur.execute(_sql.GET_ENTITY_BY_ID_SQL, (name,))
                row = await cur.fetchone()
            if row is not None:
                return (str(row[0]), float(row[8]), "explicit_id")
            return (None, 0.0, "not_found")

        async with await self._scoped_conn() as conn, conn.cursor() as cur:
            await cur.execute(
                _sql.RESOLVE_ENTITY_EXACT_SQL,
                (self._brain_id, entity_type, name),
            )
            row = await cur.fetchone()
            if row is not None:
                return (str(row[0]), float(row[1]), "exact_match")

            await cur.execute(
                _sql.RESOLVE_ENTITY_BY_ALIAS_SQL,
                (self._brain_id, entity_type, name),
            )
            alias_rows = await cur.fetchmany(2)

        if not alias_rows:
            return (None, 0.0, "not_found")

        if len(alias_rows) == 1:
            return (str(alias_rows[0][0]), float(alias_rows[0][1]), "alias_match")

        logger.warning(
            "kg.async.resolve_entity.ambiguous_alias",
            entity_type=entity_type,
            name=name,
            match_count=len(alias_rows),
        )
        return (str(alias_rows[0][0]), float(alias_rows[0][1]), "ambiguous_alias")

    async def batch_resolve_entities(
        self,
        candidates: list[str],
    ) -> dict[str, tuple[str, float, str]]:
        """Async variant of :meth:`~PostgresKnowledgeGraphStore.batch_resolve_entities`.

        Batch-resolves a list of candidate strings in a single SQL round-trip.
        Exact canonical matches take precedence over alias matches.

        Returns:
            Mapping of ``lower(candidate)`` → ``(entity_id, confidence, reason)``.
            Unmatched candidates are absent from the result.
        """
        if not candidates:
            return {}

        norms = [c.lower() for c in candidates]
        result: dict[str, tuple[str, float, str]] = {}

        async with await self._scoped_conn() as conn, conn.cursor() as cur:
            # Pass 1 — exact canonical matches.
            await cur.execute(_sql.BATCH_RESOLVE_EXACT_SQL, (self._brain_id, norms))
            for row in await cur.fetchall():
                norm, entity_id, confidence = str(row[0]), str(row[1]), float(row[2])
                if norm not in result:
                    result[norm] = (entity_id, confidence, "exact_match")

            # Pass 2 — alias matches for still-unresolved candidates.
            unresolved = [n for n in norms if n not in result]
            if unresolved:
                await cur.execute(_sql.BATCH_RESOLVE_ALIAS_SQL, (self._brain_id, unresolved))
                alias_hits: dict[str, list[tuple[str, float]]] = {}
                for row in await cur.fetchall():
                    norm, entity_id, confidence = str(row[0]), str(row[1]), float(row[2])
                    alias_hits.setdefault(norm, []).append((entity_id, confidence))

                for norm, hits in alias_hits.items():
                    if norm in result:
                        continue
                    best_eid, best_conf = hits[0]
                    reason = "ambiguous_alias" if len(hits) > 1 else "alias_match"
                    if len(hits) > 1:
                        logger.warning(
                            "kg.async.batch_resolve.ambiguous_alias",
                            norm=norm,
                            match_count=len(hits),
                        )
                    result[norm] = (best_eid, best_conf, reason)

        return result

    # ------------------------------------------------------------------
    # Neighbour queries
    # ------------------------------------------------------------------

    async def get_neighbors(
        self,
        entity_id: str,
        *,
        direction: str = "both",
        predicate: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Async variant of :meth:`PostgresKnowledgeGraphStore.get_neighbors`."""
        _VALID_DIRECTIONS = {"out", "in", "both"}
        if direction not in _VALID_DIRECTIONS:
            msg = f"direction must be one of {_VALID_DIRECTIONS}, got {direction!r}"
            raise ValueError(msg)

        results: list[dict[str, Any]] = []

        async with await self._scoped_conn() as conn, conn.cursor() as cur:
            if direction in ("out", "both"):
                await cur.execute(
                    _sql.GET_OUTGOING_NEIGHBORS_SQL,
                    (self._brain_id, entity_id),
                )
                rows = await cur.fetchmany(limit)
                for row in rows:
                    d = _row_to_dict(row, cur.description)
                    d["direction"] = "out"
                    if predicate is None or d.get("predicate") == predicate:
                        results.append(d)

            if direction in ("in", "both") and len(results) < limit:
                remaining = limit - len(results)
                await cur.execute(
                    _sql.GET_INCOMING_NEIGHBORS_SQL,
                    (self._brain_id, entity_id),
                )
                rows = await cur.fetchmany(remaining)
                for row in rows:
                    d = _row_to_dict(row, cur.description)
                    d["direction"] = "in"
                    if predicate is None or d.get("predicate") == predicate:
                        results.append(d)

        return results

    # ------------------------------------------------------------------
    # Edge lifecycle mutations
    # ------------------------------------------------------------------

    async def reinforce_edge(
        self,
        edge_id: str,
        was_useful: bool = True,
    ) -> bool:
        """Async variant of :meth:`PostgresKnowledgeGraphStore.reinforce_edge`."""
        async with await self._scoped_conn() as conn, conn.cursor() as cur:
            await cur.execute(_sql.GET_EDGE_FOR_REINFORCE_SQL, (edge_id,))
            row = await cur.fetchone()
            if row is None:
                return False

            new_s, new_d = self._compute_fsrs(
                stability=float(row[1] or 0.0),
                difficulty=float(row[2] or 0.0),
                layer=row[3],
                last_reinforced=row[4],
                updated_at=row[5],
                was_useful=was_useful,
            )

            await cur.execute(
                _sql.REINFORCE_EDGE_SQL,
                (new_s, new_d, edge_id, self._brain_id),
            )
            updated: bool = bool(cur.rowcount > 0)

        if updated:
            logger.debug("kg.async.edge.reinforced", edge_id=edge_id)
        else:
            logger.debug("kg.async.edge.reinforce_debounced", edge_id=edge_id)
        return updated

    async def mark_edge_stale(
        self,
        edge_id: str,
        reason: str | None = None,
    ) -> bool:
        """Async variant of :meth:`PostgresKnowledgeGraphStore.mark_edge_stale`."""
        async with await self._scoped_conn() as conn, conn.cursor() as cur:
            await cur.execute(
                _sql.MARK_EDGE_STALE_SQL,
                (reason, edge_id, self._brain_id),
            )
            updated: bool = bool(cur.rowcount > 0)
        return updated

    async def supersede_edge(
        self,
        old_edge_id: str,
        *,
        subject_entity_id: str,
        predicate: str,
        object_entity_id: str,
        evidence_id: str | None = None,
        confidence: float = 0.6,
        source_agent: str = "unknown",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Async variant of :meth:`PostgresKnowledgeGraphStore.supersede_edge`."""
        new_edge_id = await self.upsert_edge(
            subject_entity_id=subject_entity_id,
            predicate=predicate,
            object_entity_id=object_entity_id,
            evidence_id=evidence_id,
            confidence=confidence,
            source_agent=source_agent,
            metadata=metadata,
        )

        async with await self._scoped_conn() as conn, conn.cursor() as cur:
            await cur.execute(
                _sql.SUPERSEDE_EDGE_SQL,
                (new_edge_id, old_edge_id, self._brain_id),
            )

        logger.debug(
            "kg.async.edge.superseded",
            old_edge_id=old_edge_id,
            new_edge_id=new_edge_id,
        )
        return new_edge_id

    async def contradict_edge(
        self,
        edge_id: str,
        reason: str,
    ) -> bool:
        """Async variant of :meth:`PostgresKnowledgeGraphStore.contradict_edge`."""
        async with await self._scoped_conn() as conn, conn.cursor() as cur:
            await cur.execute(
                _sql.CONTRADICT_EDGE_SQL,
                (reason, edge_id, self._brain_id),
            )
            updated: bool = bool(cur.rowcount > 0)
        if updated:
            logger.debug("kg.async.edge.contradicted", edge_id=edge_id)
        return updated

    # ------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """No-op — the connection manager owns the pool lifecycle."""

    # ------------------------------------------------------------------
    # Internal: FSRS helper (reused from sync backend)
    # ------------------------------------------------------------------

    def _compute_fsrs(
        self,
        *,
        stability: float,
        difficulty: float,
        layer: str | None,
        last_reinforced: Any,
        updated_at: Any,
        was_useful: bool,
    ) -> tuple[float, float]:
        """Compute new (stability, difficulty) via the shared FSRS helper."""
        from tapps_brain.decay import DecayConfig, update_stability

        adapter = _EdgeDecayAdapter(
            stability=stability,
            difficulty=difficulty,
            layer=layer,
            last_reinforced=last_reinforced,
            updated_at=updated_at,
        )
        config = DecayConfig()
        return update_stability(adapter, config, was_useful)  # type: ignore[arg-type]
