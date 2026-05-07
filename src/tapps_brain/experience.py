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
from pydantic import BaseModel, Field

from tapps_brain import _postgres_kg_sql as _kg_sql

if TYPE_CHECKING:
    from tapps_brain.postgres_connection import PostgresConnectionManager

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Input specs
# ---------------------------------------------------------------------------


class EntitySpec(BaseModel):
    """Spec for one KG entity to upsert atomically with the event."""

    entity_type: str = Field(description="Ontology type, e.g. 'module', 'service', 'concept'.")
    canonical_name: str = Field(description="Human-readable canonical name.")
    aliases: list[str] = Field(
        default_factory=list, description="Alternate names or surface forms."
    )
    metadata: dict[str, Any] = Field(default_factory=dict, description="Arbitrary metadata JSONB.")
    confidence: float = Field(default=0.6, ge=0.0, le=1.0, description="Initial confidence score.")
    source: str = Field(default="agent", description="Provenance source tag.")


class EdgeSpec(BaseModel):
    """Spec for one KG edge to upsert atomically with the event.

    Both ``subject_entity_id`` and ``object_entity_id`` must be pre-resolved
    entity UUIDs (string form).  To create entities and reference them in one
    call, include them in ``ExperienceEvent.entities`` and capture the returned
    ``ExperienceResult.entity_ids`` for subsequent events — or resolve them
    via :meth:`~tapps_brain.postgres_kg.PostgresKnowledgeGraphStore.batch_resolve_entities`
    before constructing the event.
    """

    subject_entity_id: str = Field(description="UUID of the subject entity (pre-resolved).")
    predicate: str = Field(description="Edge predicate label.")
    object_entity_id: str = Field(description="UUID of the object entity (pre-resolved).")
    edge_class: str | None = Field(default=None, description="Optional edge class tag.")
    layer: str | None = Field(
        default=None, description="Memory layer, e.g. 'pattern', 'context'."
    )
    profile_name: str | None = Field(
        default=None, description="Profile that defined this edge type."
    )
    confidence: float = Field(default=0.6, ge=0.0, le=1.0, description="Initial confidence score.")
    source: str = Field(default="agent", description="Provenance source tag.")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Arbitrary metadata JSONB.")


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
_INSERT_MEMORY_SQL = """
INSERT INTO private_memories (
    project_id, agent_id, key, value,
    tier, confidence,
    source, source_agent,
    scope, agent_scope, tags,
    created_at, updated_at, last_accessed
) VALUES (
    %s, %s, %s, %s,
    %s, %s,
    'agent', %s,
    'project', %s, %s::jsonb,
    now(), now(), now()
)
ON CONFLICT (project_id, agent_id, key) DO UPDATE SET
    value      = EXCLUDED.value,
    tier       = EXCLUDED.tier,
    confidence = EXCLUDED.confidence,
    updated_at = now()
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
    ) -> None:
        self._cm = cm
        self._project_id = project_id
        self._brain_id = brain_id
        self._agent_id = agent_id

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
        entity_ids: list[str] = []
        edge_ids: list[str] = []
        evidence_ids: list[str] = []
        memory_key: str | None = None

        log = logger.bind(event_type=event.event_type, event_id=event_id)

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
                    ),
                )
                row = cur.fetchone()
                memory_key = str(row[0]) if row else mem.key

            # Step 3 — upsert KG entities.
            for entity_spec in event.entities:
                cur.execute(
                    _kg_sql.UPSERT_ENTITY_SQL,
                    (
                        self._project_id,   # tenant_id
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
                    entity_ids.append(str(row[0]))

            # Step 4 — upsert KG edges.
            for edge_spec in event.edges:
                # Reuse existing active edge rather than duplicating it.
                cur.execute(
                    _kg_sql.GET_ACTIVE_EDGE_SQL,
                    (
                        self._brain_id,
                        edge_spec.subject_entity_id,
                        edge_spec.predicate,
                        edge_spec.object_entity_id,
                    ),
                )
                existing = cur.fetchone()
                if existing is not None:
                    edge_ids.append(str(existing[0]))
                else:
                    cur.execute(
                        _kg_sql.INSERT_EDGE_SQL,
                        (
                            self._project_id,   # tenant_id
                            self._brain_id,
                            self._project_id,
                            edge_spec.subject_entity_id,
                            edge_spec.predicate,
                            edge_spec.object_entity_id,
                            edge_spec.edge_class,
                            edge_spec.layer,
                            edge_spec.profile_name,
                            edge_spec.confidence,
                            edge_spec.source,
                            self._agent_id,
                            self._agent_id,     # created_by_agent
                            json.dumps(edge_spec.metadata),
                        ),
                    )
                    row = cur.fetchone()
                    if row:
                        edge_ids.append(str(row[0]))

            # Step 5 — attach evidence rows.
            for ev_spec in event.evidence:
                cur.execute(
                    _kg_sql.ATTACH_EVIDENCE_SQL,
                    (
                        self._project_id,   # tenant_id
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
            first_entity_id = entity_ids[0] if entity_ids else None
            first_edge_id = edge_ids[0] if edge_ids else None
            if memory_key or first_entity_id or first_edge_id:
                cur.execute(
                    _UPDATE_EVENT_XREFS_SQL,
                    (memory_key, first_entity_id, first_edge_id, event_id),
                )

        log.info(
            "experience_event_recorded",
            memory_key=memory_key,
            entity_count=len(entity_ids),
            edge_count=len(edge_ids),
            evidence_count=len(evidence_ids),
        )

        return ExperienceResult(
            event_id=event_id,
            memory_key=memory_key,
            entity_ids=entity_ids,
            edge_ids=edge_ids,
            evidence_ids=evidence_ids,
        )


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
