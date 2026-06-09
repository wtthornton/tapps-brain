"""Knowledge-Graph MCP tool registrations (EPIC-076 STORY-076.5).

Exposes KG / experience tools on the standard server:

* ``brain_record_event``  — write one :class:`~tapps_brain.experience.ExperienceEvent`
  plus optional memory / entity / edge / evidence atomically.
* ``brain_query_events`` — query stored experience event payloads (TAP-3157).
* ``brain_get_neighbors`` — fetch 1-hop or 2-hop neighbourhood around entities.
* ``brain_explain_connection`` — find the shortest path (≤3 hops) between two
  entities.
* ``brain_record_feedback`` — record ``edge_helpful`` / ``edge_misleading``
  feedback via the existing :class:`~tapps_brain.feedback.FeedbackStore` path.

All tools follow the same thin-wrapper pattern as
:mod:`tapps_brain.mcp_server.tools_brain`:

1. Resolve the effective ``agent_id`` via the per-call resolver.
2. Call a function from :mod:`tapps_brain.services.kg_service`.
3. Return ``json.dumps(result, default=str)``.

The service layer returns plain dicts so the JSON serialisation step is always
trivial — no Pydantic models are imported in the hot path.
"""

from __future__ import annotations

import json
import uuid as _stdlib_uuid
import warnings
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tapps_brain.mcp_server.context import ToolContext

from tapps_brain.services import kg_service


def _bad_json_error(field: str, detail: str) -> dict[str, str]:
    """Build the canonical bad-JSON envelope used across KG MCP tools (TAP-1967)."""
    return {"error": "bad_json", "field": field, "detail": detail}


def _bad_uuid_error(field: str, value: str) -> dict[str, str]:
    """Build the canonical bad-UUID envelope (TAP-2726).

    Returned when a caller passes a non-UUID string to a KG tool parameter
    that requires a pre-resolved entity or edge UUID.  The ``field`` names
    the offending parameter so the caller knows exactly what to fix.
    """
    return {
        "error": "bad_uuid",
        "field": field,
        "detail": f"'{field}' must be a valid UUID; got {value!r}",
    }


def _validate_uuid(value: str, field: str) -> dict[str, str] | None:
    """Return ``None`` when *value* is a valid UUID string, or the bad_uuid envelope.

    Validates identifier strings **before** any ``::uuid`` cast reaches
    Postgres, preventing raw ``invalid input syntax for type uuid`` errors
    from leaking to callers (TAP-2726).
    """
    try:
        _stdlib_uuid.UUID(value)
        return None
    except (ValueError, AttributeError):
        return _bad_uuid_error(field, value)


def _validate_optional_json_object(raw: str, field: str) -> dict[str, str] | None:
    """Validate that *raw* parses to a JSON object — or is empty (TAP-1969).

    Returns ``None`` on empty / valid input; otherwise the ``bad_json``
    envelope identifying *field*.  Used by ``brain_record_feedback`` to
    fail the call before either feedback path runs.
    """
    if not raw or not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _bad_json_error(field, str(exc))
    if not isinstance(parsed, dict):
        return _bad_json_error(field, f"expected JSON object, got {type(parsed).__name__}")
    return None


def _coerce_payload(
    native: dict[str, Any] | None,
    legacy_json: str,
) -> tuple[dict[str, Any], dict[str, str] | None]:
    """Resolve effective payload from native dict or deprecated JSON string.

    Native ``payload`` wins.  When only ``payload_json`` is provided, emit
    :class:`DeprecationWarning` and parse it.

    Returns ``(payload, None)`` on success or ``({}, error_dict)`` when the
    legacy JSON string cannot be decoded (TAP-1967 — was silently swallowed).
    Empty input and ``"{}"`` still map to ``({}, None)``.
    """
    if isinstance(native, dict):
        return native, None
    if legacy_json and legacy_json.strip() not in ("", "{}"):
        warnings.warn(
            "brain_record_event(payload_json=...) is deprecated; "
            "pass payload=<dict> instead. Removed in the next minor release.",
            DeprecationWarning,
            stacklevel=3,
        )
        try:
            parsed = json.loads(legacy_json)
        except json.JSONDecodeError as exc:
            return {}, _bad_json_error("payload_json", str(exc))
        if isinstance(parsed, dict):
            return parsed, None
        return {}, _bad_json_error(
            "payload_json", f"expected JSON object, got {type(parsed).__name__}"
        )
    return {}, None


