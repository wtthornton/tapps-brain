"""Unit tests for MemoryStore explicit feedback API (STORY-029.2).

Covers:
- rate_recall(): utility_score mapping for all valid ratings
- rate_recall(): raises ValueError for unknown rating
- report_gap(): gap_reported event with query in details
- report_issue(): issue_flagged event with issue in details
- record_feedback(): generic custom event type
- query_feedback(): filtering wrapper
- Audit log emission via persistence audit_path
- Metrics incremented for each feedback call
- Lazy FeedbackStore initialisation (only created on first use)
- FeedbackConfig from profile is respected (strict_event_types)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Generator

import pytest

from tapps_brain.feedback import FeedbackConfig, FeedbackEvent
from tapps_brain.store import MemoryStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path: Path) -> Generator[MemoryStore, None, None]:
    """MemoryStore backed by a temp directory."""
    s = MemoryStore(tmp_path)
    yield s
    s.close()


# ---------------------------------------------------------------------------
# rate_recall
# ---------------------------------------------------------------------------


class TestRateRecall:
    """Tests for store.rate_recall()."""

    @pytest.mark.parametrize(
        ("rating", "expected_score"),
        [
            ("helpful", 1.0),
            ("partial", 0.5),
            ("irrelevant", 0.0),
            ("outdated", 0.0),
        ],
    )
    def test_rating_maps_to_utility_score(
        self, store: MemoryStore, rating: str, expected_score: float
    ) -> None:
        store.save("k1", "some memory value")
        ev = store.rate_recall("k1", rating=rating)
        assert isinstance(ev, FeedbackEvent)
        assert ev.event_type == "recall_rated"
        assert ev.entry_key == "k1"
        assert ev.utility_score == expected_score
        assert ev.details["rating"] == rating

    def test_default_rating_is_helpful(self, store: MemoryStore) -> None:
        store.save("k1", "value")
        ev = store.rate_recall("k1")
        assert ev.utility_score == 1.0
        assert ev.details["rating"] == "helpful"

    def test_session_id_propagated(self, store: MemoryStore) -> None:
        store.save("k1", "value")
        ev = store.rate_recall("k1", session_id="sess-abc")
        assert ev.session_id == "sess-abc"

    def test_extra_details_merged(self, store: MemoryStore) -> None:
        store.save("k1", "value")
        ev = store.rate_recall("k1", details={"context": "test run"})
        assert ev.details["context"] == "test run"
        assert ev.details["rating"] == "helpful"

    def test_unknown_rating_raises(self, store: MemoryStore) -> None:
        with pytest.raises(ValueError, match="Unknown rating"):
            store.rate_recall("k1", rating="superb")

    def test_persisted_and_queryable(self, store: MemoryStore) -> None:
        store.save("k1", "value")
        store.rate_recall("k1", rating="partial")
        events = store.query_feedback(event_type="recall_rated")
        assert len(events) == 1
        assert events[0].utility_score == 0.5


# ---------------------------------------------------------------------------
# report_gap
# ---------------------------------------------------------------------------


class TestReportGap:
    """Tests for store.report_gap()."""

    def test_gap_event_created(self, store: MemoryStore) -> None:
        ev = store.report_gap("how to configure the timeout")
        assert ev.event_type == "gap_reported"
        assert ev.details["query"] == "how to configure the timeout"
        assert ev.entry_key is None

    def test_session_id_propagated(self, store: MemoryStore) -> None:
        ev = store.report_gap("query text", session_id="s1")
        assert ev.session_id == "s1"

    def test_extra_details_merged(self, store: MemoryStore) -> None:
        ev = store.report_gap("q", details={"source": "user"})
        assert ev.details["query"] == "q"
        assert ev.details["source"] == "user"

    def test_persisted_and_queryable(self, store: MemoryStore) -> None:
        store.report_gap("missing knowledge")
        events = store.query_feedback(event_type="gap_reported")
        assert len(events) == 1
        assert events[0].details["query"] == "missing knowledge"


# ---------------------------------------------------------------------------
# report_issue
# ---------------------------------------------------------------------------


class TestReportIssue:
    """Tests for store.report_issue()."""

    def test_issue_event_created(self, store: MemoryStore) -> None:
        store.save("k1", "value")
        ev = store.report_issue("k1", "outdated information")
        assert ev.event_type == "issue_flagged"
        assert ev.entry_key == "k1"
        assert ev.details["issue"] == "outdated information"

    def test_session_id_propagated(self, store: MemoryStore) -> None:
        store.save("k1", "value")
        ev = store.report_issue("k1", "stale", session_id="sess-x")
        assert ev.session_id == "sess-x"

    def test_extra_details_merged(self, store: MemoryStore) -> None:
        store.save("k1", "value")
        ev = store.report_issue("k1", "wrong", details={"severity": "high"})
        assert ev.details["issue"] == "wrong"
        assert ev.details["severity"] == "high"

    def test_persisted_and_queryable(self, store: MemoryStore) -> None:
        store.save("k1", "value")
        store.report_issue("k1", "broken link")
        events = store.query_feedback(entry_key="k1")
        assert len(events) == 1
        assert events[0].event_type == "issue_flagged"


# ---------------------------------------------------------------------------
# record_feedback (generic)
# ---------------------------------------------------------------------------


class TestRecordFeedback:
    """Tests for store.record_feedback()."""

    def test_builtin_event_type(self, store: MemoryStore) -> None:
        ev = store.record_feedback("implicit_positive", entry_key="k1", utility_score=1.0)
        assert ev.event_type == "implicit_positive"
        assert ev.entry_key == "k1"
        assert ev.utility_score == 1.0

    def test_custom_event_type(self, store: MemoryStore) -> None:
        ev = store.record_feedback("deploy_completed", details={"env": "prod"})
        assert ev.event_type == "deploy_completed"
        assert ev.details["env"] == "prod"

    def test_invalid_event_type_raises(self, store: MemoryStore) -> None:
        """event_type must match Object-Action snake_case pattern."""
        with pytest.raises(ValueError):
            store.record_feedback("BAD_EVENT")

    def test_all_optional_fields_default(self, store: MemoryStore) -> None:
        ev = store.record_feedback("gap_reported")
        assert ev.entry_key is None
        assert ev.session_id is None
        assert ev.utility_score is None
        assert ev.details == {}

    def test_persisted_and_queryable(self, store: MemoryStore) -> None:
        store.record_feedback("implicit_negative", utility_score=-0.1)
        events = store.query_feedback(event_type="implicit_negative")
        assert len(events) == 1
        assert events[0].utility_score == pytest.approx(-0.1)


# ---------------------------------------------------------------------------
# query_feedback
# ---------------------------------------------------------------------------


class TestQueryFeedback:
    """Tests for store.query_feedback()."""

    def test_no_filters_returns_all(self, store: MemoryStore) -> None:
        store.rate_recall("k1", rating="helpful")
        store.report_gap("topic A")
        events = store.query_feedback()
        assert len(events) == 2

    def test_filter_by_event_type(self, store: MemoryStore) -> None:
        store.rate_recall("k1", rating="helpful")
        store.report_gap("topic A")
        recall_events = store.query_feedback(event_type="recall_rated")
        assert len(recall_events) == 1
        assert recall_events[0].event_type == "recall_rated"

    def test_filter_by_entry_key(self, store: MemoryStore) -> None:
        store.save("k1", "v1")
        store.save("k2", "v2")
        store.rate_recall("k1", rating="partial")
        store.rate_recall("k2", rating="irrelevant")
        events = store.query_feedback(entry_key="k1")
        assert len(events) == 1
        assert events[0].entry_key == "k1"

    def test_filter_by_session_id(self, store: MemoryStore) -> None:
        store.rate_recall("k1", rating="helpful", session_id="sess-1")
        store.rate_recall("k1", rating="partial", session_id="sess-2")
        events = store.query_feedback(session_id="sess-1")
        assert len(events) == 1
        assert events[0].session_id == "sess-1"

    def test_limit_applied(self, store: MemoryStore) -> None:
        for i in range(5):
            store.report_gap(f"query {i}")
        events = store.query_feedback(limit=3)
        assert len(events) == 3

    def test_empty_store_returns_empty_list(self, store: MemoryStore) -> None:
        assert store.query_feedback() == []


# ---------------------------------------------------------------------------
# Audit log integration
# ---------------------------------------------------------------------------


class TestAuditLog:
    """Feedback events should be emitted to the audit log."""

    def test_rate_recall_emits_audit_entry(self, tmp_path: Path) -> None:
        s = MemoryStore(tmp_path)
        try:
            s.save("k1", "value")
            s.rate_recall("k1", rating="helpful")
        finally:
            s.close()

        audit_path = tmp_path / ".tapps-brain" / "memory" / "memory_log.jsonl"
        lines = audit_path.read_text().splitlines()
        feedback_lines = [l for l in lines if "feedback_record" in l]
        assert len(feedback_lines) == 1
        record = json.loads(feedback_lines[0])
        assert record["action"] == "feedback_record"
        assert record["event_type"] == "recall_rated"

    def test_report_gap_emits_audit_entry(self, tmp_path: Path) -> None:
        s = MemoryStore(tmp_path)
        try:
            s.report_gap("missing docs")
        finally:
            s.close()

        audit_path = tmp_path / ".tapps-brain" / "memory" / "memory_log.jsonl"
        lines = audit_path.read_text().splitlines()
        feedback_lines = [l for l in lines if "feedback_record" in l]
        assert len(feedback_lines) == 1


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


class TestMetrics:
    """Feedback API should increment metrics counters."""

    def test_rate_recall_increments_metric(self, store: MemoryStore) -> None:
        store.save("k1", "value")
        before = store.get_metrics().counters.get("store.feedback.recall_rated", 0)
        store.rate_recall("k1")
        after = store.get_metrics().counters.get("store.feedback.recall_rated", 0)
        assert after == before + 1

    def test_report_gap_increments_metric(self, store: MemoryStore) -> None:
        before = store.get_metrics().counters.get("store.feedback.gap_reported", 0)
        store.report_gap("q")
        after = store.get_metrics().counters.get("store.feedback.gap_reported", 0)
        assert after == before + 1

    def test_report_issue_increments_metric(self, store: MemoryStore) -> None:
        store.save("k1", "value")
        before = store.get_metrics().counters.get("store.feedback.issue_flagged", 0)
        store.report_issue("k1", "issue")
        after = store.get_metrics().counters.get("store.feedback.issue_flagged", 0)
        assert after == before + 1

    def test_record_feedback_increments_metric(self, store: MemoryStore) -> None:
        before = store.get_metrics().counters.get("store.feedback.recorded", 0)
        store.record_feedback("gap_reported")
        after = store.get_metrics().counters.get("store.feedback.recorded", 0)
        assert after == before + 1


# ---------------------------------------------------------------------------
# Lazy initialization
# ---------------------------------------------------------------------------


class TestLazyInit:
    """FeedbackStore should be created lazily on first use."""

    def test_feedback_store_not_initialized_on_construction(self, tmp_path: Path) -> None:
        s = MemoryStore(tmp_path)
        try:
            assert s._feedback_store_instance is None
        finally:
            s.close()

    def test_feedback_store_initialized_on_first_call(self, store: MemoryStore) -> None:
        assert store._feedback_store_instance is None
        store.report_gap("test")
        assert store._feedback_store_instance is not None

    def test_feedback_store_reused_on_subsequent_calls(self, store: MemoryStore) -> None:
        store.report_gap("first")
        instance_1 = store._feedback_store_instance
        store.report_gap("second")
        instance_2 = store._feedback_store_instance
        assert instance_1 is instance_2


# ---------------------------------------------------------------------------
# FeedbackConfig from profile
# ---------------------------------------------------------------------------


class TestFeedbackConfigFromProfile:
    """When a profile with FeedbackConfig is provided, it should be used."""

    def test_strict_event_types_from_profile(self, tmp_path: Path) -> None:
        """StrictEventTypes from profile should be respected."""
        from unittest.mock import MagicMock

        mock_profile = MagicMock()
        mock_profile.feedback = FeedbackConfig(strict_event_types=True)

        s = MemoryStore(tmp_path, profile=mock_profile)
        try:
            # Built-in type is always fine
            s.report_gap("q")
            # Unknown custom type should be rejected in strict mode
            with pytest.raises(ValueError, match="strict_event_types"):
                s.record_feedback("unknown_custom_type")
        finally:
            s.close()

    def test_custom_event_types_from_profile(self, tmp_path: Path) -> None:
        """Custom event types registered in profile should be accepted."""
        from unittest.mock import MagicMock

        mock_profile = MagicMock()
        mock_profile.feedback = FeedbackConfig(
            custom_event_types=["deploy_completed"],
            strict_event_types=True,
        )

        s = MemoryStore(tmp_path, profile=mock_profile)
        try:
            ev = s.record_feedback("deploy_completed")
            assert ev.event_type == "deploy_completed"
        finally:
            s.close()


# ---------------------------------------------------------------------------
# Hive feedback propagation (STORY-029.7)
# ---------------------------------------------------------------------------


class TestHiveFeedbackPropagation:
    def test_propagate_after_hive_recall_session_index(self, tmp_path: Path) -> None:
        from tapps_brain.hive import HiveStore

        hive_db = tmp_path / "hive.db"
        hs = HiveStore(db_path=hive_db)
        try:
            hs.save(
                key="hive-only-key",
                value="unique propagation marker qqww1122",
                namespace="universal",
                source_agent="tester",
                conflict_policy="last_write_wins",
            )
            store = MemoryStore(tmp_path, hive_store=hs, hive_agent_id="tester")
            try:
                res = store.recall("qqww1122", session_id="sess-hive-fb")
                assert any(
                    m.get("key") == "hive-only-key" and m.get("source") == "hive"
                    for m in res.memories
                )
                store.rate_recall("hive-only-key", session_id="sess-hive-fb", rating="helpful")
                rows = hs.query_feedback_events(namespace="universal", limit=20)
                assert any(
                    r["entry_key"] == "hive-only-key" and r["event_type"] == "recall_rated"
                    for r in rows
                )
            finally:
                store.close()
        finally:
            hs.close()

    def test_propagate_via_details_hive_namespace(self, tmp_path: Path) -> None:
        from tapps_brain.hive import HiveStore

        hs = HiveStore(db_path=tmp_path / "h.db")
        try:
            store = MemoryStore(tmp_path, hive_store=hs)
            try:
                store.save("local-k", "v", tier="pattern")
                store.rate_recall("local-k", details={"hive_namespace": "universal"})
                rows = hs.query_feedback_events(entry_key="local-k", limit=5)
                assert len(rows) == 1
            finally:
                store.close()
        finally:
            hs.close()

    def test_no_propagate_without_namespace(self, tmp_path: Path) -> None:
        from tapps_brain.hive import HiveStore

        hs = HiveStore(db_path=tmp_path / "h2.db")
        try:
            store = MemoryStore(tmp_path, hive_store=hs)
            try:
                store.save("lk", "v", tier="pattern")
                store.rate_recall("lk")
                assert hs.query_feedback_events(limit=10) == []
            finally:
                store.close()
        finally:
            hs.close()
