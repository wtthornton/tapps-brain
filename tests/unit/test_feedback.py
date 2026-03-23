"""Unit tests for FeedbackEvent model and FeedbackStore (STORY-029.1b).

Covers:
- Model validation including open enum behavior
- Round-trip: record then query
- Query filtering (event_type, entry_key, session_id, since, until, limit)
- Thread-safety smoke test
- Audit log emission
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from pydantic import ValidationError

from tapps_brain.feedback import BUILTIN_EVENT_TYPES, FeedbackConfig, FeedbackEvent, FeedbackStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "memory.db"


@pytest.fixture()
def audit_path(tmp_path: Path) -> Path:
    return tmp_path / "memory_log.jsonl"


@pytest.fixture()
def store(db_path: Path, audit_path: Path) -> FeedbackStore:
    s = FeedbackStore(db_path=db_path, audit_path=audit_path)
    yield s
    s.close()


# ---------------------------------------------------------------------------
# FeedbackEvent model validation
# ---------------------------------------------------------------------------


class TestFeedbackEventModel:
    """Validate FeedbackEvent field constraints and open enum behavior."""

    def test_minimal_valid_event(self) -> None:
        ev = FeedbackEvent(event_type="recall_rated")
        assert ev.event_type == "recall_rated"
        assert ev.id  # auto-generated UUID
        assert ev.entry_key is None
        assert ev.utility_score is None
        assert ev.details == {}
        assert ev.timestamp  # auto-generated

    def test_full_event(self) -> None:
        ev = FeedbackEvent(
            event_type="gap_reported",
            entry_key="key123",
            session_id="sess-abc",
            utility_score=0.5,
            details={"reason": "missing context"},
        )
        assert ev.event_type == "gap_reported"
        assert ev.entry_key == "key123"
        assert ev.session_id == "sess-abc"
        assert ev.utility_score == 0.5
        assert ev.details == {"reason": "missing context"}

    # -- Standard built-in event types

    @pytest.mark.parametrize(
        "event_type",
        [
            "recall_rated",
            "gap_reported",
            "issue_flagged",
            "implicit_positive",
            "implicit_negative",
            "implicit_correction",
        ],
    )
    def test_standard_event_types_accepted(self, event_type: str) -> None:
        ev = FeedbackEvent(event_type=event_type)
        assert ev.event_type == event_type

    # -- Open enum: custom event types accepted

    def test_custom_event_type_accepted(self) -> None:
        """Any Object-Action snake_case name is accepted (open enum)."""
        ev = FeedbackEvent(event_type="project_archived")
        assert ev.event_type == "project_archived"

    def test_multi_segment_custom_event_type(self) -> None:
        ev = FeedbackEvent(event_type="user_preference_updated")
        assert ev.event_type == "user_preference_updated"

    # -- Invalid event types rejected

    @pytest.mark.parametrize(
        "bad_type",
        [
            "single",       # no underscore
            "CamelCase",    # uppercase
            "_leading",     # starts with underscore
            "trailing_",    # ends with underscore
            "has space",    # space
            "has-dash",     # dash
            "",             # empty
            "1starts_digit",  # starts with digit
        ],
    )
    def test_invalid_event_type_rejected(self, bad_type: str) -> None:
        with pytest.raises(ValidationError):
            FeedbackEvent(event_type=bad_type)

    # -- utility_score bounds

    def test_utility_score_min(self) -> None:
        ev = FeedbackEvent(event_type="recall_rated", utility_score=-1.0)
        assert ev.utility_score == -1.0

    def test_utility_score_max(self) -> None:
        ev = FeedbackEvent(event_type="recall_rated", utility_score=1.0)
        assert ev.utility_score == 1.0

    def test_utility_score_out_of_range(self) -> None:
        with pytest.raises(ValidationError):
            FeedbackEvent(event_type="recall_rated", utility_score=1.1)

    def test_utility_score_below_range(self) -> None:
        with pytest.raises(ValidationError):
            FeedbackEvent(event_type="recall_rated", utility_score=-1.1)

    # -- details is dict

    def test_details_nested(self) -> None:
        ev = FeedbackEvent(event_type="recall_rated", details={"nested": {"key": 42}})
        assert ev.details["nested"]["key"] == 42


# ---------------------------------------------------------------------------
# FeedbackStore — record / query round-trip
# ---------------------------------------------------------------------------


class TestFeedbackStoreRoundTrip:
    """record() stores an event; query() retrieves it faithfully."""

    def test_record_and_retrieve(self, store: FeedbackStore) -> None:
        ev = FeedbackEvent(
            event_type="recall_rated",
            entry_key="my_entry",
            session_id="sess-1",
            utility_score=0.8,
            details={"comment": "useful"},
        )
        store.record(ev)

        results = store.query()
        assert len(results) == 1
        got = results[0]
        assert got.id == ev.id
        assert got.event_type == "recall_rated"
        assert got.entry_key == "my_entry"
        assert got.session_id == "sess-1"
        assert got.utility_score == pytest.approx(0.8)
        assert got.details == {"comment": "useful"}
        assert got.timestamp == ev.timestamp

    def test_record_idempotent_on_duplicate_id(self, store: FeedbackStore) -> None:
        """INSERT OR IGNORE means a duplicate id is silently ignored."""
        ev = FeedbackEvent(event_type="recall_rated")
        store.record(ev)
        store.record(ev)  # second insert ignored
        assert len(store.query()) == 1

    def test_record_null_optional_fields(self, store: FeedbackStore) -> None:
        ev = FeedbackEvent(event_type="gap_reported")
        store.record(ev)
        results = store.query()
        assert len(results) == 1
        got = results[0]
        assert got.entry_key is None
        assert got.session_id is None
        assert got.utility_score is None

    def test_multiple_events_ordered_by_timestamp(self, store: FeedbackStore) -> None:
        ev1 = FeedbackEvent(event_type="recall_rated", timestamp="2026-01-01T00:00:00+00:00")
        ev2 = FeedbackEvent(event_type="gap_reported", timestamp="2026-01-02T00:00:00+00:00")
        ev3 = FeedbackEvent(event_type="issue_flagged", timestamp="2026-01-03T00:00:00+00:00")
        # Insert out of order to verify ordering
        store.record(ev3)
        store.record(ev1)
        store.record(ev2)

        results = store.query()
        assert [r.event_type for r in results] == [
            "recall_rated",
            "gap_reported",
            "issue_flagged",
        ]

    def test_details_round_trip_complex(self, store: FeedbackStore) -> None:
        ev = FeedbackEvent(
            event_type="recall_rated",
            details={"list": [1, 2, 3], "nested": {"key": True}, "score": 0.99},
        )
        store.record(ev)
        got = store.query()[0]
        assert got.details == {"list": [1, 2, 3], "nested": {"key": True}, "score": 0.99}


# ---------------------------------------------------------------------------
# FeedbackStore — query filtering
# ---------------------------------------------------------------------------


class TestFeedbackStoreQueryFiltering:
    """Each filter parameter narrows results correctly."""

    @pytest.fixture(autouse=True)
    def _populate(self, store: FeedbackStore) -> None:
        """Insert a set of events covering all filter dimensions."""
        events = [
            FeedbackEvent(
                event_type="recall_rated",
                entry_key="entry_a",
                session_id="sess-1",
                utility_score=1.0,
                timestamp="2026-01-01T10:00:00+00:00",
            ),
            FeedbackEvent(
                event_type="recall_rated",
                entry_key="entry_b",
                session_id="sess-2",
                utility_score=0.0,
                timestamp="2026-01-02T10:00:00+00:00",
            ),
            FeedbackEvent(
                event_type="gap_reported",
                entry_key="entry_a",
                session_id="sess-1",
                timestamp="2026-01-03T10:00:00+00:00",
            ),
            FeedbackEvent(
                event_type="issue_flagged",
                entry_key="entry_c",
                session_id="sess-3",
                timestamp="2026-01-04T10:00:00+00:00",
            ),
            FeedbackEvent(
                event_type="implicit_positive",
                entry_key="entry_a",
                session_id="sess-2",
                timestamp="2026-01-05T10:00:00+00:00",
            ),
        ]
        for ev in events:
            store.record(ev)
        self.store = store

    def test_filter_event_type(self) -> None:
        results = self.store.query(event_type="recall_rated")
        assert len(results) == 2
        assert all(r.event_type == "recall_rated" for r in results)

    def test_filter_event_type_no_match(self) -> None:
        results = self.store.query(event_type="implicit_correction")
        assert results == []

    def test_filter_entry_key(self) -> None:
        results = self.store.query(entry_key="entry_a")
        assert len(results) == 3

    def test_filter_entry_key_and_event_type(self) -> None:
        results = self.store.query(event_type="recall_rated", entry_key="entry_a")
        assert len(results) == 1
        assert results[0].entry_key == "entry_a"
        assert results[0].event_type == "recall_rated"

    def test_filter_session_id(self) -> None:
        results = self.store.query(session_id="sess-1")
        assert len(results) == 2

    def test_filter_since(self) -> None:
        results = self.store.query(since="2026-01-03T00:00:00+00:00")
        assert len(results) == 3

    def test_filter_until(self) -> None:
        results = self.store.query(until="2026-01-02T23:59:59+00:00")
        assert len(results) == 2

    def test_filter_since_and_until(self) -> None:
        results = self.store.query(
            since="2026-01-02T00:00:00+00:00",
            until="2026-01-04T23:59:59+00:00",
        )
        assert len(results) == 3

    def test_filter_limit(self) -> None:
        results = self.store.query(limit=2)
        assert len(results) == 2

    def test_filter_limit_zero(self) -> None:
        """limit=0 should return nothing (LIMIT 0 in SQL)."""
        results = self.store.query(limit=0)
        assert results == []

    def test_no_filter_returns_all(self) -> None:
        results = self.store.query()
        assert len(results) == 5

    def test_filter_all_params_combined(self) -> None:
        results = self.store.query(
            event_type="recall_rated",
            entry_key="entry_b",
            session_id="sess-2",
            since="2026-01-01T00:00:00+00:00",
            until="2026-01-03T00:00:00+00:00",
            limit=10,
        )
        assert len(results) == 1
        assert results[0].entry_key == "entry_b"


# ---------------------------------------------------------------------------
# FeedbackStore — audit log emission
# ---------------------------------------------------------------------------


class TestFeedbackStoreAudit:
    def test_audit_written_on_record(self, store: FeedbackStore, audit_path: Path) -> None:
        ev = FeedbackEvent(event_type="recall_rated", entry_key="k1")
        store.record(ev)

        lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["action"] == "feedback_record"
        assert entry["key"] == ev.id
        assert entry["event_type"] == "recall_rated"
        assert entry["entry_key"] == "k1"
        assert "timestamp" in entry

    def test_audit_written_for_each_event(self, store: FeedbackStore, audit_path: Path) -> None:
        store.record(FeedbackEvent(event_type="recall_rated"))
        store.record(FeedbackEvent(event_type="gap_reported"))
        lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2

    def test_no_audit_without_audit_path(self, db_path: Path) -> None:
        """Store without audit_path must not raise."""
        s = FeedbackStore(db_path=db_path, audit_path=None)
        try:
            s.record(FeedbackEvent(event_type="recall_rated"))
        finally:
            s.close()


# ---------------------------------------------------------------------------
# FeedbackConfig — custom event types and strict validation
# ---------------------------------------------------------------------------


class TestFeedbackConfig:
    """FeedbackConfig validates custom event types and drives strict mode."""

    # -- BUILTIN_EVENT_TYPES constant

    def test_builtin_event_types_non_empty(self) -> None:
        assert len(BUILTIN_EVENT_TYPES) > 0
        assert "recall_rated" in BUILTIN_EVENT_TYPES
        assert "gap_reported" in BUILTIN_EVENT_TYPES
        assert "issue_flagged" in BUILTIN_EVENT_TYPES

    # -- Default config

    def test_default_config(self) -> None:
        cfg = FeedbackConfig()
        assert cfg.custom_event_types == []
        assert cfg.strict_event_types is False

    def test_default_known_types_equals_builtins(self) -> None:
        cfg = FeedbackConfig()
        assert cfg.known_event_types == BUILTIN_EVENT_TYPES

    # -- Custom event type validation

    def test_valid_custom_event_types(self) -> None:
        cfg = FeedbackConfig(custom_event_types=["deploy_completed", "pr_review_requested"])
        assert "deploy_completed" in cfg.custom_event_types
        assert "pr_review_requested" in cfg.custom_event_types

    def test_multi_segment_custom_type(self) -> None:
        cfg = FeedbackConfig(custom_event_types=["user_preference_updated"])
        assert "user_preference_updated" in cfg.custom_event_types

    @pytest.mark.parametrize(
        "bad_name",
        [
            "single",       # no underscore
            "CamelCase",    # uppercase
            "_leading",     # starts with underscore
            "trailing_",    # ends with underscore
            "has space",    # space
            "has-dash",     # dash
            "",             # empty
            "1starts_digit",  # starts with digit
        ],
    )
    def test_invalid_custom_event_type_rejected(self, bad_name: str) -> None:
        with pytest.raises(ValidationError):
            FeedbackConfig(custom_event_types=[bad_name])

    def test_known_event_types_includes_custom(self) -> None:
        cfg = FeedbackConfig(custom_event_types=["deploy_completed"])
        assert "deploy_completed" in cfg.known_event_types
        # Built-ins are still present
        assert "recall_rated" in cfg.known_event_types

    def test_known_event_types_is_frozenset(self) -> None:
        cfg = FeedbackConfig(custom_event_types=["deploy_completed"])
        assert isinstance(cfg.known_event_types, frozenset)

    # -- Strict mode in FeedbackStore

    def test_strict_mode_rejects_unknown_event_type(self, db_path: Path) -> None:
        cfg = FeedbackConfig(strict_event_types=True)
        store = FeedbackStore(db_path=db_path, config=cfg)
        try:
            with pytest.raises(ValueError, match="Unknown event_type"):
                store.record(FeedbackEvent(event_type="custom_unknown"))
        finally:
            store.close()

    def test_strict_mode_allows_builtin_types(self, db_path: Path) -> None:
        cfg = FeedbackConfig(strict_event_types=True)
        store = FeedbackStore(db_path=db_path, config=cfg)
        try:
            # All built-in types should pass strict validation
            store.record(FeedbackEvent(event_type="recall_rated"))
            store.record(FeedbackEvent(event_type="gap_reported"))
            assert len(store.query()) == 2
        finally:
            store.close()

    def test_strict_mode_allows_registered_custom_type(self, db_path: Path) -> None:
        cfg = FeedbackConfig(
            custom_event_types=["deploy_completed"],
            strict_event_types=True,
        )
        store = FeedbackStore(db_path=db_path, config=cfg)
        try:
            store.record(FeedbackEvent(event_type="deploy_completed"))
            results = store.query()
            assert len(results) == 1
            assert results[0].event_type == "deploy_completed"
        finally:
            store.close()

    def test_non_strict_mode_allows_any_valid_event_type(self, db_path: Path) -> None:
        """Without strict mode, any Object-Action snake_case type is accepted."""
        cfg = FeedbackConfig(strict_event_types=False)
        store = FeedbackStore(db_path=db_path, config=cfg)
        try:
            store.record(FeedbackEvent(event_type="anything_goes"))
            assert len(store.query()) == 1
        finally:
            store.close()

    def test_default_store_without_config_is_non_strict(self, db_path: Path) -> None:
        """Store with no config defaults to non-strict (open enum behaviour)."""
        store = FeedbackStore(db_path=db_path)
        try:
            store.record(FeedbackEvent(event_type="any_valid_type"))
            assert len(store.query()) == 1
        finally:
            store.close()

    def test_strict_mode_error_lists_known_types(self, db_path: Path) -> None:
        """Error message should include the list of known types."""
        cfg = FeedbackConfig(
            custom_event_types=["my_custom_event"],
            strict_event_types=True,
        )
        store = FeedbackStore(db_path=db_path, config=cfg)
        try:
            with pytest.raises(ValueError) as exc_info:
                store.record(FeedbackEvent(event_type="unknown_type"))
            assert "my_custom_event" in str(exc_info.value)
        finally:
            store.close()


# ---------------------------------------------------------------------------
# FeedbackConfig — profile YAML integration
# ---------------------------------------------------------------------------


class TestFeedbackConfigProfileYaml:
    """FeedbackConfig round-trips through MemoryProfile YAML loading."""

    def test_profile_includes_default_feedback_config(self) -> None:
        """MemoryProfile should have a feedback field with defaults."""
        from tapps_brain.profile import MemoryProfile

        # Load the built-in repo-brain profile (already has all required fields)
        from tapps_brain.profile import get_builtin_profile

        profile = get_builtin_profile("repo-brain")
        assert hasattr(profile, "feedback")
        assert isinstance(profile.feedback, FeedbackConfig)
        assert profile.feedback.custom_event_types == []
        assert profile.feedback.strict_event_types is False

    def test_profile_yaml_with_custom_feedback_section(self, tmp_path: Path) -> None:
        """A profile YAML with feedback.custom_event_types loads correctly."""
        import yaml

        profile_yaml = tmp_path / "test_profile.yaml"
        data = {
            "profile": {
                "name": "test-profile",
                "layers": [
                    {
                        "name": "context",
                        "half_life_days": 14,
                    }
                ],
                "feedback": {
                    "custom_event_types": ["deploy_completed", "pr_merged"],
                    "strict_event_types": True,
                },
            }
        }
        profile_yaml.write_text(yaml.dump(data), encoding="utf-8")

        from tapps_brain.profile import load_profile

        profile = load_profile(profile_yaml)
        assert "deploy_completed" in profile.feedback.custom_event_types
        assert "pr_merged" in profile.feedback.custom_event_types
        assert profile.feedback.strict_event_types is True

    def test_profile_yaml_without_feedback_section_uses_defaults(self, tmp_path: Path) -> None:
        """A profile YAML without a feedback section uses FeedbackConfig defaults."""
        import yaml

        profile_yaml = tmp_path / "test_profile.yaml"
        data = {
            "profile": {
                "name": "test-profile",
                "layers": [{"name": "context", "half_life_days": 14}],
            }
        }
        profile_yaml.write_text(yaml.dump(data), encoding="utf-8")

        from tapps_brain.profile import load_profile

        profile = load_profile(profile_yaml)
        assert profile.feedback.custom_event_types == []
        assert profile.feedback.strict_event_types is False


# ---------------------------------------------------------------------------
# FeedbackStore — thread safety
# ---------------------------------------------------------------------------


class TestFeedbackStoreThreadSafety:
    def test_concurrent_records(self, store: FeedbackStore) -> None:
        """Multiple threads can call record() without errors."""
        errors: list[Exception] = []

        def _worker() -> None:
            try:
                store.record(FeedbackEvent(event_type="recall_rated"))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=_worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"
        # All 20 events should be stored (each has a unique auto-generated id)
        results = store.query(limit=100)
        assert len(results) == 20
