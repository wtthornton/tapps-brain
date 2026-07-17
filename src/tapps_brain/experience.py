"""ExperienceEventRecorder — single-transaction atomic write API.

TAP-1501 STORY-076.4 — EPIC-076.

Writes one :class:`ExperienceEvent` to Postgres in a **single psycopg
transaction**: the ``experience_events`` row plus optional private memory,
KG entity upserts, KG edge upserts, and evidence inserts.  Any failure on
any side-effect rolls back the entire transaction — including the event row.

Usage::

    from tapps_brain.experience import (
        ExperienceEvent,
        ExperienceEventRecorder,
        EntitySpec,
        EdgeSpec,
        EvidenceSpec,
        MemorySpec,
    )
    from tapps_brain.postgres_connection import PostgresConnectionManager

    cm = PostgresConnectionManager(dsn)
    recorder = ExperienceEventRecorder(cm, project_id="my-proj", brain_id="tapps-brain")

    result = recorder.record(ExperienceEvent(
        event_type="workflow_completed",
        utility_score=0.9,
        payload={"workflow": "plan_and_implement"},
        memory=MemorySpec(key="workflow-result", value="Plan succeeded"),
        entities=[EntitySpec(entity_type="workflow", canonical_name="plan_and_implement")],
    ))
    print(result.event_id, result.entity_ids)

Async callers use :class:`AsyncExperienceEventRecorder`, which wraps the
sync recorder via ``asyncio.to_thread`` — consistent with
:class:`tapps_brain.aio.AsyncMemoryStore`.
"""

from __future__ import annotations

import json
import uuid as _uuid_mod
from typing import TYPE_CHECKING, Any

import structlog
from pydantic import BaseModel, Field, model_validator

from tapps_brain import _postgres_kg_sql as _kg_sql
from tapps_brain._postgres_private_sql import embedding_to_pgvector

if TYPE_CHECKING:
    from tapps_brain.embeddings import SentenceTransformerProvider
    from tapps_brain.postgres_connection import PostgresConnectionManager

# Sentinel used to distinguish "not passed" from "explicitly None" so we can
# auto-load the embedding provider from env when the caller omits the arg,
# matching the pattern in MemoryStore.__init__.
_UNSET_EMBEDDING: object = object()

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Input specs
# ---------------------------------------------------------------------------


class EntitySpec(BaseModel):
    """Spec for one KG entity to upsert atomically with the event."""

    @model_validator(mode="before")
    @classmethod
    def _accept_key_shorthand(cls, data: Any) -> Any:  # noqa: ANN401 — pydantic raw input
        """TAP-2675: accept the ``{'key': '<string>'}`` production payload shape.

        Real callers (AgentForge / bambustudio) post entities as ``{'key': ...}``
        without ``entity_type`` / ``canonical_name``, which raised a 2-error
        ValidationError and 500'd ``brain_record_events_batch`` (42 events, 14
        distinct calls, all rejected).  We coerce rather than reject so existing
        callers keep working without a client change: use ``key`` as
        ``canonical_name`` when it is absent, and default ``entity_type`` to
        ``"concept"``.
        """
        if isinstance(data, dict):
            patched = dict(data)
            if not patched.get("canonical_name"):
                if patched.get("id"):
                    patched["canonical_name"] = str(patched["id"])
                elif patched.get("key"):
                    patched["canonical_name"] = str(patched["key"])
            if not patched.get("entity_type") and patched.get("type"):
                patched["entity_type"] = str(patched["type"])
            if not patched.get("entity_type"):
                patched["entity_type"] = "concept"
            return patched
        return data

    entity_type: str = Field(description="Ontology type, e.g. 'module', 'service', 'concept'.")
    canonical_name: str = Field(description="Human-readable canonical name.")
    aliases: list[str] = Field(
        default_factory=list, description="Alternate names or surface forms."
    )
    metadata: dict[str, Any] = Field(default_factory=dict, description="Arbitrary metadata JSONB.")
    confidence: float = Field(default=0.6, ge=0.0, le=1.0, description="Initial confidence score.")
    source: str = Field(default="agent", description="Provenance source tag.")


