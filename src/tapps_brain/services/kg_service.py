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
from pydantic import ValidationError

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
# Resilient spec coercion (TAP-2866)
# ---------------------------------------------------------------------------


def _validation_warning(kind: str, index: int, exc: ValidationError) -> dict[str, Any]:
    """Build a structured, payload-free warning from a spec ``ValidationError``.

    ``include_input`` / ``include_url`` are disabled so the warning never
    echoes the caller's payload or external pydantic doc URLs.
    """
    return {
        "kind": kind,
        "index": index,
        "errors": [
            {
                "field": ".".join(str(part) for part in err.get("loc", ())),
                "msg": err.get("msg", ""),
                "type": err.get("type", ""),
            }
            for err in exc.errors(include_url=False, include_input=False)
        ],
    }


def _coerce_specs(
    items: list[dict[str, Any]],
    cls: type,
    kind: str,
    warnings: list[dict[str, Any]],
) -> list[Any]:
    """Coerce *items* into *cls* instances, skipping (not raising on) the bad ones.

    TAP-2866: a malformed KG side-effect spec — e.g. an ``edges`` entry missing
    the required ``subject_entity_id`` / ``object_entity_id`` UUIDs — is dropped
    and recorded in *warnings* rather than aborting the whole event.  The core
    ``experience_events`` row (and every spec that *does* validate) still writes,
    so a consumer's event stream is never lost because one optional enrichment
    was malformed.  The prior behaviour raised ``ValidationError``, which the
    HTTP adapter masked as a 500 (the TAP-2865 incident).  Same intent as
    TAP-2675's ``EntitySpec`` coercion, generalised to all side-effect specs and
    made non-fatal.
    """
    specs: list[Any] = []
    for index, item in enumerate(items):
        try:
            specs.append(cls(**item))
        except ValidationError as exc:
            warnings.append(_validation_warning(kind, index, exc))
    return specs


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

    def _items(native: list[dict[str, Any]] | None, legacy_json: str) -> list[dict[str, Any]]:
        """Select dict items from the native list (preferred) or legacy JSON string."""
        if isinstance(native, list):
            return [it for it in native if isinstance(it, dict)]
        if legacy_json and legacy_json.strip() not in ("", "[]"):
            try:
                parsed = json.loads(legacy_json)
            except json.JSONDecodeError:
                parsed = []
            if isinstance(parsed, list):
                return [it for it in parsed if isinstance(it, dict)]
        return []

    # TAP-2866: malformed side-effect specs are skipped + reported, never fatal.
    warnings: list[dict[str, Any]] = []
    entity_specs = _coerce_specs(_items(entities, entities_json), EntitySpec, "entity", warnings)
    edge_specs = _coerce_specs(_items(edges, edges_json), EdgeSpec, "edge", warnings)
    evidence_specs = _coerce_specs(
        _items(evidence, evidence_json), EvidenceSpec, "evidence", warnings
    )

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
    result_dict = result.model_dump()
    if warnings:
        result_dict["warnings"] = warnings
    return result_dict


# ---------------------------------------------------------------------------
# query_events (TAP-3157 / STORY-074.1)
# ---------------------------------------------------------------------------

_QUERY_EVENTS_MAX_LIMIT = 500
_QUERY_EVENTS_DEFAULT_LIMIT = 100