def _coerce_list(
    native: list[dict[str, Any]] | None,
    legacy_json: str,
    field_name: str,
) -> tuple[list[dict[str, Any]], dict[str, str] | None]:
    """Resolve effective list-of-dicts from native list or deprecated JSON string.

    Native ``entities`` / ``edges`` / ``evidence`` wins.  When only the
    ``*_json`` alias is provided and non-empty, emit
    :class:`DeprecationWarning` and parse it.

    Returns ``(items, None)`` on success or ``([], error_dict)`` when the
    legacy JSON string cannot be decoded (TAP-1967 — was silently swallowed).
    Empty input and ``"[]"`` still map to ``([], None)``.
    """
    if isinstance(native, list):
        return [item for item in native if isinstance(item, dict)], None
    if legacy_json and legacy_json.strip() not in ("", "[]"):
        warnings.warn(
            f"brain_record_event({field_name}_json=...) is deprecated; "
            f"pass {field_name}=<list[dict]> instead. "
            "Removed in the next minor release.",
            DeprecationWarning,
            stacklevel=3,
        )
        try:
            parsed = json.loads(legacy_json)
        except json.JSONDecodeError as exc:
            return [], _bad_json_error(f"{field_name}_json", str(exc))
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)], None
        return [], _bad_json_error(
            f"{field_name}_json", f"expected JSON array, got {type(parsed).__name__}"
        )
    return [], None