def _ref_to_lookup(ref: dict[str, Any]) -> tuple[str, str]:
    """Extract ``(entity_type, canonical_name)`` from an entity ref object."""
    entity_type = str(ref.get("entity_type") or ref.get("type") or "").strip()
    canonical_name = str(ref.get("canonical_name") or ref.get("id") or ref.get("key") or "").strip()
    return entity_type, canonical_name


def _build_entity_lookup(
    entities: list[EntitySpec],
    entity_ids: list[str | None],
) -> tuple[dict[tuple[str, str], str], dict[str, str]]:
    """Map upserted entities to UUIDs for same-transaction edge key resolution.

    *entity_ids* is index-aligned with *entities*; ``None`` slots (failed
    upserts) are skipped so a later success is never paired with an earlier
    entity's UUID.
    """
    typed: dict[tuple[str, str], str] = {}
    by_name: dict[str, str] = {}
    for spec, entity_id in zip(entities, entity_ids, strict=True):
        if entity_id is None:
            continue
        typed[(spec.entity_type, spec.canonical_name)] = entity_id
        by_name[spec.canonical_name] = entity_id
    return typed, by_name


def _resolve_edge_endpoint(
    entity_id: str | None,
    key: str | None,
    ref: dict[str, Any] | None,
    typed: dict[tuple[str, str], str],
    by_name: dict[str, str],
) -> str | None:
    """Resolve one edge endpoint to a UUID using same-event entity upserts."""
    if entity_id:
        return entity_id
    if ref is not None:
        entity_type, canonical_name = _ref_to_lookup(ref)
        if entity_type and canonical_name:
            return typed.get((entity_type, canonical_name))
        if canonical_name:
            return by_name.get(canonical_name)
        return None
    if key:
        return by_name.get(key)
    return None


def _edge_resolution_warning(index: int, field: str, msg: str) -> dict[str, Any]:
    """Structured warning for an edge whose keys could not be resolved (TAP-3248)."""
    return {
        "kind": "edge",
        "index": index,
        "errors": [{"field": field, "msg": msg, "type": "unresolved"}],
    }


def _entity_upsert_warning(index: int, msg: str) -> dict[str, Any]:
    """Structured warning when a KG entity upsert fails without aborting the batch (TAP-4274)."""
    return {
        "kind": "entity",
        "index": index,
        "errors": [{"field": "canonical_name", "msg": msg, "type": "upsert_failed"}],
    }


class EdgeSpec(BaseModel):
    """Spec for one KG edge to upsert atomically with the event.

    Endpoints may be pre-resolved UUIDs (``subject_entity_id`` /
    ``object_entity_id``) or canonical keys (``subject_key`` / ``object_key``)
    referencing entities upserted in the same ``ExperienceEvent.entities`` list.
    Typed refs (``subject_ref`` / ``object_ref``) accept ``entity_type`` +
    ``canonical_name`` or ``type`` / ``id`` shorthand for disambiguation
    (TAP-3248 / EPIC-078).
    """

    subject_entity_id: str | None = Field(
        default=None, description="UUID of the subject entity (pre-resolved)."
    )
    object_entity_id: str | None = Field(
        default=None, description="UUID of the object entity (pre-resolved)."
    )
    subject_key: str | None = Field(
        default=None,
        description="Canonical name of the subject entity upserted in this event.",
    )
    object_key: str | None = Field(
        default=None,
        description="Canonical name of the object entity upserted in this event.",
    )
    subject_ref: dict[str, Any] | None = Field(
        default=None,
        description="Typed subject ref (entity_type/canonical_name or type/id).",
    )
    object_ref: dict[str, Any] | None = Field(
        default=None,
        description="Typed object ref (entity_type/canonical_name or type/id).",
    )
    predicate: str = Field(description="Edge predicate label.")
    edge_class: str | None = Field(default=None, description="Optional edge class tag.")
    layer: str | None = Field(default=None, description="Memory layer, e.g. 'pattern', 'context'.")
    profile_name: str | None = Field(
        default=None, description="Profile that defined this edge type."
    )
    confidence: float = Field(default=0.6, ge=0.0, le=1.0, description="Initial confidence score.")
    source: str = Field(default="agent", description="Provenance source tag.")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Arbitrary metadata JSONB.")

    @model_validator(mode="after")
    def _require_endpoints(self) -> EdgeSpec:
        """Require subject/object via UUID, key, or typed ref — not predicate alone."""
        has_subject = bool(self.subject_entity_id or self.subject_key or self.subject_ref)
        has_object = bool(self.object_entity_id or self.object_key or self.object_ref)
        if not has_subject:
            raise ValueError("edge requires subject_entity_id, subject_key, or subject_ref")
        if not has_object:
            raise ValueError("edge requires object_entity_id, object_key, or object_ref")
        if not self.predicate.strip():
            raise ValueError("predicate is required")
        return self