def query_events(
    cm: Any,
    project_id: str,
    *,
    event_type: str,
    since: str | None = None,
    until: str | None = None,
    entity_id: str | None = None,
    limit: int = _QUERY_EVENTS_DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Query ``experience_events`` rows with optional time and entity filters.

    Returns full write-time ``payload`` JSONB for each matching row.  The
    optional ``entity_id`` filter matches ``payload.file_path`` or
    ``subject_key`` (v1 — not KG entity UUID).

    Parameters
    ----------
    cm:
        Open :class:`~tapps_brain.postgres_connection.PostgresConnectionManager`.
    project_id:
        Tenant scope (RLS via ``project_context``).
    event_type:
        Required semantic category filter (e.g. ``quality_metric``).
    since / until:
        Optional ISO-8601 bounds on ``event_time`` (inclusive).
    entity_id:
        Optional file-path or subject-key filter.
    limit:
        Maximum rows returned (default 100, server cap 500).
    """
    if not (event_type or "").strip():
        return {"error": "bad_request", "detail": "event_type is required."}

    eff_limit = max(1, min(int(limit), _QUERY_EVENTS_MAX_LIMIT))

    conditions: list[str] = ["event_type = %s"]
    params: list[Any] = [event_type.strip()]

    if since and since.strip():
        conditions.append("event_time >= %s")
        params.append(since.strip())
    if until and until.strip():
        conditions.append("event_time <= %s")
        params.append(until.strip())
    if entity_id and entity_id.strip():
        eid = entity_id.strip()
        conditions.append("(payload->>'file_path' = %s OR subject_key = %s)")
        params.extend([eid, eid])

    where = " AND ".join(conditions)
    sql = (
        "SELECT id::text, event_type, payload, event_time, agent_id, session_id "
        f"FROM experience_events WHERE {where} "
        "ORDER BY event_time DESC LIMIT %s"
    )
    params.append(eff_limit)

    events: list[dict[str, Any]] = []
    with cm.project_context(project_id) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    for row in rows:
        event_time = row[3]
        ts = event_time.isoformat() if hasattr(event_time, "isoformat") else str(event_time)
        payload = row[2] if isinstance(row[2], dict) else {}
        evt: dict[str, Any] = {
            "event_id": row[0],
            "event_type": row[1],
            "payload": payload,
            "ts": ts,
            "agent_id": row[4],
        }
        if row[5]:
            evt["session_id"] = row[5]
        events.append(evt)

    return {"events": events, "count": len(events)}


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

    def _spec_list(raw: Any, cls: type, kind: str, warnings: list[dict[str, Any]]) -> list[Any]:
        if not isinstance(raw, list):
            return []
        return _coerce_specs([it for it in raw if isinstance(it, dict)], cls, kind, warnings)

    event_objs: list[ExperienceEvent] = []
    # TAP-2866: one warning bucket per event; malformed side-effect specs are
    # skipped + reported rather than failing the batch.
    per_event_warnings: list[list[dict[str, Any]]] = []
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
        ev_warnings: list[dict[str, Any]] = []
        event_objs.append(
            ExperienceEvent(
                event_type=str(ev["event_type"]).strip(),
                subject_key=ev.get("subject_key") or None,
                utility_score=max(0.0, min(1.0, us)),
                payload=ev.get("payload") or {},
                session_id=ev.get("session_id") or None,
                workflow_run_id=ev.get("workflow_run_id") or None,
                memory=mem,
                entities=_spec_list(ev.get("entities"), EntitySpec, "entity", ev_warnings),
                edges=_spec_list(ev.get("edges"), EdgeSpec, "edge", ev_warnings),
                evidence=_spec_list(ev.get("evidence"), EvidenceSpec, "evidence", ev_warnings),
            )
        )
        per_event_warnings.append(ev_warnings)

    recorder = ExperienceEventRecorder(
        cm, project_id=project_id, brain_id=brain_id, agent_id=agent_id
    )
    # TAP-1934: record_many() writes the whole batch in one transaction —
    # any failure rolls back every event (no partial commits).
    batch_results = recorder.record_many(event_objs)
    results: list[dict[str, Any]] = []
    for record_result, warns in zip(batch_results, per_event_warnings, strict=False):
        result_dict = record_result.model_dump()
        if warns:
            result_dict["warnings"] = warns
        results.append(result_dict)
    return {
        "results": results,
        "count": len(results),
    }


#: TAP-1973 — server-side cap on the per-event-tx batch tool.  Sized to match
#: ``ExperienceEventRecorder.record_many`` limit so the two batch surfaces
#: refuse the same shape.
_RECORD_EVENTS_BATCH_MAX_EVENTS = 200


def _validate_events_batch_input(events: Any, max_events: int) -> dict[str, Any] | None:
    """Structural guards for the per-event-tx batch (TAP-2758).

    Returns an error envelope when *events* is not a non-empty list within
    *max_events*, else ``None`` to signal "proceed".
    """
    if not isinstance(events, list):
        return {"error": "bad_request", "detail": "events must be a JSON array."}
    if not events:
        return {"error": "bad_request", "detail": "events must contain at least one event."}
    if len(events) > max_events:
        return {
            "error": "too_many_events",
            "detail": (
                f"batch contains {len(events)} events; server caps each call at {max_events}."
            ),
        }
    return None


def _event_record_kwargs(ev: dict[str, Any]) -> dict[str, Any]:
    """Map one event dict to :func:`record_event` keyword arguments (TAP-2758).

    Centralises the field coalescing so the per-event processor stays flat.
    """
    return {
        "event_type": str(ev["event_type"]).strip(),
        "subject_key": ev.get("subject_key") or None,
        "utility_score": float(ev.get("utility_score", 0.0) or 0.0),
        "payload": ev.get("payload") or {},
        "entities": ev.get("entities") or [],
        "edges": ev.get("edges") or [],
        "evidence": ev.get("evidence") or [],
        "memory_key": ev.get("memory_key") or None,
        "memory_value": ev.get("memory_value") or None,
        "memory_tier": str(ev.get("memory_tier") or "pattern"),
        "session_id": ev.get("session_id") or None,
        "workflow_run_id": ev.get("workflow_run_id") or None,
    }


def _process_one_event(
    cm: Any,
    project_id: str,
    brain_id: str,
    agent_id: str,
    idx: int,
    ev: Any,
) -> tuple[bool, dict[str, Any]]:
    """Validate + record a single event in its own transaction (TAP-2758).

    Returns ``(ok, entry)`` where *entry* is the ``succeeded`` record on success
    or the ``failed`` record (tagged with *idx*) otherwise — so the caller's
    loop is a plain dispatch with no branching.
    """
    if not isinstance(ev, dict):
        return False, {
            "index": idx,
            "error": "bad_request",
            "detail": f"events[{idx}] must be a JSON object.",
        }
    if not (ev.get("event_type") or "").strip():
        return False, {
            "index": idx,
            "error": "bad_request",
            "detail": f"events[{idx}].event_type is required.",
        }
    # Delegate to record_event (per-event tx).  Any uncaught exception is caught
    # and tagged with the event index so the caller can correlate failures.
    try:
        result = record_event(cm, project_id, brain_id, agent_id, **_event_record_kwargs(ev))
    except Exception as exc:  # per-event failure isolation
        return False, {"index": idx, "error": exc.__class__.__name__, "detail": str(exc)}
    if isinstance(result, dict) and result.get("error"):
        return False, {
            "index": idx,
            "error": str(result.get("error")),
            "detail": str(result.get("detail", "")),
        }
    return True, {"index": idx, "result": result}


def record_events_batch_per_event_tx(
    cm: Any,
    project_id: str,
    brain_id: str,
    agent_id: str,
    *,
    events: list[dict[str, Any]],
    max_events: int = _RECORD_EVENTS_BATCH_MAX_EVENTS,
) -> dict[str, Any]:
    """Write *events* with **per-event transactions** (TAP-1973).

    Sibling of :func:`record_events_batch` (TAP-1934) which uses a single
    batch transaction — that one rejects the whole batch on any failure.
    This variant runs each event in its own transaction so a single bad
    event in a 100-event backfill does not abort the rest.

    Used by ``brain_record_events_batch`` (MCP) and the tapps-mcp EPIC-203
    migration utility (story 203.7) for N-event backfill where partial
    success is the desired semantics.

    Parameters
    ----------
    cm:
        Open :class:`~tapps_brain.postgres_connection.PostgresConnectionManager`.
    project_id, brain_id, agent_id:
        Tenant / identity scope (identical to :func:`record_event`).
    events:
        List of event dicts matching the ``/v1/experience`` single-event
        schema.  Same keys as :func:`record_events_batch` accepts.
    max_events:
        Server-side cap (default 200).  Inputs above this size are rejected
        wholesale with ``too_many_events`` — clients chunk and retry.

    Returns
    -------
    On success::

        {"succeeded": [{"index": int, "result": {...}}, ...],
         "failed":    [{"index": int, "error": str, "detail": str}, ...],
         "count":     int,                 # len(events) (validated input size)
         "succeeded_count": int,
         "failed_count":    int}

    On structural rejection (not a list, empty, too many, malformed event)::

        {"error": "bad_request" | "too_many_events", "detail": "..."}
    """
    rejection = _validate_events_batch_input(events, max_events)
    if rejection is not None:
        return rejection

    succeeded: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for idx, ev in enumerate(events):
        ok, entry = _process_one_event(cm, project_id, brain_id, agent_id, idx, ev)
        (succeeded if ok else failed).append(entry)

    return {
        "succeeded": succeeded,
        "failed": failed,
        "count": len(events),
        "succeeded_count": len(succeeded),
        "failed_count": len(failed),
    }


# ---------------------------------------------------------------------------
# resolve_entity (TAP-2725)
# ---------------------------------------------------------------------------


def resolve_entity(
    cm: Any,
    project_id: str,
    brain_id: str,
    *,
    entity_type: str,
    canonical_name: str,
) -> dict[str, Any]:
    """Resolve or create a KG entity by ``(entity_type, canonical_name)``; return its UUID.

    First attempts an exact-canonical-name / alias lookup via
    :meth:`~tapps_brain.postgres_kg.PostgresKnowledgeGraphStore.resolve_entity`.
    When no match is found, creates the entity via
    :meth:`~tapps_brain.postgres_kg.PostgresKnowledgeGraphStore.upsert_entity` (idempotent
    ``INSERT … ON CONFLICT`` — safe to call concurrently).

    Parameters
    ----------
    cm:
        Open :class:`~tapps_brain.postgres_connection.PostgresConnectionManager`.
    project_id, brain_id:
        Tenant / identity scope.
    entity_type:
        Entity type label (e.g. ``"module"``, ``"agent"``, ``"tool"``).
    canonical_name:
        Human-readable name used as the lookup key (case-sensitive;
        stored as-is and lower-cased internally for alias matching).

    Returns
    -------
    ``{"entity_id": str, "entity_type": str, "canonical_name": str,
    "created": bool, "confidence": float, "reason": str}``
    """
    kg = _kg_store(cm, project_id, brain_id)
    try:
        entity_id, confidence, reason = kg.resolve_entity(entity_type, canonical_name)
        if entity_id is not None:
            return {
                "entity_id": entity_id,
                "entity_type": entity_type,
                "canonical_name": canonical_name,
                "created": False,
                "confidence": confidence,
                "reason": reason,
            }
        # Not found — create via upsert_entity (idempotent INSERT ON CONFLICT).
        new_id = kg.upsert_entity(entity_type=entity_type, canonical_name=canonical_name)
        return {
            "entity_id": new_id,
            "entity_type": entity_type,
            "canonical_name": canonical_name,
            "created": True,
            "confidence": 0.6,
            "reason": "created",
        }
    finally:
        kg.close()


def collect_neighbor_entity_ids(
    cm: Any,
    project_id: str,
    brain_id: str,
    *,
    entity_ids: list[str] | None = None,
    entity_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Merge explicit UUIDs with resolved entity refs for neighbourhood queries."""
    merged = list(entity_ids or [])
    if entity_refs:
        resolved = resolve_entity_refs(cm, project_id, brain_id, refs=entity_refs)
        if resolved.get("error"):
            return resolved
        merged.extend(resolved.get("entity_ids", []))
    if not merged:
        return {
            "error": "bad_request",
            "detail": "entity_ids or entity_refs is required.",
        }
    return {"entity_ids": merged}


def resolve_entity_refs(
    cm: Any,
    project_id: str,
    brain_id: str,
    *,
    refs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Resolve ``entity_refs`` objects to entity UUID strings.

    Each ref accepts ``entity_type``/``canonical_name`` or tapps-mcp
    ``type``/``id`` shorthand.  Returns ``{"entity_ids": [str, ...]}`` or an
    error envelope.
    """
    if not refs:
        return {"entity_ids": []}

    entity_ids: list[str] = []
    for idx, ref in enumerate(refs):
        if not isinstance(ref, dict):
            return {
                "error": "bad_request",
                "detail": f"entity_refs[{idx}] must be a JSON object.",
            }
        entity_type = (ref.get("entity_type") or ref.get("type") or "").strip()
        canonical_name = (
            ref.get("canonical_name") or ref.get("id") or ref.get("key") or ""
        ).strip()
        if not entity_type or not canonical_name:
            return {
                "error": "bad_request",
                "detail": (
                    f"entity_refs[{idx}] requires entity_type and canonical_name "
                    "(or type/id shorthand)."
                ),
            }
        resolved = resolve_entity(
            cm,
            project_id,
            brain_id,
            entity_type=entity_type,
            canonical_name=canonical_name,
        )
        if resolved.get("error"):
            return resolved
        entity_ids.append(str(resolved["entity_id"]))
    return {"entity_ids": entity_ids}


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
