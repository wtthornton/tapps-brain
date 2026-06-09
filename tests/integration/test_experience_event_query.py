"""Integration tests — TAP-3157/3158: experience event query round-trip.

Verifies ``record_event`` → ``query_events`` payload survival for
``quality_metric`` events filtered by file path.

Requires: ``TAPPS_BRAIN_DATABASE_URL`` (``@pytest.mark.requires_postgres``).
"""

from __future__ import annotations

import os
import uuid

import pytest

from tapps_brain.services import kg_service

pytestmark = pytest.mark.requires_postgres

_PG_DSN = os.environ.get("TAPPS_BRAIN_DATABASE_URL", "")


@pytest.fixture(scope="module", autouse=True)
def _allow_privileged_dev_role() -> None:
    """Dev compose DSN uses the ``tapps`` owner role — same override as CI."""
    prev = os.environ.get("TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE")
    os.environ["TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE"] = "1"
    yield
    if prev is None:
        os.environ.pop("TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE", None)
    else:
        os.environ["TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE"] = prev


_BRAIN_ID = "tapps-brain"


def _apply_migrations() -> None:
    from tapps_brain.postgres_migrations import apply_private_migrations

    apply_private_migrations(_PG_DSN)


def _make_cm() -> object:
    from tapps_brain.postgres_connection import PostgresConnectionManager

    return PostgresConnectionManager(_PG_DSN, min_size=1, max_size=3)


@pytest.fixture(scope="module", autouse=True)
def _migrations() -> None:
    _apply_migrations()


@pytest.fixture()
def scope() -> tuple[object, str, str]:
    cm = _make_cm()
    project_id = f"test-proj-{uuid.uuid4().hex[:8]}"
    agent_id = f"test-agent-{uuid.uuid4().hex[:8]}"
    return cm, project_id, agent_id


def test_quality_metric_round_trip_by_file_path(scope: tuple[object, str, str]) -> None:
    cm, project_id, agent_id = scope
    file_path = "packages/tapps-mcp/src/tapps_mcp/checklist.py"
    payload = {
        "score": 92.5,
        "duration_ms": 418,
        "gate_passed": True,
        "started_at": "2026-06-09T12:00:00Z",
        "file_path": file_path,
    }

    record = kg_service.record_event(
        cm,
        project_id,
        _BRAIN_ID,
        agent_id,
        event_type="quality_metric",
        payload=payload,
        entities=[{"type": "file", "id": file_path}],
    )
    assert "event_id" in record

    out = kg_service.query_events(
        cm,
        project_id,
        event_type="quality_metric",
        entity_id=file_path,
    )
    assert out["count"] >= 1
    match = next(e for e in out["events"] if e["event_id"] == record["event_id"])
    assert match["payload"]["score"] == payload["score"]
    assert match["payload"]["duration_ms"] == payload["duration_ms"]
    assert match["payload"]["gate_passed"] is True
    assert match["payload"]["started_at"] == payload["started_at"]
    assert match["payload"]["file_path"] == file_path
    assert match["agent_id"] == agent_id


def test_query_events_rejects_missing_event_type(scope: tuple[object, str, str]) -> None:
    cm, project_id, _agent_id = scope
    out = kg_service.query_events(cm, project_id, event_type="")
    assert out.get("error") == "bad_request"