class EvidenceSpec(BaseModel):
    """Spec for one evidence row to attach atomically with the event.

    Exactly one of ``edge_id`` or ``entity_id`` must be non-None.
    """

    edge_id: str | None = Field(
        default=None, description="UUID of the edge this evidence supports."
    )
    entity_id: str | None = Field(
        default=None, description="UUID of the entity this evidence supports."
    )
    source_type: str = Field(default="agent", description="Evidence source category.")
    source_id: str | None = Field(default=None, description="Opaque source system identifier.")
    source_key: str | None = Field(
        default=None, description="Key within the source, e.g. a memory key."
    )
    source_uri: str | None = Field(default=None, description="URI to the source document.")
    source_hash: str | None = Field(default=None, description="Content hash for deduplication.")
    source_span: str | None = Field(default=None, description="Span or offset within the source.")
    quote: str | None = Field(default=None, description="Verbatim excerpt from the source.")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Arbitrary metadata JSONB.")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Evidence confidence.")
    utility_score: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Measured utility signal."
    )

    @model_validator(mode="after")
    def _require_exactly_one_attachment(self) -> EvidenceSpec:
        """TAP-2868: enforce the documented XOR — exactly one of ``edge_id`` /
        ``entity_id`` must be set.

        Mirrors the ``kg_evidence`` ``chk_evidence_xor_attachment`` CHECK.
        Without it, evidence with neither attachment (or both) passed Pydantic
        and raised ``CheckViolation`` at the DB insert, sinking the whole
        experience event with a masked 500 — the production shape AgentForge
        was posting.  Enforcing it at the model layer turns malformed evidence
        into a skipped-with-warning side-effect via ``record_event``'s
        resilient coercion (TAP-2866) instead of a 500.
        """
        if (self.edge_id is not None) == (self.entity_id is not None):
            raise ValueError("evidence requires exactly one of edge_id or entity_id")
        return self


class MemorySpec(BaseModel):
    """Spec for a private memory entry to write atomically with the event.

    Uses a minimal INSERT that covers only the essential fields; remaining
    private_memories columns use their database defaults.  For full-featured
    writes (decay metadata, FSRS scores, provenance fields) use
    :meth:`tapps_brain.store.MemoryStore.save` separately.
    """

    key: str = Field(description="Memory entry slug (max 128 chars).")
    value: str = Field(description="Memory content (max 4096 chars).")
    tier: str = Field(default="pattern", description="MemoryTier label.")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0, description="Memory confidence.")
    tags: list[str] = Field(default_factory=list, description="Classification tags (max 10).")
    agent_scope: str = Field(default="private", description="Hive propagation scope.")


class ExperienceEvent(BaseModel):
    """Input payload for one atomic experience event write.

    Supported event types (not exhaustive):

    * ``workflow_completed`` — a multi-step pipeline finished successfully.
    * ``tool_called`` — an external tool or API was invoked.
    * ``approach_failed`` — an attempted approach did not succeed.
    * ``memory_recalled`` — a recall query was executed and consumed.

    All fields in ``memory``, ``entities``, ``edges``, and ``evidence`` are
    written in the **same** Postgres transaction as the ``experience_events``
    row.  A constraint or RLS failure on any side-effect rolls back the entire
    transaction, including the event row itself.
    """

    event_type: str = Field(description="Semantic event category.")
    subject_key: str | None = Field(
        default=None, description="Primary memory key this event relates to."
    )
    utility_score: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Measured utility of this event (0-1)."
    )
    payload: dict[str, Any] = Field(
        default_factory=dict, description="Arbitrary event metadata JSONB."
    )
    session_id: str | None = Field(default=None, description="Session that produced this event.")
    workflow_run_id: str | None = Field(
        default=None, description="Workflow run identifier for grouping related events."
    )

    # Optional side effects — all written atomically
    memory: MemorySpec | None = Field(
        default=None, description="Private memory to persist in the same transaction."
    )
    entities: list[EntitySpec] = Field(
        default_factory=list, description="KG entities to upsert in the same transaction."
    )
    edges: list[EdgeSpec] = Field(
        default_factory=list, description="KG edges to upsert in the same transaction."
    )
    evidence: list[EvidenceSpec] = Field(
        default_factory=list, description="Evidence to attach in the same transaction."
    )


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


