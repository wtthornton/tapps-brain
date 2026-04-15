"""Postgres integration tests for FeedbackStore — record / query / strict-mode.

STORY-066.13: Replaces deleted SQLite-coupled test_feedback and test_store_feedback
test files with Postgres-backed equivalents.

Requires: ``TAPPS_BRAIN_DATABASE_URL`` environment variable (skipped otherwise).
Mark: ``requires_postgres``
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest

pytestmark = pytest.mark.requires_postgres

_PG_DSN = os.environ.get("TAPPS_BRAIN_DATABASE_URL", "")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _apply_migrations() -> None:
    from tapps_brain.postgres_migrations import apply_private_migrations

    apply_private_migrations(_PG_DSN)


def _make_cm() -> Any:
    from tapps_brain.postgres_connection import PostgresConnectionManager

    return PostgresConnectionManager(_PG_DSN)


def _unique_project() -> str:
    return f"test-proj-{uuid.uuid4().hex[:8]}"


def _unique_agent() -> str:
    return f"test-agent-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _migrate() -> None:
    _apply_migrations()


@pytest.fixture
def feedback_store(request: Any) -> Any:
    """FeedbackStore scoped to unique (project_id, agent_id) per test.

    ``FeedbackStore.close()`` is a no-op (the cm is owned by the caller),
    so we close the connection manager directly to release the pool —
    required to avoid pool exhaustion in later tests (TAP-362).
    """
    from tapps_brain.feedback import FeedbackStore

    cm = _make_cm()
    store = FeedbackStore(cm, project_id=_unique_project(), agent_id=_unique_agent())
    try:
        yield store
    finally:
        store.close()
        cm.close()


# ---------------------------------------------------------------------------
# Record / query round-trip
# ---------------------------------------------------------------------------


class TestRecordQuery:
    def test_record_and_query_single_event(self, feedback_store: Any) -> None:
        from tapps_brain.feedback import FeedbackEvent

        ev = FeedbackEvent(event_type="recall_rated", entry_key="key-a", utility_score=0.9)
        feedback_store.record(ev)

        results = feedback_store.query(event_type="recall_rated")
        assert len(results) == 1
        assert results[0].event_type == "recall_rated"
        assert results[0].entry_key == "key-a"
        assert results[0].utility_score is not None
        assert abs(results[0].utility_score - 0.9) < 1e-4

    def test_record_multiple_events_all_returned(self, feedback_store: Any) -> None:
        from tapps_brain.feedback import FeedbackEvent

        events = [FeedbackEvent(event_type="recall_rated", entry_key=f"k-{i}") for i in range(3)]
        for ev in events:
            feedback_store.record(ev)

        results = feedback_store.query()
        assert len(results) == 3

    def test_query_filters_by_event_type(self, feedback_store: Any) -> None:
        from tapps_brain.feedback import FeedbackEvent

        feedback_store.record(FeedbackEvent(event_type="recall_rated", entry_key="k1"))
        feedback_store.record(FeedbackEvent(event_type="gap_reported", entry_key="k2"))

        rated = feedback_store.query(event_type="recall_rated")
        gaps = feedback_store.query(event_type="gap_reported")

        assert all(e.event_type == "recall_rated" for e in rated)
        assert all(e.event_type == "gap_reported" for e in gaps)
        assert len(rated) == 1
        assert len(gaps) == 1

    def test_query_filters_by_entry_key(self, feedback_store: Any) -> None:
        from tapps_brain.feedback import FeedbackEvent

        feedback_store.record(FeedbackEvent(event_type="recall_rated", entry_key="target-key"))
        feedback_store.record(FeedbackEvent(event_type="recall_rated", entry_key="other-key"))

        results = feedback_store.query(entry_key="target-key")
        assert len(results) == 1
        assert results[0].entry_key == "target-key"

    def test_query_filters_by_session_id(self, feedback_store: Any) -> None:
        from tapps_brain.feedback import FeedbackEvent

        feedback_store.record(FeedbackEvent(event_type="recall_rated", session_id="session-abc"))
        feedback_store.record(FeedbackEvent(event_type="recall_rated", session_id="session-xyz"))

        results = feedback_store.query(session_id="session-abc")
        assert len(results) == 1
        assert results[0].session_id == "session-abc"

    def test_idempotent_on_duplicate_id(self, feedback_store: Any) -> None:
        """Recording the same event ID twice must not duplicate the row."""
        from tapps_brain.feedback import FeedbackEvent

        ev = FeedbackEvent(event_type="recall_rated", entry_key="idem-key")
        feedback_store.record(ev)
        feedback_store.record(ev)  # same id

        results = feedback_store.query(event_type="recall_rated")
        assert len(results) == 1

    def test_details_round_trip(self, feedback_store: Any) -> None:
        from tapps_brain.feedback import FeedbackEvent

        ev = FeedbackEvent(
            event_type="issue_flagged",
            details={"reason": "stale_fact", "confidence_drop": 0.2},
        )
        feedback_store.record(ev)
        results = feedback_store.query(event_type="issue_flagged")
        assert len(results) == 1
        assert results[0].details["reason"] == "stale_fact"

    def test_query_empty_when_no_events(self, feedback_store: Any) -> None:
        assert feedback_store.query() == []


# ---------------------------------------------------------------------------
# Strict-mode rejection
# ---------------------------------------------------------------------------


class TestStrictMode:
    def test_strict_mode_rejects_unknown_event_type(self) -> None:
        from tapps_brain.feedback import FeedbackConfig, FeedbackEvent, FeedbackStore

        cm = _make_cm()
        config = FeedbackConfig(strict_event_types=True)
        store = FeedbackStore(
            cm,
            project_id=_unique_project(),
            agent_id=_unique_agent(),
            config=config,
        )
        ev = FeedbackEvent(event_type="custom_unknown_type")
        try:
            with pytest.raises(ValueError, match="strict_event_types"):
                store.record(ev)
        finally:
            store.close()
            cm.close()

    def test_strict_mode_accepts_builtin_event_type(self) -> None:
        from tapps_brain.feedback import FeedbackConfig, FeedbackEvent, FeedbackStore

        cm = _make_cm()
        config = FeedbackConfig(strict_event_types=True)
        store = FeedbackStore(
            cm,
            project_id=_unique_project(),
            agent_id=_unique_agent(),
            config=config,
        )
        ev = FeedbackEvent(event_type="recall_rated", utility_score=0.5)
        try:
            store.record(ev)  # must not raise
            results = store.query(event_type="recall_rated")
            assert len(results) == 1
        finally:
            store.close()
            cm.close()

    def test_strict_mode_accepts_custom_registered_type(self) -> None:
        from tapps_brain.feedback import FeedbackConfig, FeedbackEvent, FeedbackStore

        cm = _make_cm()
        config = FeedbackConfig(strict_event_types=True, custom_event_types=["custom_signal"])
        store = FeedbackStore(
            cm,
            project_id=_unique_project(),
            agent_id=_unique_agent(),
            config=config,
        )
        ev = FeedbackEvent(event_type="custom_signal")
        try:
            store.record(ev)  # must not raise
        finally:
            store.close()
            cm.close()
