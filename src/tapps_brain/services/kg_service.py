"""Knowledge-Graph service functions for MCP tools and HTTP endpoints.

TAP-1502 STORY-076.5 — EPIC-076.

Service functions are pure (no FastAPI / MCP imports) and accept an explicit
``PostgresConnectionManager`` so they can be called from both the MCP server
and the HTTP adapter without coupling either to the other's infrastructure.

Thread-safety: all functions create ``PostgresKnowledgeGraphStore`` per-call.
The underlying ``PostgresConnectionManager`` is safe to share across threads.
"""

from __future__ import annotations

import json
import os
import threading
from collections import deque
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from tapps_brain.postgres_connection import PostgresConnectionManager

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Brain-ID resolution (process-level constant)
# ---------------------------------------------------------------------------

_DEFAULT_BRAIN_ID = os.environ.get("TAPPS_BRAIN_BRAIN_ID", "tapps-brain")

# ---------------------------------------------------------------------------
# Lazy process-level connection manager (for MCP server context)
# ---------------------------------------------------------------------------

_CM_LOCK = threading.Lock()
_CM: Any = None  # PostgresConnectionManager | None


def _get_or_create_cm() -> Any | None:  # noqa: ANN401
    """Return (creating if absent) a process-level ``PostgresConnectionManager``.

    Reads ``TAPPS_BRAIN_DATABASE_URL`` (falling back to
    ``TAPPS_BRAIN_HIVE_DSN``).  Returns ``None`` when no DSN is set — callers
    should return a 503 / capability-unavailable error in that case.
    """
    global _CM  # noqa: PLW0603
    if _CM is not None:
        return _CM
    dsn = (
        os.environ.get("TAPPS_BRAIN_DATABASE_URL")
        or os.environ.get("TAPPS_BRAIN_HIVE_DSN")
        or ""
    ).strip()
    if not dsn:
        return None
    with _CM_LOCK:
        if _CM is None:
            from tapps_brain.postgres_connection import PostgresConnectionManager

            _CM = PostgresConnectionManager(dsn)
    return _CM


# ---------------------------------------------------------------------------
# Helper — build a per-call PostgresKnowledgeGraphStore
# ---------------------------------------------------------------------------


def _kg_store(cm: Any, project_id: str, brain_id: str) -> Any:  # noqa: ANN401
    from tapps_brain.postgres_kg import PostgresKnowledgeGraphStore

    return PostgresKnowledgeGraphStore(
        cm,
        project_id=project_id,
        brain_id=brain_id,
        evidence_required=False,  # recorder path; callers attach evidence explicitly
    )


# ---------------------------------------------------------------------------
# record_event
# ---------------------------------------------------------------------------


def record_event(
    cm: Any,  # noqa: ANN401
    project_id: str,
    brain_id: str,
    agent_id: str,
    *,
    event_type: str,
    subject_key: str | None = None,
    utility_score: float = 0.0,
    payload: dict[str, Any] | None = None,
    entities_json: str = "",
    edges_json: str = "",
    evidence_json: str = "",
    memory_key: str | None = None,
    memory_value: str | None = None,
    memory_tier: str = "pattern",
    session_id: str | None = None,
    workflow_run_id: str | None = None,
) -> dict[str, Any]:
    """Write an ExperienceEvent and optional side-effects atomically.

    All writes happen in a single psycopg transaction via
    :class:`~tapps_brain.experience.ExperienceEventRecorder`.

    Parameters
    ----------
    cm:
        Open :class:`~tapps_brain.postgres_connection.PostgresConnectionManager`.
    project_id, brain_id, agent_id:
        Tenant / identity scope.
    event_type:
        Semantic event category (e.g. ``workflow_completed``).
    subject_key:
        Optional primary memory key this event relates to.
    utility_score:
        Measured utility `[0, 1]`.
    payload:
        Arbitrary JSONB event metadata.
    entities_json, edges_json, evidence_json:
        JSON-serialised lists of spec dicts matching
        :class:`~tapps_brain.experience.EntitySpec`,
        :class:`~tapps_brain.experience.EdgeSpec`, and
        :class:`~tapps_brain.experience.EvidenceSpec` respectively.
        Pass ``""`` or ``"[]"`` to skip that component.
    memory_key / memory_value:
        When both are provided, a :class:`~tapps_brain.experience.MemorySpec`
        is written atomically alongside the event.
    """
    from tapps_brain.experience import (
        EdgeSpec,
        EntitySpec,
        EvidenceSpec,
        ExperienceEvent,
        ExperienceEventRecorder,
        MemorySpec,
    )

    # Parse optional JSON arrays.
    def _parse_specs(raw: str, cls: type) -> list[Any]:
        if not raw or raw.strip() in ("", "[]"):
            return []
        try:
            items = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return [cls(**item) for item in (items if isinstance(items, list) else [])]

    entity_specs = _parse_specs(entities_json, EntitySpec)
    edge_specs = _parse_specs(edges_json, EdgeSpec)
    evidence_specs = _parse_specs(evidence_json, EvidenceSpec)

    mem_spec: MemorySpec | None = None
    if memory_key and memory_value:
        mem_spec = MemorySpec(key=memory_key, value=memory_value, tier=memory_tier)

    event = ExperienceEvent(
        event_type=event_type,
        subject_key=subject_key,
        utility_score=max(0.0, min(1.0, utility_score)),
        payload=payload or {},
        session_id=session_id,
        workflow_run_id=workflow_run_id,
        memory=mem_spec,
        entities=entity_specs,
        edges=edge_specs,
        evidence=evidence_specs,
    )

    recorder = ExperienceEventRecorder(
        cm, project_id=project_id, brain_id=brain_id, agent_id=agent_id
    )
    result = recorder.record(event)
    return result.model_dump()