class ExperienceResult(BaseModel):
    """Result of a successful :meth:`ExperienceEventRecorder.record` call."""

    event_id: str = Field(description="UUID of the persisted experience_events row.")
    memory_key: str | None = Field(default=None, description="Key of the memory written, if any.")
    entity_ids: list[str] = Field(
        default_factory=list, description="UUIDs of upserted KG entities, in input order."
    )
    edge_ids: list[str] = Field(
        default_factory=list,
        description="UUIDs of upserted or reinforced KG edges, in input order.",
    )
    evidence_ids: list[str] = Field(
        default_factory=list, description="UUIDs of attached evidence rows, in input order."
    )
    warnings: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Non-fatal entity/edge warnings (TAP-3248, TAP-4274), merged at HTTP layer.",
    )


# ---------------------------------------------------------------------------
# Internal SQL
# ---------------------------------------------------------------------------

_INSERT_EVENT_SQL = """
INSERT INTO experience_events (
    id, tenant_id, brain_id, project_id, agent_id,
    session_id, workflow_run_id,
    event_type, subject_key, utility_score, payload
) VALUES (
    %s::uuid, %s, %s, %s, %s,
    %s, %s,
    %s, %s, %s, %s::jsonb
)
RETURNING id::text
"""

_UPDATE_EVENT_XREFS_SQL = """
UPDATE experience_events
SET created_memory_key = %s,
    created_entity_id  = %s::uuid,
    created_edge_id    = %s::uuid
WHERE id = %s::uuid
"""

# Minimal private_memories upsert — uses DB defaults for fields not in this
# INSERT (decay scores, FSRS fields, provenance, etc.).  Full-featured writes
# should go through MemoryStore.save().
#
# TAP-2705: embedding_model_id + embedding are now included so that
# experience-event memories participate in pgvector recall.  When no
# embedding provider is configured both columns receive NULL (fail-soft,
# matching MemoryStore.save).  ON CONFLICT preserves an existing embedding
# when the re-record lacks a provider (COALESCE), but refreshes it when one
# is available — so re-records never silently drop the vector.
_INSERT_MEMORY_SQL = """
INSERT INTO private_memories (
    project_id, agent_id, key, value,
    tier, confidence,
    source, source_agent,
    scope, agent_scope, tags,
    created_at, updated_at, last_accessed,
    embedding_model_id,
    embedding
) VALUES (
    %s, %s, %s, %s,
    %s, %s,
    'agent', %s,
    'project', %s, %s::jsonb,
    now(), now(), now(),
    %s,
    %s::vector
)
ON CONFLICT (project_id, agent_id, key) DO UPDATE SET
    value              = EXCLUDED.value,
    tier               = EXCLUDED.tier,
    confidence         = EXCLUDED.confidence,
    source_agent       = EXCLUDED.source_agent,
    agent_scope        = EXCLUDED.agent_scope,
    tags               = EXCLUDED.tags,
    updated_at         = now(),
    embedding_model_id = COALESCE(EXCLUDED.embedding_model_id, private_memories.embedding_model_id),
    embedding          = COALESCE(EXCLUDED.embedding, private_memories.embedding)
RETURNING key
"""


# ---------------------------------------------------------------------------
# Sync recorder
# ---------------------------------------------------------------------------


