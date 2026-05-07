"""Knowledge-Graph MCP tool registrations (EPIC-076 STORY-076.5).

Exposes four tools on the standard server:

* ``brain_record_event``  — write one :class:`~tapps_brain.experience.ExperienceEvent`
  plus optional memory / entity / edge / evidence atomically.
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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tapps_brain.mcp_server.context import ToolContext

from tapps_brain.services import kg_service


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
        payload_json: str = "",
        entities_json: str = "",
        edges_json: str = "",
        evidence_json: str = "",
        memory_key: str = "",
        memory_value: str = "",
        memory_tier: str = "pattern",
        session_id: str = "",
        workflow_run_id: str = "",
        agent_id: str = "",
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
        payload_json:
            JSON-serialised ``dict`` of arbitrary event metadata.  Omit or
            pass ``"{}"`` for an empty payload.
        entities_json:
            JSON-serialised ``list[dict]`` matching
            :class:`~tapps_brain.experience.EntitySpec`.  Pass ``""`` to skip.
        edges_json:
            JSON-serialised ``list[dict]`` matching
            :class:`~tapps_brain.experience.EdgeSpec`.  Both
            ``subject_entity_id`` and ``object_entity_id`` must be
            pre-resolved entity UUIDs.  Pass ``""`` to skip.
        evidence_json:
            JSON-serialised ``list[dict]`` matching
            :class:`~tapps_brain.experience.EvidenceSpec`.  Pass ``""`` to
            skip.
        memory_key / memory_value:
            When both are provided, a private memory is written atomically
            alongside the event.
        memory_tier:
            Tier for the optional private memory (``pattern`` by default).
        session_id / workflow_run_id:
            Optional grouping identifiers for correlation.
        agent_id:
            Override the server-level default for this call (STORY-070.7).

        Returns
        -------
        JSON object: ``{ "event_id": str, "memory_key": str|null,
        "entity_ids": [str], "edge_ids": [str], "evidence_ids": [str] }``
        """
        eff_aid = _rpc(agent_id, default=_server_aid)
        project_id = _pid()

        cm = kg_service._get_or_create_cm()
        if cm is None:
            return json.dumps(
                {"error": "db_unavailable", "detail": "TAPPS_BRAIN_DATABASE_URL is not set."}
            )

        # Parse payload_json — silently fall back to empty dict on decode error.
        payload: dict[str, Any] = {}
        if payload_json and payload_json.strip() not in ("", "{}"):
            try:
                parsed = json.loads(payload_json)
                if isinstance(parsed, dict):
                    payload = parsed
            except json.JSONDecodeError:
                pass

        result = kg_service.record_event(
            cm,
            project_id,
            kg_service._DEFAULT_BRAIN_ID,
            eff_aid,
            event_type=event_type,
            subject_key=subject_key or None,
            utility_score=float(utility_score),
            payload=payload,
            entities_json=entities_json,
            edges_json=edges_json,
            evidence_json=evidence_json,
            memory_key=memory_key or None,
            memory_value=memory_value or None,
            memory_tier=memory_tier or "pattern",
            session_id=session_id or None,
            workflow_run_id=workflow_run_id or None,
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
        eff_aid = _rpc(agent_id, default=_server_aid)
        project_id = _pid()

        # Suppress unused variable warning — eff_aid kept for consistency
        _ = eff_aid

        cm = kg_service._get_or_create_cm()
        if cm is None:
            return json.dumps(
                {"error": "db_unavailable", "detail": "TAPPS_BRAIN_DATABASE_URL is not set."}
            )

        entity_ids: list[str] = []
        if entity_ids_json and entity_ids_json.strip():
            try:
                parsed = json.loads(entity_ids_json)
                if isinstance(parsed, list):
                    entity_ids = [str(e) for e in parsed if e]
            except json.JSONDecodeError:
                pass

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
            Maximum hops to traverse (clamped to [1, 3]).  Default 3.
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
        eff_aid = _rpc(agent_id, default=_server_aid)
        project_id = _pid()

        # Suppress unused variable warning — eff_aid kept for consistency
        _ = eff_aid

        if not subject_id or not object_id:
            return json.dumps(
                {"error": "bad_request", "detail": "subject_id and object_id are required."}
            )

        cm = kg_service._get_or_create_cm()
        if cm is None:
            return json.dumps(
                {"error": "db_unavailable", "detail": "TAPPS_BRAIN_DATABASE_URL is not set."}
            )

        result = kg_service.explain_connection(
            cm,
            project_id,
            kg_service._DEFAULT_BRAIN_ID,
            subject_id=subject_id,
            object_id=object_id,
            max_hops=max(1, min(int(max_hops), 3)),
        )
        return json.dumps(result, default=str)

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_record_feedback(
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
            Numeric utility signal ``[-1, 1]`` stored alongside the event
            (memory feedback path only; ignored for edge feedback).
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
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        project_id = _pid()

        # Edge feedback path
        if edge_id:
            raw = kg_service.record_kg_feedback(
                s,
                project_id,
                eff_aid,
                edge_id=edge_id,
                feedback_type=feedback_type,
                session_id=session_id or "",
            )
            if isinstance(raw, dict) and raw.get("error"):
                return json.dumps(raw, default=str)
            result: dict[str, Any] = {
                "recorded": True,
                "feedback_type": feedback_type,
                "edge_id": edge_id,
                "entry_key": None,
            }
            if isinstance(raw, dict):
                kg_upd = raw.get("kg_update")
                if isinstance(kg_upd, dict):
                    result["kg_update"] = kg_upd
            return json.dumps(result, default=str)

        # Memory feedback path
        from tapps_brain.services import feedback_service

        score: float | None = float(utility_score) if utility_score else None
        mem_raw = feedback_service.feedback_record(
            s,
            project_id,
            eff_aid,
            event_type=feedback_type,
            entry_key=entry_key or "",
            session_id=session_id or "",
            utility_score=score,
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