def register_kg_tools(mcp: Any, ctx: ToolContext) -> None:  # noqa: ANN401, PLR0915
    """Register the four KG tools on *mcp* (TAP-1502 STORY-076.5)."""
    _server_aid = ctx.server_agent_id
    _resolve = ctx.resolve_store_for_call
    _pid = ctx.pid
    _rpc = ctx.resolve_per_call_agent_id

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_record_event(
        event_type: str,
        subject_key: str = "",
        utility_score: float = 0.0,
        payload: dict[str, Any] | None = None,
        entities: list[dict[str, Any]] | None = None,
        edges: list[dict[str, Any]] | None = None,
        evidence: list[dict[str, Any]] | None = None,
        memory_key: str = "",
        memory_value: str = "",
        memory_tier: str = "pattern",
        session_id: str = "",
        workflow_run_id: str = "",
        agent_id: str = "",
        # Deprecated JSON-string aliases (TAP-1932). Removed in next minor.
        payload_json: str = "",
        entities_json: str = "",
        edges_json: str = "",
        evidence_json: str = "",
    ) -> str:
        """Record an experience event with optional KG side-effects.

        Writes one ``experience_events`` row plus optional private memory,
        KG entity upserts, KG edge upserts, and evidence inserts — all in a
        **single Postgres transaction**.  Any failure on any side-effect rolls
        back the entire transaction including the event row.

        Parameters
        ----------
        event_type:
            Semantic event category, e.g. ``workflow_completed``,
            ``tool_called``, ``approach_failed``, ``memory_recalled``.
        subject_key:
            Optional primary memory key this event relates to.
        utility_score:
            Measured utility ``[0, 1]``.  Defaults to ``0.0``.
        payload:
            ``dict`` of arbitrary event metadata (TAP-1932 native shape).
            Omit or pass ``None`` for an empty payload.
        entities:
            ``list[dict]`` matching :class:`~tapps_brain.experience.EntitySpec`.
            Native shape (TAP-1932) — REST `/v1/experience` parity.  Pass
            ``None`` or ``[]`` to skip.
        edges:
            ``list[dict]`` matching :class:`~tapps_brain.experience.EdgeSpec`.
            Both ``subject_entity_id`` and ``object_entity_id`` must be
            pre-resolved entity UUIDs.  Pass ``None`` or ``[]`` to skip.
        evidence:
            ``list[dict]`` matching :class:`~tapps_brain.experience.EvidenceSpec`.
            Pass ``None`` or ``[]`` to skip.
        memory_key / memory_value:
            When both are provided, a private memory is written atomically
            alongside the event.
        memory_tier:
            Tier for the optional private memory (``pattern`` by default).
        session_id / workflow_run_id:
            Optional grouping identifiers for correlation.
        agent_id:
            Override the server-level default for this call (STORY-070.7).
        payload_json, entities_json, edges_json, evidence_json:
            **Deprecated (TAP-1932).** JSON-string aliases retained for one
            minor cycle.  Use native ``payload`` / ``entities`` / ``edges`` /
            ``evidence`` instead.  Emits :class:`DeprecationWarning` when
            non-empty.  Removed in the next minor release.

        Returns
        -------
        JSON object: ``{ "event_id": str, "memory_key": str|null,
        "entity_ids": [str], "edge_ids": [str], "evidence_ids": [str] }``
        """
        try:
            eff_aid = _rpc(agent_id, default=_server_aid)
        except ValueError as exc:
            return json.dumps({"error": "bad_request", "detail": str(exc)})
        project_id = _pid()

        cm = kg_service._get_or_create_cm()
        if cm is None:
            return json.dumps(
                {"error": "db_unavailable", "detail": "TAPPS_BRAIN_DATABASE_URL is not set."}
            )

        # TAP-1932: prefer native shapes; fall back to deprecated _json aliases.
        # TAP-1967: surface decode failures as a structured bad_json error
        # rather than silently swallowing them (no event row is written).
        eff_payload, payload_err = _coerce_payload(payload, payload_json)
        eff_entities, ent_err = _coerce_list(entities, entities_json, "entities")
        eff_edges, edge_err = _coerce_list(edges, edges_json, "edges")
        eff_evidence, ev_err = _coerce_list(evidence, evidence_json, "evidence")
        for err in (payload_err, ent_err, edge_err, ev_err):
            if err is not None:
                return json.dumps(err)

        # TAP-2726: validate edge entity UUIDs before any DB ::uuid cast.
        for _i, _edge in enumerate(eff_edges):
            for _uid_field in ("subject_entity_id", "object_entity_id"):
                _val = _edge.get(_uid_field)
                if _val:
                    _uuid_err = _validate_uuid(str(_val), f"edges[{_i}].{_uid_field}")
                    if _uuid_err is not None:
                        return json.dumps(_uuid_err)

        result = kg_service.record_event(
            cm,
            project_id,
            kg_service._DEFAULT_BRAIN_ID,
            eff_aid,
            event_type=event_type,
            subject_key=subject_key or None,
            utility_score=float(utility_score),
            payload=eff_payload,
            entities=eff_entities,
            edges=eff_edges,
            evidence=eff_evidence,
            memory_key=memory_key or None,
            memory_value=memory_value or None,
            memory_tier=memory_tier or "pattern",
            session_id=session_id or None,
            workflow_run_id=workflow_run_id or None,
        )
        return json.dumps(result, default=str)

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_record_events_batch(
        events_json: str,
        agent_id: str = "",
    ) -> str:
        """Record many experience events in one MCP round-trip (TAP-1973).

        Sibling of :func:`brain_record_event` for N-event backfill.  Each
        event runs in **its own Postgres transaction** so a single bad event
        in the batch does not abort the rest — partial success is allowed
        and surfaced through the ``failed`` array.

        The single-transaction variant lives at the REST surface
        (``POST /v1/experience:batch`` — TAP-1934).  Pick that one when you
        need all-or-nothing semantics; pick this tool when you want
        best-effort import of a large flat dump.

        Parameters
        ----------
        events_json:
            JSON-array string of event dicts.  Each dict accepts the same
            keys as :func:`brain_record_event` (``event_type``,
            ``subject_key``, ``utility_score``, ``payload``, ``entities``,
            ``edges``, ``evidence``, ``memory_key``, ``memory_value``,
            ``memory_tier``, ``session_id``, ``workflow_run_id``).
            Capped server-side at 200 events per call.
        agent_id:
            Override the server-level default for this call (STORY-070.7).

        Returns
        -------
        On success::

            { "succeeded": [{"index": int, "result": {...}}, ...],
              "failed":    [{"index": int, "error": str, "detail": str}, ...],
              "count":     int,
              "succeeded_count": int,
              "failed_count":    int }

        Malformed ``events_json`` returns the canonical bad-json envelope
        ``{"error": "bad_json", "field": "events_json", "detail": "<msg>"}``
        (TAP-1967 / EPIC-300).  A structurally invalid (non-array, empty,
        too-large) payload returns ``{"error": "bad_request" |
        "too_many_events", "detail": "..."}``.
        """
        try:
            eff_aid = _rpc(agent_id, default=_server_aid)
        except ValueError as exc:
            return json.dumps({"error": "bad_request", "detail": str(exc)})
        project_id = _pid()

        # TAP-1967 envelope on JSON decode + shape mismatch.
        if not events_json or not events_json.strip():
            return json.dumps({"error": "bad_request", "detail": "events_json is required."})
        try:
            parsed = json.loads(events_json)
        except json.JSONDecodeError as exc:
            return json.dumps(_bad_json_error("events_json", str(exc)))
        if not isinstance(parsed, list):
            return json.dumps(
                _bad_json_error(
                    "events_json",
                    f"expected JSON array, got {type(parsed).__name__}",
                )
            )

        cm = kg_service._get_or_create_cm()
        if cm is None:
            return json.dumps(
                {"error": "db_unavailable", "detail": "TAPPS_BRAIN_DATABASE_URL is not set."}
            )

        # TAP-2726: pre-validate edge entity UUIDs per event before any DB
        # ::uuid cast.  Invalid events are collected in pre_failures and
        # excluded from the service call; they appear in the unified ``failed``
        # array alongside service-layer failures so callers get one coherent
        # response.
        valid_events: list[Any] = []
        pre_failures: list[dict[str, Any]] = []
        for _i, _event in enumerate(parsed):
            _uuid_err = None
            if isinstance(_event, dict):
                for _edge in _event.get("edges") or []:
                    if not isinstance(_edge, dict):
                        continue
                    for _uid_field in ("subject_entity_id", "object_entity_id"):
                        _val = _edge.get(_uid_field)
                        if _val:
                            _uuid_err = _validate_uuid(
                                str(_val), f"events[{_i}].edges.{_uid_field}"
                            )
                            if _uuid_err is not None:
                                break
                    if _uuid_err is not None:
                        break
            if _uuid_err is not None:
                pre_failures.append(
                    {
                        "index": _i,
                        "error": _uuid_err["error"],
                        "field": _uuid_err.get("field", ""),
                        "detail": _uuid_err["detail"],
                    }
                )
            else:
                valid_events.append(_event)

        if valid_events:
            svc_result: dict[str, Any] = kg_service.record_events_batch_per_event_tx(
                cm,
                project_id,
                kg_service._DEFAULT_BRAIN_ID,
                eff_aid,
                events=valid_events,
            )
        else:
            svc_result = {
                "succeeded": [],
                "failed": [],
                "count": 0,
                "succeeded_count": 0,
                "failed_count": 0,
            }

        if pre_failures:
            existing_failed = svc_result.get("failed") or []
            merged_failed = list(existing_failed) + pre_failures
            svc_result = dict(svc_result)
            svc_result["failed"] = merged_failed
            svc_result["failed_count"] = len(merged_failed)
            svc_result["count"] = len(parsed)
            # succeeded_count comes from the service result unchanged; it
            # already reflects only the valid_events slice.

        return json.dumps(svc_result, default=str)

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_query_events(
        event_type: str,
        since: str = "",
        until: str = "",
        entity_id: str = "",
        limit: int = 100,
    ) -> str:
        """Query stored experience events with full payload round-trip.

        REST counterpart: ``POST /v1/experience:query``.  Unblocks tapps-mcp
        metrics migration (TAP-3157) — returns write-time ``payload`` JSONB
        from ``experience_events``.

        Parameters
        ----------
        event_type:
            Required semantic category (e.g. ``quality_metric``).
        since / until:
            Optional ISO-8601 UTC bounds on ``event_time`` (inclusive).
        entity_id:
            Optional filter matching ``payload.file_path`` or ``subject_key``
            (v1 — not KG entity UUID).
        limit:
            Maximum rows (default 100, server cap 500).

        Returns
        -------
        JSON object: ``{ "events": [{event_id, event_type, payload, ts,
        agent_id, session_id?}], "count": int }``
        """
        project_id = _pid()

        cm = kg_service._get_or_create_cm()
        if cm is None:
            return json.dumps(
                {"error": "db_unavailable", "detail": "TAPPS_BRAIN_DATABASE_URL is not set."}
            )

        result = kg_service.query_events(
            cm,
            project_id,
            event_type=event_type,
            since=since or None,
            until=until or None,
            entity_id=entity_id or None,
            limit=limit,
        )
        return json.dumps(result, default=str)

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_get_neighbors(
        entity_ids_json: str,
        hops: int = 1,
        limit: int = 20,
        predicate_filter: str = "",
        agent_id: str = "",
    ) -> str:
        """Return the neighbourhood graph around one or more KG entities.

        Fetches 1-hop or 2-hop neighbours for all ``entity_ids`` in a single
        SQL round-trip and returns scored edge + entity rows.

        Parameters
        ----------
        entity_ids_json:
            JSON array of entity UUID strings, e.g.
            ``'["uuid1", "uuid2"]'``.
        hops:
            Neighbourhood depth: ``1`` (direct neighbours) or ``2``
            (two-hop recursive CTE).  Values > 2 are clamped to ``2``.
        limit:
            Maximum total edge rows to return (capped at 200).
        predicate_filter:
            When non-empty, only edges whose predicate matches are returned.
        agent_id:
            Override the server-level default for this call (STORY-070.7).

        Returns
        -------
        JSON object: ``{ "neighbors": [{edge_id, predicate, edge_confidence,
        neighbor_id, entity_type, canonical_name, hop, ...}],
        "entity_ids": [str] }``
        """
        # TAP-1936: under TAPPS_BRAIN_STRICT_AGENT_ID=1 the resolver raises
        # on header/kwarg disagreement.  Translate to the same {error: ...}
        # envelope the other KG tools use for consistency.
        try:
            eff_aid = _rpc(agent_id, default=_server_aid)
        except ValueError as exc:
            return json.dumps({"error": "bad_request", "detail": str(exc)})
        project_id = _pid()

        # eff_aid is intentionally computed for its side effects (the mismatch
        # warning in strict-soft mode) — the get_neighbors service routine
        # does not currently take an agent_id.
        _ = eff_aid

        cm = kg_service._get_or_create_cm()
        if cm is None:
            return json.dumps(
                {"error": "db_unavailable", "detail": "TAPPS_BRAIN_DATABASE_URL is not set."}
            )

        # TAP-1968: surface JSON decode failures as a structured bad_json error
        # rather than silently substituting an empty entity_ids list (which used
        # to return a misleading empty-neighbourhood response).
        entity_ids: list[str] = []
        if entity_ids_json and entity_ids_json.strip():
            try:
                parsed = json.loads(entity_ids_json)
            except json.JSONDecodeError as exc:
                return json.dumps(_bad_json_error("entity_ids_json", str(exc)))
            if not isinstance(parsed, list):
                return json.dumps(
                    _bad_json_error(
                        "entity_ids_json",
                        f"expected JSON array, got {type(parsed).__name__}",
                    )
                )
            entity_ids = [str(e) for e in parsed if e]

        # TAP-2726: validate each entity ID is a UUID before the DB ::uuid cast.
        for i, eid in enumerate(entity_ids):
            uuid_err = _validate_uuid(eid, f"entity_ids_json[{i}]")
            if uuid_err is not None:
                return json.dumps(uuid_err)

        result = kg_service.get_neighbors(
            cm,
            project_id,
            kg_service._DEFAULT_BRAIN_ID,
            entity_ids=entity_ids,
            hops=max(1, min(int(hops), 2)),
            limit=max(1, min(int(limit), 200)),
            predicate_filter=predicate_filter or None,
        )
        return json.dumps(result, default=str)

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_explain_connection(
        subject_id: str,
        object_id: str,
        max_hops: int = 3,
        agent_id: str = "",
    ) -> str:
        """Find the shortest path between two KG entities.

        Performs a BFS traversal over the active edge graph from *subject_id*
        to *object_id* up to *max_hops* depth (clamped to 3).  Returns the
        first path found, or ``found=false`` when no path exists.

        Parameters
        ----------
        subject_id:
            UUID of the starting entity.
        object_id:
            UUID of the target entity.
        max_hops:
            Maximum hops to traverse.  Clamped against the configured ceiling
            ``TAPPS_BRAIN_KG_EXPLAIN_MAX_HOPS`` (default 3) — TAP-1933.
            Values above the ceiling are clamped, not rejected.
        agent_id:
            Override the server-level default for this call (STORY-070.7).

        Returns
        -------
        JSON object: ``{ "found": bool, "hops": int|null,
        "path": [{"entity_id", "edge_id", "predicate", ...}],
        "subject_id": str, "object_id": str }``

        The ``path`` list starts at *subject_id* and ends at *object_id*.
        Each intermediate step includes ``edge_id`` and ``predicate``.
        When ``found=false`` the path list is empty.
        """
        try:
            eff_aid = _rpc(agent_id, default=_server_aid)
        except ValueError as exc:
            return json.dumps({"error": "bad_request", "detail": str(exc)})
        project_id = _pid()

        # Suppress unused variable warning — eff_aid kept for consistency
        _ = eff_aid

        if not subject_id or not object_id:
            return json.dumps(
                {"error": "bad_request", "detail": "subject_id and object_id are required."}
            )

        # TAP-2726: validate UUIDs before the DB ::uuid cast.
        for _field, _val in (("subject_id", subject_id), ("object_id", object_id)):
            _uuid_err = _validate_uuid(_val, _field)
            if _uuid_err is not None:
                return json.dumps(_uuid_err)

        cm = kg_service._get_or_create_cm()
        if cm is None:
            return json.dumps(
                {"error": "db_unavailable", "detail": "TAPPS_BRAIN_DATABASE_URL is not set."}
            )

        ceiling = kg_service.explain_max_hops_ceiling()
        result = kg_service.explain_connection(
            cm,
            project_id,
            kg_service._DEFAULT_BRAIN_ID,
            subject_id=subject_id,
            object_id=object_id,
            max_hops=max(1, min(int(max_hops), ceiling)),
        )
        return json.dumps(result, default=str)

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_record_feedback(  # noqa: PLR0911  # dual-path tool: guard-clause early returns
        feedback_type: str,
        edge_id: str = "",
        entry_key: str = "",
        session_id: str = "",
        utility_score: float = 0.0,
        details_json: str = "",
        agent_id: str = "",
    ) -> str:
        """Record feedback for a KG edge or a private memory entry.

        Accepts **both** edge-level and memory-level feedback in one schema —
        the routing is determined by which subject identifier is provided:

        * **Edge feedback** (``edge_id`` set): routes through
          :class:`~tapps_brain.feedback.FeedbackStore` and also applies
          counter / confidence updates directly to the KG edge row.
          Supported ``feedback_type`` values: ``"edge_helpful"``,
          ``"edge_misleading"``.
        * **Memory feedback** (``entry_key`` set, no ``edge_id``): routes
          through ``MemoryStore.record_feedback()`` as a generic
          ``FeedbackEvent``.  Any Object-Action snake_case ``feedback_type``
          is accepted (e.g. ``"recall_rated"``, ``"gap_reported"``).

        Parameters
        ----------
        feedback_type:
            Event type.  For edges: ``"edge_helpful"`` or
            ``"edge_misleading"``.  For memory: any Object-Action snake_case
            name accepted by :class:`~tapps_brain.feedback.FeedbackStore`.
        edge_id:
            UUID of the KG edge being rated (edge feedback path).
        entry_key:
            Memory entry key (memory feedback path).  Ignored when
            ``edge_id`` is set.
        session_id:
            Optional session identifier for correlation.
        utility_score:
            Numeric utility signal ``[-1, 1]``.  Stored alongside the event
            on both the memory feedback path and (TAP-1930) the edge feedback
            path — recorded in the audit trail on both ``edge_helpful`` and
            ``edge_misleading``.  On ``edge_misleading``, ``abs(utility_score)``
            additionally weights the confidence delta (max 0.1 at
            ``|utility_score| = 1.0``).  On ``edge_helpful`` the SQL path is
            counter-based and ignores the delta — the score still lands in the
            audit row.  Default ``0.0`` means "no useful signal" (legacy
            fixed-step behaviour applies).
        details_json:
            JSON-serialised ``dict`` of extra metadata (memory path only).
        agent_id:
            Override the server-level default for this call (STORY-070.7).

        Returns
        -------
        JSON object: ``{ "recorded": true, "feedback_type": str,
        "edge_id": str|null, "entry_key": str|null }`` on success, or
        ``{ "error": str, "detail": str }`` on validation failure.
        """
        try:
            eff_aid = _rpc(agent_id, default=_server_aid)
        except ValueError as exc:
            return json.dumps({"error": "bad_request", "detail": str(exc)})
        s = _resolve(agent_id)
        project_id = _pid()

        # TAP-1969: validate details_json once up-front and surface the same
        # bad_json envelope used by TAP-1967 / TAP-1968.  The service-layer
        # parser (`parse_details_json`) returns a `parse_error` shape that
        # other feedback tools depend on — we translate only on this MCP
        # surface to keep the contract symmetric with brain_record_event /
        # brain_get_neighbors without rippling through feedback_service.
        details_err = _validate_optional_json_object(details_json, "details_json")
        if details_err is not None:
            return json.dumps(details_err)

        # TAP-1930: pass utility_score through verbatim on both paths so MCP
        # and REST behave identically.  The service layer treats explicit 0.0
        # as "no useful signal" (fixed-step delta) and only weights when
        # |score| > 0 — see kg_service.record_kg_feedback.
        us = float(utility_score)

        # TAP-2726: validate edge_id UUID before the DB ::uuid cast.
        if edge_id:
            _edge_uuid_err = _validate_uuid(edge_id, "edge_id")
            if _edge_uuid_err is not None:
                return json.dumps(_edge_uuid_err)

        # Edge feedback path
        if edge_id:
            raw = kg_service.record_kg_feedback(
                s,
                project_id,
                eff_aid,
                edge_id=edge_id,
                feedback_type=feedback_type,
                session_id=session_id or "",
                utility_score=us,
            )
            if isinstance(raw, dict) and raw.get("error"):
                return json.dumps(raw, default=str)
            result: dict[str, Any] = {
                "recorded": True,
                "feedback_type": feedback_type,
                "edge_id": edge_id,
                "entry_key": None,
            }
            # TAP-1975: surface post-update edge state at the response top
            # level so callers (e.g. tapps-mcp edge_feedback) don't have to
            # drill into `kg_update` to decide whether to retry a path.  The
            # nested `kg_update` dict is preserved for back-compat — existing
            # consumers already read confidence / counters from it.
            if isinstance(raw, dict):
                kg_upd = raw.get("kg_update")
                if isinstance(kg_upd, dict):
                    result["kg_update"] = kg_upd
                    if kg_upd.get("applied"):
                        if "confidence" in kg_upd:
                            result["confidence"] = float(kg_upd["confidence"])
                        if "positive_feedback_count" in kg_upd:
                            result["helpful_count"] = int(float(kg_upd["positive_feedback_count"]))
                        if "negative_feedback_count" in kg_upd:
                            result["misleading_count"] = int(
                                float(kg_upd["negative_feedback_count"])
                            )
                        if "flagged_for_review" in kg_upd:
                            result["flagged_for_review"] = bool(kg_upd["flagged_for_review"])
            return json.dumps(result, default=str)

        # Memory feedback path
        from tapps_brain.services import feedback_service

        mem_raw = feedback_service.feedback_record(
            s,
            project_id,
            eff_aid,
            event_type=feedback_type,
            entry_key=entry_key or "",
            session_id=session_id or "",
            utility_score=us,
            details_json=details_json or "",
        )
        if isinstance(mem_raw, dict) and mem_raw.get("error"):
            return json.dumps(mem_raw, default=str)
        return json.dumps(
            {
                "recorded": True,
                "feedback_type": feedback_type,
                "edge_id": None,
                "entry_key": entry_key or None,
            },
            default=str,
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_resolve_entity(
        entity_type: str,
        canonical_name: str,
        agent_id: str = "",
    ) -> str:
        """Resolve an entity name to its UUID, creating the entity if absent.

        Turns a human-readable ``(entity_type, canonical_name)`` pair into the
        UUID required by ``brain_record_event`` edges.  Internally tries an exact
        canonical-name match and then an alias match before falling back to an
        idempotent ``INSERT … ON CONFLICT`` create.  Two calls with the same
        inputs always return the same UUID.

        Parameters
        ----------
        entity_type:
            Entity type label, e.g. ``"module"``, ``"agent"``, ``"tool"``.
        canonical_name:
            Human-readable name to resolve or create.
        agent_id:
            Override the server-level default for this call (STORY-070.7).

        Returns
        -------
        JSON object: ``{ "entity_id": str, "entity_type": str,
        "canonical_name": str, "created": bool, "confidence": float,
        "reason": str }``
        """
        try:
            eff_aid = _rpc(agent_id, default=_server_aid)
        except ValueError as exc:
            return json.dumps({"error": "bad_request", "detail": str(exc)})
        project_id = _pid()
        _ = eff_aid  # reserved for future per-agent entity scoping

        if not entity_type or not entity_type.strip():
            return json.dumps({"error": "bad_request", "detail": "entity_type is required."})
        if not canonical_name or not canonical_name.strip():
            return json.dumps({"error": "bad_request", "detail": "canonical_name is required."})

        cm = kg_service._get_or_create_cm()
        if cm is None:
            return json.dumps(
                {"error": "db_unavailable", "detail": "TAPPS_BRAIN_DATABASE_URL is not set."}
            )

        result = kg_service.resolve_entity(
            cm,
            project_id,
            kg_service._DEFAULT_BRAIN_ID,
            entity_type=entity_type,
            canonical_name=canonical_name,
        )
        return json.dumps(result, default=str)
