"""Feedback service functions (EPIC-070 STORY-070.1).

Exposes memory feedback recording and querying via MCP feedback tools and the
HTTP adapter. Delegates to ``MemoryStore.rate_recall()``, ``report_gap()``,
``report_issue()``, ``record_feedback()``, and ``query_feedback()``.
"""

from __future__ import annotations

from typing import Any

from tapps_brain.services._common import parse_details_json


def feedback_rate(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    entry_key: str,
    rating: str = "helpful",
    session_id: str = "",
    details_json: str = "",
) -> dict[str, Any]:
    """Record a helpfulness rating (e.g. helpful/unhelpful) for a recalled memory entry."""
    details, err = parse_details_json(details_json)
    if err is not None:
        return {"error": "parse_error", "message": err}
    try:
        event = store.rate_recall(
            entry_key,
            rating=rating,
            session_id=session_id.strip() or None,
            details=details if details else None,
        )
    except ValueError as exc:
        return {"error": "validation_error", "message": str(exc)}
    return {"status": "recorded", "event": event.model_dump(mode="json")}


def feedback_gap(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    query: str,
    session_id: str = "",
    details_json: str = "",
) -> dict[str, Any]:
    """Report a knowledge gap: a query that returned no useful results."""
    details, err = parse_details_json(details_json)
    if err is not None:
        return {"error": "parse_error", "message": err}
    event = store.report_gap(
        query,
        session_id=session_id.strip() or None,
        details=details if details else None,
    )
    return {"status": "recorded", "event": event.model_dump(mode="json")}


def feedback_issue(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    entry_key: str,
    issue: str,
    session_id: str = "",
    details_json: str = "",
) -> dict[str, Any]:
    """Report a quality issue (e.g. stale or incorrect content) against a specific entry."""
    details, err = parse_details_json(details_json)
    if err is not None:
        return {"error": "parse_error", "message": err}
    event = store.report_issue(
        entry_key,
        issue,
        session_id=session_id.strip() or None,
        details=details if details else None,
    )
    return {"status": "recorded", "event": event.model_dump(mode="json")}


def feedback_record(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    event_type: str,
    entry_key: str = "",
    session_id: str = "",
    utility_score: float | None = None,
    details_json: str = "",
) -> dict[str, Any]:
    """Record an arbitrary feedback event with an optional utility score and details blob."""
    details, err = parse_details_json(details_json)
    if err is not None:
        return {"error": "parse_error", "message": err}
    try:
        event = store.record_feedback(
            event_type,
            entry_key=entry_key.strip() or None,
            session_id=session_id.strip() or None,
            utility_score=utility_score,
            details=details if details else None,
        )
    except ValueError as exc:
        return {"error": "validation_error", "message": str(exc)}
    return {"status": "recorded", "event": event.model_dump(mode="json")}


def feedback_query(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    event_type: str = "",
    entry_key: str = "",
    session_id: str = "",
    since: str = "",
    until: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    """Query stored feedback events with optional filters on type, key, session, and date range."""
    events = store.query_feedback(
        event_type=event_type.strip() or None,
        entry_key=entry_key.strip() or None,
        session_id=session_id.strip() or None,
        since=since.strip() or None,
        until=until.strip() or None,
        limit=limit,
    )
    return {
        "events": [e.model_dump(mode="json") for e in events],
        "count": len(events),
    }