# ---------------------------------------------------------------------------
# get_neighbors
# ---------------------------------------------------------------------------


def get_neighbors(
    cm: Any,  # noqa: ANN401
    project_id: str,
    brain_id: str,
    *,
    entity_ids: list[str],
    hops: int = 1,
    limit: int = 20,
    predicate_filter: str | None = None,
) -> dict[str, Any]:
    """Return neighbourhood graph rows for *entity_ids*.

    Delegates to
    :meth:`~tapps_brain.postgres_kg.PostgresKnowledgeGraphStore.get_neighbors_multi`.

    Parameters
    ----------
    entity_ids:
        List of entity UUID strings.
    hops:
        Hop depth (1 or 2).  Values > 2 are capped to 2.
    limit:
        Maximum number of edge rows to return.
    predicate_filter:
        Optional predicate substring filter.
    """
    if not entity_ids:
        return {"neighbors": [], "entity_ids": []}

    kg = _kg_store(cm, project_id, brain_id)
    try:
        rows = kg.get_neighbors_multi(
            entity_ids,
            hops=min(hops, 2),
            limit=max(1, min(limit, 200)),
            predicate_filter=predicate_filter,
        )
        # Ensure UUIDs are returned as strings (psycopg may return uuid objects).
        serialisable = [
            {k: str(v) if hasattr(v, "hex") else v for k, v in row.items()} for row in rows
        ]
        return {"neighbors": serialisable, "entity_ids": entity_ids}
    finally:
        kg.close()


# ---------------------------------------------------------------------------
# explain_connection
# ---------------------------------------------------------------------------


def explain_connection(
    cm: Any,  # noqa: ANN401
    project_id: str,
    brain_id: str,
    *,
    subject_id: str,
    object_id: str,
    max_hops: int = 3,
) -> dict[str, Any]:
    """Find the shortest path between *subject_id* and *object_id*.

    Uses BFS over
    :meth:`~tapps_brain.postgres_kg.PostgresKnowledgeGraphStore.get_neighbors`
    up to *max_hops* depth (capped at 3).  Returns the first path found or
    ``found=False`` when no path exists within the hop limit.

    Parameters
    ----------
    subject_id:
        UUID of the starting entity.
    object_id:
        UUID of the target entity.
    max_hops:
        Maximum hops to traverse (clamped to [1, 3]).
    """
    max_hops = max(1, min(max_hops, 3))

    if subject_id == object_id:
        return {"found": True, "hops": 0, "path": [{"entity_id": subject_id}]}

    kg = _kg_store(cm, project_id, brain_id)
    try:
        # BFS: queue entries are (current_entity_id, path_so_far).
        # path_so_far is a list of dicts describing the walk from subject.
        queue: deque[tuple[str, list[dict[str, Any]]]] = deque()
        queue.append((subject_id, [{"entity_id": subject_id}]))
        visited: set[str] = {subject_id}

        for _ in range(max_hops):
            next_queue: deque[tuple[str, list[dict[str, Any]]]] = deque()
            while queue:
                current_id, path = queue.popleft()
                try:
                    neighbors = kg.get_neighbors(current_id, direction="both", limit=50)
                except Exception:
                    continue
                for n in neighbors:
                    neighbor_id = str(n.get("neighbor_id", ""))
                    if not neighbor_id or neighbor_id in visited:
                        continue
                    visited.add(neighbor_id)
                    step: dict[str, Any] = {
                        "edge_id": str(n.get("edge_id", "")),
                        "predicate": str(n.get("predicate", "")),
                        "direction": str(n.get("direction", "out")),
                        "entity_id": neighbor_id,
                        "entity_type": str(n.get("entity_type", "")),
                        "canonical_name": str(n.get("canonical_name", "")),
                    }
                    new_path = [*path, step]
                    if neighbor_id == object_id:
                        return {
                            "found": True,
                            "hops": len(new_path) - 1,
                            "path": new_path,
                            "subject_id": subject_id,
                            "object_id": object_id,
                        }
                    next_queue.append((neighbor_id, new_path))
            queue = next_queue
            if not queue:
                break
    finally:
        kg.close()

    return {"found": False, "hops": None, "path": [], "subject_id": subject_id, "object_id": object_id}


# ---------------------------------------------------------------------------
# record_kg_feedback
# ---------------------------------------------------------------------------


def record_kg_feedback(
    store: Any,  # noqa: ANN401
    project_id: str,
    agent_id: str,
    *,
    edge_id: str,
    feedback_type: str,
    session_id: str = "",
) -> dict[str, Any]:
    """Record edge-level feedback (``edge_helpful`` or ``edge_misleading``).

    Delegates to
    :func:`~tapps_brain.services.feedback_service.feedback_record` so the
    event lands in the ``feedback_events`` table with the edge UUID as the
    ``entry_key``.

    Parameters
    ----------
    store:
        MemoryStore (or StoreProxy) for the calling agent.
    project_id:
        Tenant identity.
    agent_id:
        Agent performing the feedback write.
    edge_id:
        UUID of the KG edge being rated.
    feedback_type:
        ``"edge_helpful"`` or ``"edge_misleading"``.
    session_id:
        Optional session identifier.
    """
    from tapps_brain.services import feedback_service

    allowed = {"edge_helpful", "edge_misleading"}
    if feedback_type not in allowed:
        return {
            "error": "bad_request",
            "detail": f"feedback_type must be one of {sorted(allowed)!r}.",
        }

    return feedback_service.feedback_record(
        store,
        project_id,
        agent_id,
        event_type=feedback_type,
        entry_key=edge_id,
        session_id=session_id,
        details_json=json.dumps({"edge_id": edge_id}),
    )
