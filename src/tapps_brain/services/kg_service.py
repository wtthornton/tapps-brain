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
from typing import Any

import structlog

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


def _get_or_create_cm() -> Any | None:
    """Return (creating if absent) a process-level ``PostgresConnectionManager``.

    Reads ``TAPPS_BRAIN_DATABASE_URL`` (falling back to
    ``TAPPS_BRAIN_HIVE_DSN``).  Returns ``None`` when no DSN is set — callers
    should return a 503 / capability-unavailable error in that case.
    """
    global _CM
    if _CM is not None:
        return _CM
    dsn = (
        os.environ.get("TAPPS_BRAIN_DATABASE_URL") or os.environ.get("TAPPS_BRAIN_HIVE_DSN") or ""
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


def _kg_store(cm: Any, project_id: str, brain_id: str) -> Any:
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
    cm: Any,
    project_id: str,
    brain_id: str,
    agent_id: str,
    *,
    event_type: str,
    subject_key: str | None = None,
    utility_score: float = 0.0,
    payload: dict[str, Any] | None = None,
    entities: list[dict[str, Any]] | None = None,
    edges: list[dict[str, Any]] | None = None,
    evidence: list[dict[str, Any]] | None = None,
    memory_key: str | None = None,
    memory_value: str | None = None,
    memory_tier: str = "pattern",
    session_id: str | None = None,
    workflow_run_id: str | None = None,
    # Deprecated JSON-string aliases (TAP-1932). Removed in next minor.
    entities_json: str = "",
    edges_json: str = "",
    evidence_json: str = "",
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
    entities, edges, evidence:
        Native ``list[dict]`` shapes matching
        :class:`~tapps_brain.experience.EntitySpec`,
        :class:`~tapps_brain.experience.EdgeSpec`, and
        :class:`~tapps_brain.experience.EvidenceSpec` respectively
        (TAP-1932).
    entities_json, edges_json, evidence_json:
        **Deprecated (TAP-1932).** JSON-serialised list aliases retained for
        one minor cycle. The native list arguments take precedence.
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

    def _coerce(
        native: list[dict[str, Any]] | None,
        legacy_json: str,
        cls: type,
    ) -> list[Any]:
        """Build spec instances from native list (preferred) or legacy JSON string."""
        items: list[dict[str, Any]] = []
        if isinstance(native, list):
            items = [it for it in native if isinstance(it, dict)]
        elif legacy_json and legacy_json.strip() not in ("", "[]"):
            try:
                parsed = json.loads(legacy_json)
            except json.JSONDecodeError:
                parsed = []
            if isinstance(parsed, list):
                items = [it for it in parsed if isinstance(it, dict)]
        return [cls(**item) for item in items]

    entity_specs = _coerce(entities, entities_json, EntitySpec)
    edge_specs = _coerce(edges, edges_json, EdgeSpec)
    evidence_specs = _coerce(evidence, evidence_json, EvidenceSpec)

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
# record_events_batch (TAP-1934)
# ---------------------------------------------------------------------------


def record_events_batch(
    cm: Any,
    project_id: str,
    brain_id: str,
    agent_id: str,
    *,
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Write multiple :class:`ExperienceEvent` rows atomically.

    Patterned after :func:`record_event` but writes the entire batch in a
    single Postgres transaction via a per-batch
    :class:`~tapps_brain.experience.ExperienceEventRecorder` invoked with one
    ``cm.project_context`` block.  Any failure on any event rolls back the
    whole batch — partial commits are not possible.

    Parameters
    ----------
    cm:
        Open :class:`~tapps_brain.postgres_connection.PostgresConnectionManager`.
    project_id, brain_id, agent_id:
        Tenant / identity scope.  Identical to :func:`record_event`.
    events:
        List of event dicts matching the ``/v1/experience`` single-event
        schema.  Each dict accepts the same keys as the per-event body
        (``event_type``, ``subject_key``, ``utility_score``, ``payload``,
        ``entities``, ``edges``, ``evidence``, ``memory_key``,
        ``memory_value``, ``memory_tier``, ``session_id``,
        ``workflow_run_id``).

    Returns
    -------
    ``{"results": [{"event_id": ..., "memory_key": ..., "entity_ids": ...,
    "edge_ids": ..., "evidence_ids": ...}, ...], "count": int}`` on success,
    or ``{"error": str, "detail": str}`` on validation failure.
    """
    if not isinstance(events, list):
        return {"error": "bad_request", "detail": "events must be a JSON array."}
    if not events:
        return {"error": "bad_request", "detail": "events must contain at least one event."}

    from tapps_brain.experience import (
        EdgeSpec,
        EntitySpec,
        EvidenceSpec,
        ExperienceEvent,
        ExperienceEventRecorder,
        MemorySpec,
    )

    # Pre-validate: every event must carry a non-empty event_type.
    for idx, ev in enumerate(events):
        if not isinstance(ev, dict):
            return {
                "error": "bad_request",
                "detail": f"events[{idx}] must be a JSON object.",
            }
        if not (ev.get("event_type") or "").strip():
            return {
                "error": "bad_request",
                "detail": f"events[{idx}].event_type is required.",
            }

    def _spec_list(raw: Any, cls: type) -> list[Any]:
        if not isinstance(raw, list):
            return []
        return [cls(**item) for item in raw if isinstance(item, dict)]

    event_objs: list[ExperienceEvent] = []
    for ev in events:
        mem: MemorySpec | None = None
        if ev.get("memory_key") and ev.get("memory_value"):
            mem = MemorySpec(
                key=str(ev["memory_key"]),
                value=str(ev["memory_value"]),
                tier=str(ev.get("memory_tier") or "pattern"),
            )
        try:
            us = float(ev.get("utility_score", 0.0))
        except (TypeError, ValueError):
            us = 0.0
        event_objs.append(
            ExperienceEvent(
                event_type=str(ev["event_type"]).strip(),
                subject_key=ev.get("subject_key") or None,
                utility_score=max(0.0, min(1.0, us)),
                payload=ev.get("payload") or {},
                session_id=ev.get("session_id") or None,
                workflow_run_id=ev.get("workflow_run_id") or None,
                memory=mem,
                entities=_spec_list(ev.get("entities"), EntitySpec),
                edges=_spec_list(ev.get("edges"), EdgeSpec),
                evidence=_spec_list(ev.get("evidence"), EvidenceSpec),
            )
        )

    recorder = ExperienceEventRecorder(
        cm, project_id=project_id, brain_id=brain_id, agent_id=agent_id
    )
    # TAP-1934: record_many() writes the whole batch in one transaction —
    # any failure rolls back every event (no partial commits).
    batch_results = recorder.record_many(event_objs)
    return {
        "results": [r.model_dump() for r in batch_results],
        "count": len(batch_results),
    }


# ---------------------------------------------------------------------------
# get_neighbors
# ---------------------------------------------------------------------------


def get_neighbors(
    cm: Any,
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


# TAP-1933: configurable explain BFS ceilings. Server-side env vars cap how
# deep / how wide the brain_explain_connection BFS may walk. Per-call
# max_hops is still clamped against TAPPS_BRAIN_KG_EXPLAIN_MAX_HOPS; per-node
# fan-out is capped by TAPPS_BRAIN_KG_EXPLAIN_BRANCHING_FACTOR so deeper hops
# don't explode query cost on dense graphs.
_DEFAULT_EXPLAIN_MAX_HOPS = 3
_DEFAULT_EXPLAIN_BRANCHING_FACTOR = 50


def _int_env(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, value)


def explain_max_hops_ceiling() -> int:
    """Return the configured ceiling for ``brain_explain_connection`` hops.

    Defaults to 3 (the historical hard clamp). Operators with denser graphs
    can raise it by setting ``TAPPS_BRAIN_KG_EXPLAIN_MAX_HOPS``.
    """
    return _int_env("TAPPS_BRAIN_KG_EXPLAIN_MAX_HOPS", _DEFAULT_EXPLAIN_MAX_HOPS)


def explain_branching_factor() -> int:
    """Return the per-node fan-out cap for ``brain_explain_connection`` BFS.

    Defaults to 50 (the historical implicit cap from ``get_neighbors(limit=50)``).
    Tune via ``TAPPS_BRAIN_KG_EXPLAIN_BRANCHING_FACTOR`` to keep BFS cost
    bounded on dense graphs.
    """
    return _int_env("TAPPS_BRAIN_KG_EXPLAIN_BRANCHING_FACTOR", _DEFAULT_EXPLAIN_BRANCHING_FACTOR)


def explain_connection(
    cm: Any,
    project_id: str,
    brain_id: str,
    *,
    subject_id: str,
    object_id: str,
    max_hops: int = 3,
    branching_factor: int | None = None,
) -> dict[str, Any]:
    """Find the shortest path between *subject_id* and *object_id*.

    Uses BFS over
    :meth:`~tapps_brain.postgres_kg.PostgresKnowledgeGraphStore.get_neighbors`
    up to *max_hops* depth.  Returns the first path found or
    ``found=False`` when no path exists within the hop limit.

    Parameters
    ----------
    subject_id:
        UUID of the starting entity.
    object_id:
        UUID of the target entity.
    max_hops:
        Maximum hops to traverse.  Clamped against the configured ceiling
        :func:`explain_max_hops_ceiling` (default 3, env-tunable via
        ``TAPPS_BRAIN_KG_EXPLAIN_MAX_HOPS``) — TAP-1933.
    branching_factor:
        Per-node fan-out cap on each BFS layer.  When ``None``, falls back
        to :func:`explain_branching_factor` (default 50, env-tunable via
        ``TAPPS_BRAIN_KG_EXPLAIN_BRANCHING_FACTOR``) — TAP-1933.
    """
    ceiling = explain_max_hops_ceiling()
    max_hops = max(1, min(max_hops, ceiling))
    fanout = branching_factor if branching_factor is not None else explain_branching_factor()
    fanout = max(1, min(fanout, 500))

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
                    neighbors = kg.get_neighbors(current_id, direction="both", limit=fanout)
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

    return {
        "found": False,
        "hops": None,
        "path": [],
        "subject_id": subject_id,
        "object_id": object_id,
    }


# ---------------------------------------------------------------------------
# record_kg_feedback
# ---------------------------------------------------------------------------


_DEFAULT_EDGE_CONFIDENCE_DELTA = 0.05


def record_kg_feedback(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    edge_id: str,
    feedback_type: str,
    session_id: str = "",
    confidence_delta: float = _DEFAULT_EDGE_CONFIDENCE_DELTA,
    utility_score: float | None = None,
) -> dict[str, Any]:
    """Record edge-level feedback (``edge_helpful`` or ``edge_misleading``).

    Two-phase write (EPIC-076 STORY-076.6):

    1. **FeedbackStore audit trail** — delegates to
       :func:`~tapps_brain.services.feedback_service.feedback_record` so the
       event lands in ``feedback_events`` with the edge UUID as ``entry_key``.
       This gives EWMA diagnostics and the full audit trail for free.

    2. **KG counter + confidence update** — calls
       :meth:`~tapps_brain.postgres_kg.PostgresKnowledgeGraphStore.apply_edge_feedback`
       on a fresh ``PostgresKnowledgeGraphStore`` (per-call, same CM) to
       update ``useful_access_count``, ``positive/negative_feedback_count``,
       and edge confidence.  A ``Postgres`` connection is required; if none is
       available the FeedbackStore write still succeeds and the counter step is
       skipped with a warning.

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
    confidence_delta:
        Confidence reduction per ``edge_misleading`` event (default 0.05).
    utility_score:
        TAP-1930. Continuous utility signal in ``[-1, 1]``.  Passed to the
        FeedbackStore audit row on **both** edge_helpful and edge_misleading
        so EWMA / flywheel diagnostics pick it up regardless of feedback
        type.  On **edge_misleading**, ``abs(utility_score)`` additionally
        weights the confidence delta so callers can express "how misleading"
        as a continuous signal (max 0.1 at ``|utility_score| = 1.0``).  On
        **edge_helpful**, the underlying ``APPLY_EDGE_HELPFUL_SQL`` does not
        accept a delta — the FSRS reinforce step is binary — so the score
        is recorded in the audit trail but does not alter the edge
        confidence.  When *utility_score* is ``None`` the legacy
        fixed-step behaviour applies. Explicit ``0.0`` is treated as
        "no useful signal" — the fixed step still applies (zeroing the
        delta would defeat the purpose of recording the feedback).
    """
    from tapps_brain.services import feedback_service

    allowed = {"edge_helpful", "edge_misleading"}
    if feedback_type not in allowed:
        return {
            "error": "bad_request",
            "detail": f"feedback_type must be one of {sorted(allowed)!r}.",
        }

    # TAP-1930: validate utility_score range server-side.
    if utility_score is not None and not (-1.0 <= float(utility_score) <= 1.0):
        return {
            "error": "bad_request",
            "detail": "utility_score must be in [-1, 1].",
        }

    # TAP-1930: weight the confidence delta by |utility_score| when a non-zero
    # value is provided.  Explicit 0.0 is treated as "no useful signal" and
    # keeps the legacy fixed step so the call still moves the needle (zeroing
    # the delta would silently drop the misleading penalty altogether).  The
    # weighting applies only to the edge_misleading SQL — see docstring above.
    effective_delta = confidence_delta
    if utility_score is not None and float(utility_score) != 0.0:
        effective_delta = abs(float(utility_score)) * 0.1

    # Phase 1: FeedbackStore audit trail
    fb_result = feedback_service.feedback_record(
        store,
        project_id,
        agent_id,
        event_type=feedback_type,
        entry_key=edge_id,
        session_id=session_id,
        utility_score=utility_score,
        details_json=json.dumps({"edge_id": edge_id}),
    )
    if isinstance(fb_result, dict) and fb_result.get("error"):
        return fb_result

    # Phase 2: KG counter + confidence update
    cm = _get_or_create_cm()
    if cm is None:
        logger.warning(
            "kg.feedback.no_cm",
            edge_id=edge_id,
            feedback_type=feedback_type,
            detail="TAPPS_BRAIN_DATABASE_URL not set; KG counters not updated.",
        )
        return {**fb_result, "kg_update": "skipped_no_db"}

    from tapps_brain.postgres_kg import PostgresKnowledgeGraphStore

    brain_id = _DEFAULT_BRAIN_ID
    kg = PostgresKnowledgeGraphStore(cm, brain_id=brain_id, project_id=project_id)
    try:
        kg_result = kg.apply_edge_feedback(
            edge_id,
            feedback_type,
            confidence_delta=effective_delta,
        )
    finally:
        kg.close()

    return {**fb_result, "kg_update": kg_result}
