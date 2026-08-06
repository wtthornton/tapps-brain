"""PostgreSQL sync backend for the first-class Knowledge Graph store.

EPIC-075 — KnowledgeGraphStore API + Entity Resolver.

Mirrors the structure of :mod:`tapps_brain.postgres_private` so both backends
share the same connection-management and RLS-enforcement patterns.  All SQL
strings live in :mod:`tapps_brain._postgres_kg_sql`; the async variant
(:mod:`tapps_brain.async_postgres_kg`) imports the same module.

Design invariants
-----------------
* Every write is scoped to ``(tenant_id=project_id, brain_id)`` supplied at
  construction.  RLS on the underlying tables enforces this at the DB layer.
* Evidence is required for edge writes by default (``evidence_required=True``).
  Pass ``evidence_required=False`` for the inferred/low-confidence path; that
  path caps edge confidence at 0.4 (ADR-009).
* Edge reinforcement is debounced: a second call within 60 s returns ``False``
  without touching the DB (the REINFORCE_EDGE_SQL WHERE clause enforces this).
* FSRS-style stability / difficulty updates reuse :func:`tapps_brain.decay.update_stability`
  via a thin adapter — no duplication.
* Raises ``ValueError`` on a missing or non-Postgres DSN (no SQLite fallback,
  ADR-007).
"""

from __future__ import annotations

import json
import threading
import uuid as _uuid_mod
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

import structlog

from tapps_brain import _postgres_kg_sql as _sql

if TYPE_CHECKING:
    from tapps_brain.postgres_connection import PostgresConnectionManager

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

#: Confidence cap for the inferred (evidence-free) edge path (ADR-009).
_INFERRED_CONFIDENCE_CAP = 0.4


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def json_safe_kg_value(value: Any) -> Any:
    """Coerce psycopg row scalars to JSON-serializable primitives (TAP-4275)."""
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, _uuid_mod.UUID):
        return str(value)
    return value


def _row_to_dict(row: Any, description: Any) -> dict[str, Any]:
    """Convert a psycopg row + cursor.description to a plain dict."""
    if row is None:
        return {}
    return {desc.name: json_safe_kg_value(row[i]) for i, desc in enumerate(description)}


def _is_uuid(value: str) -> bool:
    """Return True if *value* looks like a hyphenated UUID string."""
    try:
        _uuid_mod.UUID(value)
        return True
    except (ValueError, AttributeError):
        return False


class _EdgeDecayAdapter:
    """Thin shim that exposes the fields ``decay.update_stability`` needs.

    ``update_stability`` accesses ``entry.stability``, ``entry.difficulty``,
    ``entry.tier``, ``entry.last_reinforced``, and ``entry.updated_at``.
    This adapter satisfies all five without importing MemoryEntry.
    """

    __slots__ = ("difficulty", "last_reinforced", "stability", "tier", "updated_at")

    def __init__(
        self,
        *,
        stability: float,
        difficulty: float,
        layer: str | None,
        last_reinforced: Any,  # datetime | None (psycopg returns datetime)
        updated_at: Any,  # datetime
    ) -> None:
        self.stability = stability
        self.difficulty = difficulty
        # Map edge layer → MemoryTier-compatible string used by _get_half_life.
        # Schema allows "domain"; decay only knows MemoryTier names — map it.
        _layer = (layer or "pattern").strip().lower()
        if _layer == "domain":
            _layer = "architectural"
        self.tier = _layer
        # decay.py expects ISO-8601 strings; psycopg returns datetime objects.
        self.last_reinforced = last_reinforced.isoformat() if last_reinforced is not None else None
        self.updated_at = updated_at.isoformat() if updated_at is not None else None


# ---------------------------------------------------------------------------
# Sync backend
# ---------------------------------------------------------------------------