class ExperienceEventRecorder:
    """Writes one :class:`ExperienceEvent` to Postgres in a single transaction.

    All side-effects — optional private memory, KG entity upserts, KG edge
    upserts, evidence inserts — are committed or rolled back together with the
    ``experience_events`` row.  No LLM calls are made; all writes are
    deterministic.

    Parameters
    ----------
    cm:
        Open :class:`~tapps_brain.postgres_connection.PostgresConnectionManager`
        pointing at the tapps-brain Postgres instance.
    project_id:
        Tenant identity.  Sets ``app.project_id`` on the borrowed connection
        for RLS enforcement on ``experience_events``, ``kg_*``, and
        ``private_memories``.
    brain_id:
        Brain / instance identity (e.g. ``"tapps-brain"``).
    agent_id:
        Agent performing the write.  Defaults to ``"unknown"``.
    """

    def __init__(
        self,
        cm: PostgresConnectionManager,
        *,
        project_id: str,
        brain_id: str,
        agent_id: str = "unknown",
        embedding_provider: SentenceTransformerProvider | None = _UNSET_EMBEDDING,  # type: ignore[assignment]
    ) -> None:
        self._cm = cm
        self._project_id = project_id
        self._brain_id = brain_id
        self._agent_id = agent_id
        if embedding_provider is _UNSET_EMBEDDING:
            from tapps_brain.embeddings import get_embedding_provider

            self._embedding_provider: SentenceTransformerProvider | None = get_embedding_provider()
        else:
            self._embedding_provider = embedding_provider

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_embedding(self, text: str) -> tuple[str | None, str | None]:
        """Return ``(pgvector_str, model_id)`` for *text*, or ``(None, None)`` on
        failure or when no embedding provider is configured (fail-soft, matching
        :meth:`tapps_brain.store.MemoryStore._embed_entry`).
        """
        if self._embedding_provider is None:
            return None, None
        try:
            emb = self._embedding_provider.embed(text)
            mid_raw = getattr(self._embedding_provider, "model_id", None)
            mid: str | None = (
                mid_raw.strip() if isinstance(mid_raw, str) and mid_raw.strip() else None
            )
            return embedding_to_pgvector(emb), mid
        except Exception:
            logger.warning("experience_embedding_compute_failed", exc_info=True)
            return None, None

    def _ensure_memory_capacity(self, cur: Any, key: str) -> None:  # noqa: ANN401
        """Evict lowest-confidence row when a new key would exceed max_entries."""
        from tapps_brain.store import _max_entries_from_env

        max_entries = _max_entries_from_env()
        cur.execute(
            "SELECT 1 FROM private_memories WHERE project_id = %s AND agent_id = %s AND key = %s",
            (self._project_id, self._agent_id, key),
        )
        if cur.fetchone() is not None:
            return  # upsert path — does not grow cardinality
        cur.execute(
            "SELECT COUNT(*) FROM private_memories WHERE project_id = %s AND agent_id = %s",
            (self._project_id, self._agent_id),
        )
        row = cur.fetchone()
        count = int(row[0]) if row and row[0] is not None else 0
        if count < max_entries:
            return
        cur.execute(
            """
            DELETE FROM private_memories
            WHERE project_id = %s AND agent_id = %s AND ctid = (
                SELECT ctid FROM private_memories
                WHERE project_id = %s AND agent_id = %s
                ORDER BY confidence ASC, updated_at ASC NULLS FIRST
                LIMIT 1
            )
            """,
            (self._project_id, self._agent_id, self._project_id, self._agent_id),
        )
        logger.info(
            "experience_memory_evicted_for_cap",
            project_id=self._project_id,
            agent_id=self._agent_id,
            max_entries=max_entries,
            incoming_key=key,
        )

    def _upsert_edges(
        self,
        cur: Any,  # noqa: ANN401 — psycopg cursor
        edges: list[EdgeSpec],
        entities: list[EntitySpec],
        entity_ids: list[str | None],
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """Upsert edges after resolving key/ref endpoints against same-event entities."""
        edge_ids: list[str] = []
        warnings: list[dict[str, Any]] = []
        typed, by_name = _build_entity_lookup(entities, entity_ids)

        for index, edge_spec in enumerate(edges):
            subject_id = _resolve_edge_endpoint(
                edge_spec.subject_entity_id,
                edge_spec.subject_key,
                edge_spec.subject_ref,
                typed,
                by_name,
            )
            object_id = _resolve_edge_endpoint(
                edge_spec.object_entity_id,
                edge_spec.object_key,
                edge_spec.object_ref,
                typed,
                by_name,
            )
            if not subject_id:
                warnings.append(
                    _edge_resolution_warning(
                        index,
                        "subject_entity_id",
                        "could not resolve subject endpoint from keys in this event",
                    )
                )
                continue
            if not object_id:
                warnings.append(
                    _edge_resolution_warning(
                        index,
                        "object_entity_id",
                        "could not resolve object endpoint from keys in this event",
                    )
                )
                continue

            cur.execute(
                _kg_sql.GET_ACTIVE_EDGE_SQL,
                (
                    self._brain_id,
                    subject_id,
                    edge_spec.predicate,
                    object_id,
                ),
            )
            existing = cur.fetchone()
            if existing is not None:
                edge_ids.append(str(existing[0]))
            else:
                cur.execute(
                    _kg_sql.INSERT_EDGE_SQL,
                    (
                        self._project_id,
                        self._brain_id,
                        self._project_id,
                        subject_id,
                        edge_spec.predicate,
                        object_id,
                        edge_spec.edge_class,
                        edge_spec.layer,
                        edge_spec.profile_name,
                        edge_spec.confidence,
                        edge_spec.source,
                        self._agent_id,
                        self._agent_id,
                        json.dumps(edge_spec.metadata),
                    ),
                )
                row = cur.fetchone()
                if row:
                    edge_ids.append(str(row[0]))

        return edge_ids, warnings

    def _upsert_entities(
        self,
        cur: Any,  # noqa: ANN401 — psycopg cursor
        entities: list[EntitySpec],
    ) -> tuple[list[str | None], list[dict[str, Any]]]:
        """Upsert KG entities; isolate failures via savepoints (TAP-4274).

        Returns an index-aligned list (``None`` for failed upserts) so edge
        key resolution never pairs a later entity with an earlier UUID.
        """
        entity_ids: list[str | None] = [None] * len(entities)
        warnings: list[dict[str, Any]] = []

        for index, entity_spec in enumerate(entities):
            savepoint = f"upsert_entity_{index}"
            cur.execute(f"SAVEPOINT {savepoint}")
            try:
                cur.execute(
                    _kg_sql.UPSERT_ENTITY_SQL,
                    (
                        self._project_id,
                        self._brain_id,
                        self._project_id,
                        entity_spec.entity_type,
                        entity_spec.canonical_name,
                        json.dumps(entity_spec.aliases),
                        json.dumps(entity_spec.metadata),
                        entity_spec.confidence,
                        entity_spec.source,
                        self._agent_id,
                    ),
                )
                row = cur.fetchone()
                if row:
                    entity_ids[index] = str(row[0])
                cur.execute(f"RELEASE SAVEPOINT {savepoint}")
            except Exception as exc:
                cur.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                warnings.append(_entity_upsert_warning(index, str(exc)))

        return entity_ids, warnings

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(self, event: ExperienceEvent) -> ExperienceResult:
        """Write *event* and all its side-effects in one Postgres transaction.

        Returns :class:`ExperienceResult` with the IDs of every persisted
        object.  Raises ``psycopg.DatabaseError`` on any constraint or RLS
        violation; the transaction is rolled back before the exception
        propagates — including the ``experience_events`` row itself.

        Parameters
        ----------
        event:
            The event to record.  The ``entities`` list is upserted first;
            ``edges`` are upserted second (using pre-resolved entity UUIDs);
            ``evidence`` rows are attached last.

        Raises
        ------
        psycopg.DatabaseError
            Propagated on any Postgres constraint, RLS, or type error.
            The full transaction is rolled back before this is raised.
        """
        event_id = str(_uuid_mod.uuid4())
        entity_ids: list[str | None] = []
        edge_ids: list[str] = []
        evidence_ids: list[str] = []
        memory_key: str | None = None
        edge_warnings: list[dict[str, Any]] = []
        entity_warnings: list[dict[str, Any]] = []
        resolved_entity_ids: list[str] = []

        log = logger.bind(event_type=event.event_type, event_id=event_id)

        # TAP-2705: compute embedding outside the DB transaction to avoid
        # holding the connection open during CPU-bound inference.
        emb_pgv: str | None = None
        emb_model_id: str | None = None
        if event.memory is not None:
            emb_pgv, emb_model_id = self._compute_embedding(event.memory.value)

        with self._cm.project_context(self._project_id) as conn, conn.cursor() as cur:
            # Step 1 — insert the event row (cross-ref columns updated later).
            cur.execute(
                _INSERT_EVENT_SQL,
                (
                    event_id,
                    self._project_id,
                    self._brain_id,
                    self._project_id,
                    self._agent_id,
                    event.session_id,
                    event.workflow_run_id,
                    event.event_type,
                    event.subject_key,
                    event.utility_score,
                    json.dumps(event.payload),
                ),
            )

            # Step 2 — optional private memory write.
            if event.memory is not None:
                mem = event.memory
                self._ensure_memory_capacity(cur, mem.key)
                cur.execute(
                    _INSERT_MEMORY_SQL,
                    (
                        self._project_id,
                        self._agent_id,
                        mem.key,
                        mem.value,
                        mem.tier,
                        mem.confidence,
                        self._agent_id,
                        mem.agent_scope,
                        json.dumps(mem.tags),
                        emb_model_id,
                        emb_pgv,
                    ),
                )
                row = cur.fetchone()
                memory_key = str(row[0]) if row else mem.key

            # Step 3 — upsert KG entities.
            entity_ids, entity_warnings = self._upsert_entities(cur, event.entities)

            # Step 4 — upsert KG edges (resolve key/ref endpoints from step 3).
            edge_ids, edge_warnings = self._upsert_edges(
                cur, event.edges, event.entities, entity_ids
            )

            # Step 5 — attach evidence rows.
            for ev_spec in event.evidence:
                cur.execute(
                    _kg_sql.ATTACH_EVIDENCE_SQL,
                    (
                        self._project_id,  # tenant_id
                        self._brain_id,
                        self._project_id,
                        ev_spec.edge_id,
                        ev_spec.entity_id,
                        ev_spec.source_type,
                        ev_spec.source_id,
                        ev_spec.source_key,
                        ev_spec.source_uri,
                        ev_spec.source_hash,
                        ev_spec.source_span,
                        ev_spec.quote,
                        json.dumps(ev_spec.metadata),
                        self._agent_id,
                        ev_spec.confidence,
                        ev_spec.utility_score,
                    ),
                )
                row = cur.fetchone()
                if row:
                    evidence_ids.append(str(row[0]))

            # Step 6 — patch event cross-reference columns.
            resolved_entity_ids = [eid for eid in entity_ids if eid is not None]
            first_entity_id = resolved_entity_ids[0] if resolved_entity_ids else None
            first_edge_id = edge_ids[0] if edge_ids else None
            if memory_key or first_entity_id or first_edge_id:
                cur.execute(
                    _UPDATE_EVENT_XREFS_SQL,
                    (memory_key, first_entity_id, first_edge_id, event_id),
                )

        log.info(
            "experience_event_recorded",
            memory_key=memory_key,
            entity_count=len(resolved_entity_ids),
            edge_count=len(edge_ids),
            evidence_count=len(evidence_ids),
        )

        return ExperienceResult(
            event_id=event_id,
            memory_key=memory_key,
            entity_ids=resolved_entity_ids,
            edge_ids=edge_ids,
            evidence_ids=evidence_ids,
            warnings=edge_warnings + entity_warnings,
        )

    def record_many(self, events: list[ExperienceEvent]) -> list[ExperienceResult]:
        """Write *events* atomically in a single Postgres transaction (TAP-1934).

        Patterned after :meth:`record` but the entire list shares one
        ``project_context`` cursor block — any failure on any event rolls back
        the whole batch.  Returns one :class:`ExperienceResult` per event in
        input order.

        This is the all-or-nothing primitive used by the
        ``POST /v1/experience:batch`` REST endpoint for high-throughput
        consumers (Ralph-style autonomous loops emitting 20+ events/min).

        Parameters
        ----------
        events:
            List of events to record.  Each event runs the same side-effect
            pipeline as :meth:`record` (entities → edges → evidence →
            cross-ref patch), interleaved within the shared transaction.

        Raises
        ------
        psycopg.DatabaseError
            Propagated on any constraint or RLS violation.  The full batch
            transaction is rolled back before this is raised.
        """
        if not events:
            return []

        # TAP-2705: precompute embeddings for all events before opening the
        # transaction so we don't hold the DB connection open during CPU-bound
        # inference.  Index aligns with the events list.
        event_embeddings: list[tuple[str | None, str | None]] = [
            self._compute_embedding(e.memory.value) if e.memory is not None else (None, None)
            for e in events
        ]

        results: list[ExperienceResult] = []

        with self._cm.project_context(self._project_id) as conn, conn.cursor() as cur:
            for event, (emb_pgv, emb_model_id) in zip(events, event_embeddings, strict=True):
                event_id = str(_uuid_mod.uuid4())
                entity_ids: list[str | None] = []
                edge_ids: list[str] = []
                evidence_ids: list[str] = []
                memory_key: str | None = None
                edge_warnings: list[dict[str, Any]] = []
                entity_warnings: list[dict[str, Any]] = []

                # Insert the event row.
                cur.execute(
                    _INSERT_EVENT_SQL,
                    (
                        event_id,
                        self._project_id,
                        self._brain_id,
                        self._project_id,
                        self._agent_id,
                        event.session_id,
                        event.workflow_run_id,
                        event.event_type,
                        event.subject_key,
                        event.utility_score,
                        json.dumps(event.payload),
                    ),
                )

                # Optional memory.
                if event.memory is not None:
                    mem = event.memory
                    self._ensure_memory_capacity(cur, mem.key)
                    cur.execute(
                        _INSERT_MEMORY_SQL,
                        (
                            self._project_id,
                            self._agent_id,
                            mem.key,
                            mem.value,
                            mem.tier,
                            mem.confidence,
                            self._agent_id,
                            mem.agent_scope,
                            json.dumps(mem.tags),
                            emb_model_id,
                            emb_pgv,
                        ),
                    )
                    row = cur.fetchone()
                    memory_key = str(row[0]) if row else mem.key

                # Entities → edges → evidence (identical to record()).
                entity_ids, entity_warnings = self._upsert_entities(cur, event.entities)

                edge_ids, edge_warnings = self._upsert_edges(
                    cur, event.edges, event.entities, entity_ids
                )

                for ev_spec in event.evidence:
                    cur.execute(
                        _kg_sql.ATTACH_EVIDENCE_SQL,
                        (
                            self._project_id,
                            self._brain_id,
                            self._project_id,
                            ev_spec.edge_id,
                            ev_spec.entity_id,
                            ev_spec.source_type,
                            ev_spec.source_id,
                            ev_spec.source_key,
                            ev_spec.source_uri,
                            ev_spec.source_hash,
                            ev_spec.source_span,
                            ev_spec.quote,
                            json.dumps(ev_spec.metadata),
                            self._agent_id,
                            ev_spec.confidence,
                            ev_spec.utility_score,
                        ),
                    )
                    row = cur.fetchone()
                    if row:
                        evidence_ids.append(str(row[0]))

                resolved_entity_ids = [eid for eid in entity_ids if eid is not None]
                first_entity_id = resolved_entity_ids[0] if resolved_entity_ids else None
                first_edge_id = edge_ids[0] if edge_ids else None
                if memory_key or first_entity_id or first_edge_id:
                    cur.execute(
                        _UPDATE_EVENT_XREFS_SQL,
                        (memory_key, first_entity_id, first_edge_id, event_id),
                    )

                results.append(
                    ExperienceResult(
                        event_id=event_id,
                        memory_key=memory_key,
                        entity_ids=resolved_entity_ids,
                        edge_ids=edge_ids,
                        evidence_ids=evidence_ids,
                        warnings=edge_warnings + entity_warnings,
                    )
                )

        logger.info(
            "experience_events_batch_recorded",
            count=len(results),
            agent_id=self._agent_id,
        )

        return results


# ---------------------------------------------------------------------------
# Async wrapper
# ---------------------------------------------------------------------------


class AsyncExperienceEventRecorder:
    """Async parity for :class:`ExperienceEventRecorder`.

    Wraps the sync recorder via ``asyncio.to_thread`` so callers in an
    asyncio event loop can ``await recorder.record(event)`` without blocking
    the loop.  Consistent with :class:`tapps_brain.aio.AsyncMemoryStore`.

    Parameters
    ----------
    recorder:
        An already-constructed :class:`ExperienceEventRecorder` instance.
    """

    def __init__(self, recorder: ExperienceEventRecorder) -> None:
        self._recorder = recorder

    async def record(self, event: ExperienceEvent) -> ExperienceResult:
        """Async variant of :meth:`ExperienceEventRecorder.record`."""
        import asyncio

        return await asyncio.to_thread(self._recorder.record, event)