class PostgresKnowledgeGraphStore:
    """Sync PostgreSQL-backed Knowledge Graph store.

    Satisfies the ``KnowledgeGraphBackend`` protocol (``_protocols.py``).
    All operations are scoped to the ``(project_id, brain_id)`` pair set at
    construction.

    Args:
        connection_manager: Active
            :class:`~tapps_brain.postgres_connection.PostgresConnectionManager`.
        project_id: Tenant identifier (= ``app.project_id`` for RLS).
        brain_id: Logical brain identity (agent-level scope for entity uniqueness).
        evidence_required: When ``True`` (default), :meth:`upsert_edge` raises
            ``ValueError`` if no *evidence_id* is supplied.  When ``False``,
            the inferred path is used and edge confidence is capped at
            :data:`_INFERRED_CONFIDENCE_CAP`.
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
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Connection helper — enforces tenant RLS
    # ------------------------------------------------------------------

    def _scoped_conn(self) -> Any:
        """Return a connection context bound to this store's project_id."""
        pc = getattr(self._cm, "project_context", None)
        if pc is not None:
            return pc(self._project_id)
        return self._cm.get_connection()

    # ------------------------------------------------------------------
    # Entity operations
    # ------------------------------------------------------------------

    def upsert_entity(
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
        """Insert or update a KG entity; return its UUID string."""
        aliases_json = json.dumps([a.lower() for a in (aliases or [])])
        metadata_json = json.dumps(metadata or {})

        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.execute(
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
            row = cur.fetchone()
            entity_id = str(row[0])

        logger.debug(
            "kg.entity.upserted",
            entity_type=entity_type,
            canonical_name=canonical_name,
            entity_id=entity_id,
        )
        return entity_id

    # ------------------------------------------------------------------
    # Edge operations
    # ------------------------------------------------------------------

    def upsert_edge(
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
        """Insert or reinforce an edge; return its UUID string.

        Raises:
            ValueError: When ``evidence_required=True`` and *evidence_id* is ``None``.
        """
        if self._evidence_required and evidence_id is None:
            msg = (
                "upsert_edge: evidence_id is required when evidence_required=True. "
                "Call attach_evidence() first, then pass the returned ID here. "
                "Set evidence_required=False on the backend for the inferred path "
                "(confidence will be capped at 0.4)."
            )
            raise ValueError(msg)

        # Cap confidence on the inferred (evidence-free) path.
        if evidence_id is None:
            confidence = min(confidence, _INFERRED_CONFIDENCE_CAP)

        metadata_json = json.dumps(metadata or {})

        with self._scoped_conn() as conn, conn.cursor() as cur:
            # Check for an existing active edge (partial unique index).
            cur.execute(
                _sql.GET_ACTIVE_EDGE_SQL,
                (
                    self._brain_id,
                    subject_entity_id,
                    predicate,
                    object_entity_id,
                ),
            )
            existing = cur.fetchone()

            if existing is not None:
                # Reinforce the existing edge via the debounced UPDATE.
                # GET_ACTIVE_EDGE_SQL column order:
                # [0]=id [1]=confidence [2]=stability [3]=difficulty
                # [4]=last_reinforced [5]=reinforce_count [6]=source_agent
                # [7]=created_at [8]=updated_at
                edge_id = str(existing[0])
                existing_stability = float(existing[2] or 0.0)
                existing_difficulty = float(existing[3] or 0.0)
                existing_last_reinforced = existing[4]
                existing_updated_at = existing[8]

                new_s, new_d = self._compute_fsrs(
                    stability=existing_stability,
                    difficulty=existing_difficulty,
                    layer=layer,
                    last_reinforced=existing_last_reinforced,
                    updated_at=existing_updated_at,
                    was_useful=True,
                )
                cur.execute(
                    _sql.REINFORCE_EDGE_SQL,
                    (new_s, new_d, edge_id, self._brain_id),
                )
            else:
                # Insert new edge.
                cur.execute(
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
                row = cur.fetchone()
                edge_id = str(row[0])

        logger.debug(
            "kg.edge.upserted",
            edge_id=edge_id,
            predicate=predicate,
            new_edge=(existing is None),
        )
        return edge_id

    # ------------------------------------------------------------------
    # Evidence
    # ------------------------------------------------------------------

    def attach_evidence(
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
        """Attach evidence to an edge XOR entity; return evidence UUID string.

        Raises:
            ValueError: When both or neither of *edge_id* / *entity_id* are given.
        """
        if (edge_id is None) == (entity_id is None):
            msg = "attach_evidence: exactly one of edge_id or entity_id must be given."
            raise ValueError(msg)

        metadata_json = json.dumps(metadata or {})

        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.execute(
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
            row = cur.fetchone()
            evidence_id = str(row[0])

        logger.debug("kg.evidence.attached", evidence_id=evidence_id)
        return evidence_id

    # ------------------------------------------------------------------
    # Entity resolution
    # ------------------------------------------------------------------

    def resolve_entity(
        self,
        entity_type: str,
        name: str,
    ) -> tuple[str | None, float, str]:
        """Deterministic entity resolver.

        Precedence:
        1. If *name* is a UUID string, look up directly.
        2. Exact canonical_name_norm match.
        3. Alias containment match (up to 2 rows to detect ambiguity).

        Returns:
            ``(entity_id, confidence, reason)``
        """
        # Phase 0: explicit UUID ID supplied as name.
        if _is_uuid(name):
            with self._scoped_conn() as conn, conn.cursor() as cur:
                cur.execute(_sql.GET_ENTITY_BY_ID_SQL, (name,))
                row = cur.fetchone()
            if row is not None:
                return (str(row[0]), float(row[8]), "explicit_id")
            return (None, 0.0, "not_found")

        # Phase 1: exact canonical match.
        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.execute(
                _sql.RESOLVE_ENTITY_EXACT_SQL,
                (self._brain_id, entity_type, name),
            )
            row = cur.fetchone()
            if row is not None:
                return (str(row[0]), float(row[1]), "exact_match")

            # Phase 2: alias lookup.
            cur.execute(
                _sql.RESOLVE_ENTITY_BY_ALIAS_SQL,
                (self._brain_id, entity_type, name),
            )
            alias_rows = cur.fetchmany(2)

        if not alias_rows:
            return (None, 0.0, "not_found")

        if len(alias_rows) == 1:
            return (str(alias_rows[0][0]), float(alias_rows[0][1]), "alias_match")

        # Ambiguous — return highest-confidence match with a warning.
        logger.warning(
            "kg.resolve_entity.ambiguous_alias",
            entity_type=entity_type,
            name=name,
            match_count=len(alias_rows),
        )
        return (str(alias_rows[0][0]), float(alias_rows[0][1]), "ambiguous_alias")

    def batch_resolve_entities(
        self,
        candidates: list[str],
    ) -> dict[str, tuple[str, float, str]]:
        """Batch-resolve a list of candidate strings in a single SQL round-trip.

        Each candidate is lower-cased before lookup.  Exact canonical matches
        take precedence over alias matches.  Ambiguous alias matches (>1 entity)
        select the highest-confidence entity and tag the reason as
        ``"ambiguous_alias"``.

        Args:
            candidates: Raw surface strings to resolve.

        Returns:
            Mapping of ``lower(candidate)`` → ``(entity_id, confidence, reason)``.
            Unmatched candidates are absent from the result.
        """
        if not candidates:
            return {}

        norms = [c.lower() for c in candidates]

        result: dict[str, tuple[str, float, str]] = {}

        with self._scoped_conn() as conn, conn.cursor() as cur:
            # Pass 1 — exact canonical matches.
            cur.execute(_sql.BATCH_RESOLVE_EXACT_SQL, (self._brain_id, norms))
            for row in cur.fetchall():
                norm, entity_id, confidence = str(row[0]), str(row[1]), float(row[2])
                if norm not in result:
                    result[norm] = (entity_id, confidence, "exact_match")

            # Pass 2 — alias matches for still-unresolved candidates.
            unresolved = [n for n in norms if n not in result]
            if unresolved:
                cur.execute(_sql.BATCH_RESOLVE_ALIAS_SQL, (self._brain_id, unresolved))
                alias_hits: dict[str, list[tuple[str, float]]] = {}
                for row in cur.fetchall():
                    norm, entity_id, confidence = str(row[0]), str(row[1]), float(row[2])
                    alias_hits.setdefault(norm, []).append((entity_id, confidence))

                for norm, hits in alias_hits.items():
                    if norm in result:
                        continue
                    best_eid, best_conf = hits[0]
                    reason = "ambiguous_alias" if len(hits) > 1 else "alias_match"
                    if len(hits) > 1:
                        logger.warning(
                            "kg.batch_resolve.ambiguous_alias",
                            norm=norm,
                            match_count=len(hits),
                        )
                    result[norm] = (best_eid, best_conf, reason)

        return result

    # ------------------------------------------------------------------
    # Neighbour queries
    # ------------------------------------------------------------------

    def get_neighbors(
        self,
        entity_id: str,
        *,
        direction: str = "both",
        predicate: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return active neighbouring entities and connecting edges.

        Args:
            entity_id: UUID string of the focal entity.
            direction: ``"out"`` (outgoing), ``"in"`` (incoming), or ``"both"``.
            predicate: Optional predicate filter.
            limit: Max neighbours to return per direction.

        Returns:
            List of dicts with keys: ``edge_id``, ``predicate``,
            ``edge_confidence``, ``neighbor_id``, ``entity_type``,
            ``canonical_name``, ``entity_confidence``, ``direction``.
        """
        _valid_directions = {"out", "in", "both"}
        if direction not in _valid_directions:
            msg = f"direction must be one of {_valid_directions}, got {direction!r}"
            raise ValueError(msg)

        pf = predicate
        with self._scoped_conn() as conn, conn.cursor() as cur:
            if direction == "both":
                arm = (self._brain_id, entity_id, pf, pf)
                cur.execute(
                    _sql.GET_BOTH_NEIGHBORS_SQL,
                    (*arm, *arm, limit),
                )
            elif direction == "out":
                cur.execute(
                    _sql.GET_OUTGOING_NEIGHBORS_SQL,
                    (self._brain_id, entity_id, pf, pf, limit),
                )
            else:
                cur.execute(
                    _sql.GET_INCOMING_NEIGHBORS_SQL,
                    (self._brain_id, entity_id, pf, pf, limit),
                )
            return [_row_to_dict(row, cur.description) for row in cur.fetchall()]

    def get_neighbors_multi(
        self,
        entity_ids: list[str],
        *,
        hops: int = 1,
        limit: int = 100,
        predicate_filter: str | None = None,
        include_historical: bool = False,
    ) -> list[dict[str, Any]]:
        """Batch neighbourhood query for multiple focal entities (STORY-076.2).

        Args:
            entity_ids:        UUID strings of focal entities.
            hops:              Neighbourhood depth: 1 (direct neighbours) or 2
                               (two-hop recursive CTE). Values > 2 are clamped to 2.
            limit:             Maximum number of edge rows returned in total.
            predicate_filter:  When set, only edges with this predicate are returned.
            include_historical: Include stale, contradicted, and superseded edges.

        Returns:
            List of dicts with keys: ``edge_id``, ``predicate``, ``edge_confidence``,
            ``stability``, ``difficulty``, ``last_reinforced``, ``edge_updated_at``,
            ``edge_status``, ``contradicted``, ``reinforce_count``,
            ``useful_access_count``, ``access_count``, ``source``, ``evidence_count``,
            ``neighbor_id``, ``entity_type``, ``canonical_name``,
            ``entity_confidence``, ``hop``, and for 1-hop rows ``direction``
            (``"out"`` / ``"in"``) so callers can orient edges correctly.
        """
        if not entity_ids:
            return []

        hops = max(1, min(hops, 2))
        ih = include_historical
        pf = predicate_filter  # None or str — pushed to SQL

        with self._scoped_conn() as conn, conn.cursor() as cur:
            if hops == 1:
                # Params repeated once per UNION arm (out + in).
                arm = (self._brain_id, entity_ids, ih, ih, pf, pf)
                cur.execute(
                    _sql.GET_MULTI_NEIGHBORS_1HOP_SQL,
                    (*arm, *arm, limit),
                )
            else:
                # Parenthesized base out+in, then one undirected recursive arm.
                base_arm = (self._brain_id, entity_ids, ih, ih, pf, pf)
                rec_arm = (self._brain_id, ih, ih, pf, pf, hops)
                cur.execute(
                    _sql.GET_MULTI_NEIGHBORS_2HOP_SQL,
                    (*base_arm, *base_arm, *rec_arm, limit),
                )
            rows = cur.fetchall()
            results: list[dict[str, Any]] = [_row_to_dict(row, cur.description) for row in rows]

        return results

    def graph_health_counts(self) -> dict[str, int]:
        """Aggregate KG-graph health counts for this brain (P5).

        Returns ``entities_active``, ``orphan_entities`` (active entity with no
        active edge on either side), ``edges_total``, ``edges_active``,
        ``edges_stale``, ``edges_superseded``, ``edges_contradicted``. Feeds
        :func:`tapps_brain.visual_snapshot.build_kg_health`.
        """
        out: dict[str, int] = {
            "entities_active": 0,
            "orphan_entities": 0,
            "edges_total": 0,
            "edges_active": 0,
            "edges_stale": 0,
            "edges_superseded": 0,
            "edges_contradicted": 0,
        }
        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.execute(_sql.GRAPH_HEALTH_ENTITIES_SQL, (self._brain_id,))
            row = cur.fetchone()
            if row is not None:
                d = _row_to_dict(row, cur.description)
                out["entities_active"] = int(d.get("entities_active") or 0)
                out["orphan_entities"] = int(d.get("orphan_entities") or 0)
            cur.execute(_sql.GRAPH_HEALTH_EDGES_SQL, (self._brain_id,))
            row = cur.fetchone()
            if row is not None:
                d = _row_to_dict(row, cur.description)
                for key in (
                    "edges_total",
                    "edges_active",
                    "edges_stale",
                    "edges_superseded",
                    "edges_contradicted",
                ):
                    out[key] = int(d.get(key) or 0)
        return out

    # ------------------------------------------------------------------
    # Edge lifecycle mutations
    # ------------------------------------------------------------------

    def reinforce_edge(
        self,
        edge_id: str,
        was_useful: bool = True,
    ) -> bool:
        """Reinforce an edge using FSRS stability update.

        Returns ``True`` if updated, ``False`` if debounced (< 60 s since last
        reinforce) or edge not found.
        """
        with self._scoped_conn() as conn, conn.cursor() as cur:
            # Fetch current FSRS fields.
            cur.execute(_sql.GET_EDGE_FOR_REINFORCE_SQL, (edge_id,))
            row = cur.fetchone()
            if row is None:
                return False

            stability = float(row[1] or 0.0)
            difficulty = float(row[2] or 0.0)
            layer = row[3]
            last_reinforced = row[4]
            updated_at = row[5]

            new_s, new_d = self._compute_fsrs(
                stability=stability,
                difficulty=difficulty,
                layer=layer,
                last_reinforced=last_reinforced,
                updated_at=updated_at,
                was_useful=was_useful,
            )

            cur.execute(
                _sql.REINFORCE_EDGE_SQL,
                (new_s, new_d, edge_id, self._brain_id),
            )
            updated: bool = bool(cur.rowcount > 0)

        if updated:
            logger.debug("kg.edge.reinforced", edge_id=edge_id, was_useful=was_useful)
        else:
            logger.debug("kg.edge.reinforce_debounced", edge_id=edge_id)

        return updated

    def apply_edge_feedback(
        self,
        edge_id: str,
        feedback_type: str,
        confidence_delta: float = 0.05,
    ) -> dict[str, Any]:
        """Apply feedback counters and confidence adjustment for a KG edge.

        For ``edge_helpful``: increments ``useful_access_count`` and
        ``positive_feedback_count``, then calls :meth:`reinforce_edge`
        (subject to the existing 60-second debounce).

        For ``edge_misleading``: increments ``negative_feedback_count``,
        lowers ``confidence`` by *confidence_delta* (floored at
        ``confidence_floor``), and sets ``metadata.review_flagged = true``
        when ``negative_feedback_count > 3 * positive_feedback_count``.

        Returns a plain dict describing the outcome — callers should treat
        this as informational; the FeedbackStore audit trail is the
        authoritative record of the event.

        Parameters
        ----------
        edge_id:
            UUID of the edge to update.
        feedback_type:
            ``"edge_helpful"`` or ``"edge_misleading"``.
        confidence_delta:
            Amount by which to reduce confidence for ``edge_misleading``
            (default 0.05; clamped at ``confidence_floor``).
        """
        if feedback_type == "edge_helpful":
            with self._scoped_conn() as conn, conn.cursor() as cur:
                cur.execute(_sql.APPLY_EDGE_HELPFUL_SQL, (edge_id, self._brain_id))
                row = cur.fetchone()
            if row is None:
                logger.debug("kg.edge.feedback_not_found", edge_id=edge_id)
                return {"applied": False, "reason": "edge_not_found", "edge_id": edge_id}
            # Reinforce with FSRS update (60 s debounce enforced inside).
            # NOTE: reinforce_edge may also nudge confidence upward (x1.05);
            # the row[3] confidence captured above is pre-reinforce.  For
            # TAP-1975 we surface the post-feedback (pre-reinforce) confidence
            # because that's the value the UPDATE landed in this transaction.
            self.reinforce_edge(edge_id, was_useful=True)
            logger.debug("kg.edge.feedback_helpful", edge_id=edge_id)
            return {
                "applied": True,
                "feedback_type": feedback_type,
                "edge_id": edge_id,
                "positive_feedback_count": float(row[1]),
                "negative_feedback_count": float(row[2]),
                "confidence": float(row[3]),
            }

        if feedback_type == "edge_misleading":
            with self._scoped_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    _sql.APPLY_EDGE_MISLEADING_SQL,
                    (float(confidence_delta), edge_id, self._brain_id),
                )
                row = cur.fetchone()
            if row is None:
                logger.debug("kg.edge.feedback_not_found", edge_id=edge_id)
                return {"applied": False, "reason": "edge_not_found", "edge_id": edge_id}
            flagged = row[4] == "true" if row[4] else False
            logger.debug(
                "kg.edge.feedback_misleading",
                edge_id=edge_id,
                confidence=float(row[3]),
                flagged_for_review=flagged,
            )
            return {
                "applied": True,
                "feedback_type": feedback_type,
                "edge_id": edge_id,
                "positive_feedback_count": float(row[1]),
                "negative_feedback_count": float(row[2]),
                "confidence": float(row[3]),
                "flagged_for_review": flagged,
            }

        return {"applied": False, "reason": "unknown_feedback_type", "edge_id": edge_id}

    def mark_edge_stale(
        self,
        edge_id: str,
        reason: str | None = None,
    ) -> bool:
        """Mark an active edge stale; returns ``True`` if updated."""
        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.execute(
                _sql.MARK_EDGE_STALE_SQL,
                (reason, edge_id, self._brain_id),
            )
            updated: bool = bool(cur.rowcount > 0)

        if updated:
            logger.debug("kg.edge.marked_stale", edge_id=edge_id, reason=reason)
        return updated

    def supersede_edge(
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
        """Insert a replacement edge and mark *old_edge_id* superseded.

        Returns the new edge UUID string.
        """
        # Insert the new edge first.
        new_edge_id = self.upsert_edge(
            subject_entity_id=subject_entity_id,
            predicate=predicate,
            object_entity_id=object_entity_id,
            evidence_id=evidence_id,
            confidence=confidence,
            source_agent=source_agent,
            metadata=metadata,
        )

        # Record the supersession pointer.
        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.execute(
                _sql.SUPERSEDE_EDGE_SQL,
                (new_edge_id, old_edge_id, self._brain_id),
            )

        logger.debug(
            "kg.edge.superseded",
            old_edge_id=old_edge_id,
            new_edge_id=new_edge_id,
        )
        return new_edge_id

    def contradict_edge(
        self,
        edge_id: str,
        reason: str,
    ) -> bool:
        """Mark an edge contradicted + stale; returns ``True`` if updated."""
        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.execute(
                _sql.CONTRADICT_EDGE_SQL,
                (reason, edge_id, self._brain_id),
            )
            updated: bool = bool(cur.rowcount > 0)

        if updated:
            logger.debug("kg.edge.contradicted", edge_id=edge_id, reason=reason)
        return updated

    # ------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------

    def close(self) -> None:
        """No-op — the connection manager owns the pool lifecycle."""

    # ------------------------------------------------------------------
    # Internal: FSRS helper
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
        """Compute new (stability, difficulty) via the shared FSRS helper.

        Imports decay lazily to avoid a hard import cycle at module level.
        """
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

    # ------------------------------------------------------------------
    # Predicate registry (TAP-5508)
    # ------------------------------------------------------------------

    @staticmethod
    def _predicate_row_to_dict(row: tuple[Any, ...]) -> dict[str, Any]:
        """Shape one ``kg_predicates`` row for the wire.

        Column order matches ``UPSERT_PREDICATE_SQL`` / ``LIST_PREDICATES_SQL`` /
        ``GET_PREDICATE_SQL``, which deliberately select the same list so one
        mapper serves all three.
        """
        return {
            "id": str(row[0]),
            "predicate": row[1],
            "max_count": int(row[2]) if row[2] is not None else None,
            "domain_type": row[3],
            "range_type": row[4],
            "description": row[5],
            "registered_by": row[6],
            "created_at": row[7].isoformat() if row[7] is not None else None,
            "updated_at": row[8].isoformat() if row[8] is not None else None,
        }

    def register_predicate(
        self,
        *,
        predicate: str,
        max_count: int | None = None,
        domain_type: str | None = None,
        range_type: str | None = None,
        description: str | None = None,
        registered_by: str = "unknown",
    ) -> dict[str, Any]:
        """Declare what a predicate means for this project (TAP-5508).

        Re-registering an existing predicate updates it in place. The registry
        describes predicates; it does not own them — an unregistered predicate
        stays writable, and nothing here rejects an edge. TAP-5510 enforces
        ``max_count`` on the write path.

        Args:
            predicate: The predicate label, e.g. ``refunded``.
            max_count: Active objects one subject may hold for this predicate.
                ``None`` means unbounded; ``1`` declares a functional edge.
            domain_type: Optional ``kg_entities.entity_type`` for the subject.
            range_type: Optional ``kg_entities.entity_type`` for the object.
            description: Free text for humans reading the registry.
            registered_by: Who declared it.

        Returns:
            The stored declaration.
        """
        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.execute(
                _sql.UPSERT_PREDICATE_SQL,
                (
                    self._project_id,
                    self._brain_id,
                    self._project_id,
                    predicate,
                    max_count,
                    domain_type,
                    range_type,
                    description,
                    registered_by,
                ),
            )
            row = cur.fetchone()

        logger.info(
            "kg.predicate.registered",
            predicate=predicate,
            max_count=max_count,
            brain_id=self._brain_id,
        )
        return self._predicate_row_to_dict(row)

    def list_predicates(self) -> list[dict[str, Any]]:
        """Return every predicate declaration for this brain, ordered by name."""
        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.execute(_sql.LIST_PREDICATES_SQL, (self._project_id, self._brain_id))
            rows = cur.fetchall()
        return [self._predicate_row_to_dict(r) for r in rows]

    def get_predicate(self, predicate: str) -> dict[str, Any] | None:
        """Return one predicate declaration, or ``None`` when unregistered.

        ``None`` is the normal answer for a free-form predicate, not an error —
        the registry is open by default.
        """
        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.execute(
                _sql.GET_PREDICATE_SQL,
                (self._project_id, self._brain_id, predicate),
            )
            row = cur.fetchone()
        return self._predicate_row_to_dict(row) if row is not None else None

    # ------------------------------------------------------------------
    # Ledger check (TAP-5509)
    # ------------------------------------------------------------------

    def find_entities_by_name(self, canonical_name: str) -> list[dict[str, Any]]:
        """Return active entities matching *canonical_name* across all types.

        Returns every match rather than picking one: a bare name that resolves
        to two types is ambiguous input the caller has to disambiguate, not a
        coin the store should flip.
        """
        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.execute(_sql.FIND_ENTITIES_BY_NAME_SQL, (self._brain_id, canonical_name))
            rows = cur.fetchall()
        return [{"entity_id": str(r[0]), "entity_type": r[1], "canonical_name": r[2]} for r in rows]

    def count_active_objects(self, *, subject_entity_id: str, predicate: str) -> int:
        """Return how many DISTINCT objects *subject* holds for *predicate*.

        Distinct objects, not edge rows — cardinality bounds how many things a
        subject points at, and counting rows would let one object with a
        historical duplicate consume two slots.
        """
        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.execute(
                _sql.COUNT_ACTIVE_OBJECTS_SQL,
                (self._brain_id, subject_entity_id, predicate),
            )
            row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    def active_edge_exists(
        self, *, subject_entity_id: str, predicate: str, object_entity_id: str
    ) -> bool:
        """Return whether this exact triple is already active.

        Re-asserting an existing edge does not add an object, so it must not be
        denied by a cardinality check that is already at its limit.
        """
        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.execute(
                _sql.GET_ACTIVE_EDGE_SQL,
                (self._brain_id, subject_entity_id, predicate, object_entity_id),
            )
            return cur.fetchone() is not None
