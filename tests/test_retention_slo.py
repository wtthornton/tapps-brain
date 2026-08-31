"""Retention SLO pytest nodes (TAP-6698, KB-3.6) — skip-free (see tests/_pg_fixture.py).

Five assertions, each mirrored by ``/healthz?deep=1``'s ``retention_ok`` field
(``tapps_brain.services.retention_slo``, ``tapps_brain.http.probe_cache._probe_retention_slos``):

1. No ``status='active'`` row is older than 2x its tier half-life.
2. The newest ``experience_events`` partition is >= 3 months ahead of ``now()``.
3. ``experience_events_default`` holds 0 rows.
4. No ``flywheel_meta`` feedback cursor is > 48h behind ``feedback_events``.
5. A configured retention window with no active manager is a violation —
   conditional by design, so it reports ``applicable: False`` when no window is
   configured rather than an indistinguishable pass (VAL-09 defect 4).

Each enumerating check reports a **true total** alongside its bounded sample:
``violating_total`` counts the whole filtered population while ``violations``
stays capped at ``_MAX_SAMPLE``. SLO 1 additionally LEFT-joins the tier
half-life table so a row carrying a tier nothing defines is surfaced with
``reason='unrecognised_tier'`` instead of being dropped by an inner join
(VAL-09 defects 1 and 2).

Method lesson from the lane's own corrections log: an assertion that reports
"0 violations" is worthless unless the same probe is shown catching a real
one first. SLO 1's and SLO 4's tests both do — manufacture a violating row,
assert the probe flags it, then assert the surrounding fixture is otherwise
clean.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from tapps_brain.services import partition_manager, retention_slo
from tapps_brain.services.maintenance_heartbeat import record_heartbeat
from tests._pg_fixture import resolve_fixture_dsn

_PROJECT_ID = "tap-6698-slo-fixture"
_AGENT_ID = "slo-fixture-agent"
#: ``project_profiles.profile`` is JSONB; only ``project_id`` is read by the scan.
_PROFILE_JSON = '{"name": "repo-brain"}'


@pytest.fixture(scope="session")
def retention_fixture_dsn() -> str:
    return resolve_fixture_dsn()


@pytest.fixture()
def conn(retention_fixture_dsn: str):
    with psycopg.connect(retention_fixture_dsn) as c:
        yield c


@pytest.fixture(scope="module", autouse=True)
def _register_fixture_project(retention_fixture_dsn: str):
    """Make ``_PROJECT_ID`` discoverable by SLO 1's per-tenant scan (TAP-6698).

    Since Ruling 15, ``check_no_overdue_active_rows`` enumerates tenants from
    ``project_profiles`` and the non-RLS tenanted tables and scans each one under
    ``SET LOCAL app.project_id`` — ``private_memories`` has fail-closed RLS with
    no admin bypass, so there is no "all tenants" query to run instead.  These
    tests write rows with raw SQL, which leaves no audit trail, so without a
    registry row the fixture tenant would be invisible to the very check under
    test.  Registering it keeps these assertions end-to-end (they exercise
    discovery too) rather than pinning them to an explicit tenant list.
    """
    with psycopg.connect(retention_fixture_dsn) as c:
        with c.cursor() as cur:
            cur.execute(
                "INSERT INTO project_profiles (project_id, profile) "
                "VALUES (%s, %s::jsonb) ON CONFLICT (project_id) DO NOTHING",
                (_PROJECT_ID, _PROFILE_JSON),
            )
        c.commit()
    yield
    with psycopg.connect(retention_fixture_dsn) as c:
        with c.cursor() as cur:
            cur.execute("DELETE FROM project_profiles WHERE project_id = %s", (_PROJECT_ID,))
        c.commit()


def _insert_memory(conn, *, key: str, tier: str, updated_at: datetime | None = None) -> None:
    with conn.cursor() as cur:
        if updated_at is None:
            cur.execute(
                "INSERT INTO private_memories (project_id, agent_id, key, value, tier) "
                "VALUES (%s, %s, %s, 'v', %s)",
                (_PROJECT_ID, _AGENT_ID, key, tier),
            )
        else:
            cur.execute(
                "INSERT INTO private_memories "
                "(project_id, agent_id, key, value, tier, updated_at) "
                "VALUES (%s, %s, %s, 'v', %s, %s)",
                (_PROJECT_ID, _AGENT_ID, key, tier, updated_at),
            )
    conn.commit()


def _delete_memory(conn, *, key: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM private_memories WHERE project_id = %s AND agent_id = %s AND key = %s",
            (_PROJECT_ID, _AGENT_ID, key),
        )
    conn.commit()


class TestSLO1NoOverdueActiveRows:
    def test_detects_a_manufactured_violation(self, conn) -> None:
        """Prove the probe finds a real violation before any test trusts a 0-count."""
        key = f"slo1-violation-{uuid.uuid4().hex[:8]}"
        # context tier: 14-day default half-life: 2x = 28 days. 40 days is well past.
        old_ts = datetime.now(UTC) - timedelta(days=40)
        _insert_memory(conn, key=key, tier="context", updated_at=old_ts)
        try:
            result = retention_slo.check_no_overdue_active_rows(conn)
            assert result["ok"] is False
            assert any(v["key"] == key for v in result["violations"])
        finally:
            _delete_memory(conn, key=key)

    def test_passes_on_a_fresh_row(self, conn) -> None:
        key = f"slo1-fresh-{uuid.uuid4().hex[:8]}"
        _insert_memory(conn, key=key, tier="context")
        try:
            result = retention_slo.check_no_overdue_active_rows(conn)
            assert not any(v["key"] == key for v in result["violations"])
        finally:
            _delete_memory(conn, key=key)


class TestSLO1SurfacesUnrecognisedTiers:
    """VAL-09: the check must not be blind to the rows most likely malformed.

    ``private_memories.tier`` is free text and also carries EPIC-010 profile
    layer names, so a row can hold a tier the half-life table does not list.
    The check used to ``JOIN`` that table, which dropped those rows before the
    age predicate ever saw them — the deployed brain holds ten of them
    (``identity`` / ``long-term`` / ``short-term``, written 2026-08-07) and
    SLO 1 reported ``ok`` at every age.

    The give-away is that age cannot be the trigger: these tests insert a
    *brand-new* row, which no age-based assertion could ever flag, and require
    it to be reported anyway.
    """

    def test_a_fresh_row_with_an_unrecognised_tier_is_reported(self, conn) -> None:
        key = f"slo1-unknown-tier-{uuid.uuid4().hex[:8]}"
        _insert_memory(conn, key=key, tier="identity")
        try:
            result = retention_slo.check_no_overdue_active_rows(conn)
            assert result["ok"] is False
            hit = next((v for v in result["violations"] if v["key"] == key), None)
            assert hit is not None, "an unrecognised tier was dropped, not surfaced"
            assert hit["reason"] == "unrecognised_tier"
            assert hit["tier"] == "identity"
            assert hit["half_life_days"] is None
            assert result["unrecognised_tier_total"] >= 1
        finally:
            _delete_memory(conn, key=key)

    def test_an_overdue_row_is_still_reported_as_overdue(self, conn) -> None:
        """Correct-negative: widening the join must not relabel real overdue rows."""
        key = f"slo1-overdue-reason-{uuid.uuid4().hex[:8]}"
        old_ts = datetime.now(UTC) - timedelta(days=40)
        _insert_memory(conn, key=key, tier="context", updated_at=old_ts)
        try:
            result = retention_slo.check_no_overdue_active_rows(conn)
            hit = next(v for v in result["violations"] if v["key"] == key)
            assert hit["reason"] == "overdue"
            assert hit["half_life_days"] == 14.0
        finally:
            _delete_memory(conn, key=key)

    def test_a_fresh_row_with_a_known_tier_is_still_not_a_violation(self, conn) -> None:
        """The LEFT JOIN must not turn every fresh row into a violation."""
        key = f"slo1-fresh-known-{uuid.uuid4().hex[:8]}"
        _insert_memory(conn, key=key, tier="architectural")
        try:
            result = retention_slo.check_no_overdue_active_rows(conn)
            assert not any(v["key"] == key for v in result["violations"])
        finally:
            _delete_memory(conn, key=key)


class TestSLO1ReportsTheTrueViolationCount:
    """VAL-09 defect 2: the sample cap must not become the reported number.

    ``_MAX_SAMPLE`` bounds the enumerated rows, which is right — a health probe
    must not stream thousands of rows.  Reporting *only* that bounded list is
    what made a live breach of 5,603 rows read as 20.  ``ok`` was already sound
    (the LIMIT applies to the already-filtered set, so zero matches still means
    zero); the count was not.
    """

    def test_violating_total_exceeds_the_sample_cap(self, conn) -> None:
        overshoot = retention_slo._MAX_SAMPLE + 5
        baseline = retention_slo.check_no_overdue_active_rows(conn)["violating_total"]
        old_ts = datetime.now(UTC) - timedelta(days=40)
        prefix = f"slo1-bulk-{uuid.uuid4().hex[:8]}"
        keys = [f"{prefix}-{i}" for i in range(overshoot)]
        for key in keys:
            _insert_memory(conn, key=key, tier="context", updated_at=old_ts)
        try:
            result = retention_slo.check_no_overdue_active_rows(conn)
            assert result["violating_total"] == baseline + overshoot
            assert len(result["violations"]) == retention_slo._MAX_SAMPLE
            assert result["sample_truncated"] is True
            assert result["ok"] is False
        finally:
            for key in keys:
                _delete_memory(conn, key=key)

    def test_no_truncation_flag_when_the_sample_is_complete(self, conn) -> None:
        key = f"slo1-single-{uuid.uuid4().hex[:8]}"
        old_ts = datetime.now(UTC) - timedelta(days=40)
        _insert_memory(conn, key=key, tier="context", updated_at=old_ts)
        try:
            result = retention_slo.check_no_overdue_active_rows(conn)
            assert result["violating_total"] == len(result["violations"])
            assert result["sample_truncated"] is False
        finally:
            _delete_memory(conn, key=key)

    def test_a_clean_population_reports_zero_not_an_empty_sample(self, conn) -> None:
        result = retention_slo.check_no_overdue_active_rows(conn)
        assert result["violating_total"] == 0
        assert result["ok"] is True
        assert result["sample_truncated"] is False


class TestSLO4ReportsTheTrueViolationCount:
    """Same cap, same fix, on the flywheel-lag check (``:121`` before the fix)."""

    def test_violating_total_exceeds_the_sample_cap(self, conn) -> None:
        overshoot = retention_slo._MAX_SAMPLE + 5
        baseline = retention_slo.check_flywheel_lag(conn)["violating_total"]
        prefix = f"slo4-bulk-{uuid.uuid4().hex[:8]}"
        newest = datetime.now(UTC)
        with conn.cursor() as cur:
            for i in range(overshoot):
                cur.execute(
                    "INSERT INTO feedback_events "
                    "(project_id, agent_id, id, event_type, timestamp) "
                    "VALUES (%s, %s, %s, 'implicit_positive', %s)",
                    (f"{prefix}-{i}", "slo4-bulk-agent", str(uuid.uuid4()), newest),
                )
        conn.commit()
        try:
            result = retention_slo.check_flywheel_lag(conn)
            assert result["violating_total"] == baseline + overshoot
            assert result["missing_cursor_total"] >= overshoot
            assert len(result["violations"]) == retention_slo._MAX_SAMPLE
            assert result["sample_truncated"] is True
            assert all(v["reason"] == "no_cursor_row" for v in result["violations"])
        finally:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM feedback_events WHERE project_id LIKE %s",
                    (f"{prefix}-%",),
                )
            conn.commit()


class TestSLO2PartitionHorizon:
    def test_horizon_holds_on_the_migrated_fixture(self, conn) -> None:
        """Migration 020 pre-creates partitions well past now()+3 months already."""
        result = retention_slo.check_partition_horizon(conn)
        assert result["ok"] is True, result


class TestSLO3DefaultPartitionEmpty:
    def test_default_partition_is_empty_on_a_fresh_fixture(self, conn) -> None:
        result = retention_slo.check_default_partition_empty(conn)
        assert result == {"ok": True, "row_count": 0}


class TestSLO4FlywheelLag:
    def test_detects_a_manufactured_stale_cursor(self, conn) -> None:
        project_id = f"tap-6698-flywheel-{uuid.uuid4().hex[:8]}"
        agent_id = "flywheel-fixture-agent"
        newest_feedback = datetime.now(UTC)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO feedback_events "
                "(project_id, agent_id, id, event_type, timestamp) "
                "VALUES (%s, %s, %s, 'implicit_positive', %s)",
                (project_id, agent_id, str(uuid.uuid4()), newest_feedback),
            )
        conn.commit()
        try:
            # No flywheel_meta cursor row at all == maximally stale.
            result = retention_slo.check_flywheel_lag(conn)
            assert result["ok"] is False
            assert any(
                v["project_id"] == project_id and v["agent_id"] == agent_id
                for v in result["violations"]
            )
        finally:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM feedback_events WHERE project_id = %s AND agent_id = %s",
                    (project_id, agent_id),
                )
            conn.commit()

    def test_passes_on_a_fresh_fixture(self, conn) -> None:
        result = retention_slo.check_flywheel_lag(conn)
        assert result == {
            "ok": True,
            "violating_total": 0,
            "missing_cursor_total": 0,
            "sample_truncated": False,
            "violations": [],
        }


class TestSLO5RetentionManagerActive:
    @pytest.fixture(autouse=True)
    def _no_prior_heartbeat(self, conn) -> None:
        """A heartbeat from an earlier test/run must not leak into this class.

        The heartbeat sentinel row is process-global (not scoped per test), so
        without this, an earlier ``record_heartbeat`` call — in this class or
        a prior pytest invocation against the same fixture container within
        the 2h freshness window — makes "no heartbeat" unreproducible.
        """
        from tapps_brain.services.maintenance_heartbeat import (
            HEARTBEAT_AGENT_ID,
            HEARTBEAT_EVENT_TYPE,
            HEARTBEAT_PROJECT_ID,
        )

        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM audit_log WHERE project_id = %s AND agent_id = %s AND event_type = %s",
                (HEARTBEAT_PROJECT_ID, HEARTBEAT_AGENT_ID, HEARTBEAT_EVENT_TYPE),
            )
        conn.commit()

    def test_ok_when_retention_env_unset(self, conn) -> None:
        result = retention_slo.check_retention_manager_active(conn, retention_env="")
        assert result["ok"] is True
        assert result["retention_env_set"] is False

    def test_unset_retention_reports_itself_as_not_applicable(self, conn) -> None:
        """VAL-09 defect 4: an SLO that cannot fail must not look like a pass.

        ``TAPPS_BRAIN_EVENTS_RETENTION_MONTHS`` is unset on the deployed
        ``tapps-brain-http``, so this check short-circuits without touching the
        DB and only four of the five SLOs can move ``retention_ok``. That is
        correct by design — no retention window configured means no retention
        promise to break — but it was reported as an ordinary pass, which is
        what made the asymmetry invisible. No failure mode is added here: the
        verdict is still ``ok``.
        """
        result = retention_slo.check_retention_manager_active(conn, retention_env="")
        assert result["ok"] is True
        assert result["applicable"] is False
        assert "TAPPS_BRAIN_EVENTS_RETENTION_MONTHS" in result["reason"]

    def test_a_configured_retention_window_is_applicable(self, conn) -> None:
        """Correct-negative: with a window set, the check really does run."""
        result = retention_slo.check_retention_manager_active(conn, retention_env="12")
        assert result["applicable"] is True

    def test_violation_when_env_set_and_no_heartbeat(self, conn) -> None:
        result = retention_slo.check_retention_manager_active(conn, retention_env="12")
        assert result["ok"] is False
        assert result["retention_env_set"] is True

    def test_ok_when_env_set_and_heartbeat_present(self, conn) -> None:
        record_heartbeat(conn, details={"trigger": "test"})
        conn.commit()
        result = retention_slo.check_retention_manager_active(conn, retention_env="12")
        assert result["ok"] is True
        assert result["manager_active"] is True


class TestEvaluateRetentionSlos:
    def test_aggregate_is_ok_on_a_clean_fixture(self, conn) -> None:
        result = retention_slo.evaluate_retention_slos(conn, retention_env="")
        assert result["retention_ok"] is True, result["checks"]

    def test_aggregate_names_the_checks_that_passed_vacuously(self, conn) -> None:
        result = retention_slo.evaluate_retention_slos(conn, retention_env="")
        assert result["not_applicable"] == ["retention_manager_active"]

    def test_nothing_is_inapplicable_once_retention_is_configured(self, conn) -> None:
        record_heartbeat(conn, details={"trigger": "test"})
        conn.commit()
        result = retention_slo.evaluate_retention_slos(conn, retention_env="12")
        assert result["not_applicable"] == []


class TestVal07PartitionDropFixtureProof:
    """VAL-07: on a fixture DB, drop a partition older than the retention window."""

    def test_pre_create_is_a_noop_when_horizon_already_covers_the_window(self, conn) -> None:
        """Correct-negative shape: nothing to create on the pre-migrated fixture."""
        result = partition_manager.pre_create_partitions(conn, months_ahead=3, dry_run=True)
        assert result["would_create"] == []

    def test_drop_removes_a_manufactured_old_partition(self, conn) -> None:
        from psycopg import sql

        old_month_start = datetime(2020, 1, 1).date()
        old_month_end = datetime(2020, 2, 1).date()
        partition_name = "experience_events_y2020m01"
        with conn.cursor() as cur:
            # FOR VALUES FROM/TO requires constants, not bind params — sql.Literal
            # inlines a safely-quoted one (same fix as partition_manager.py).
            cur.execute(
                sql.SQL(
                    "CREATE TABLE IF NOT EXISTS {name} PARTITION OF experience_events "
                    "FOR VALUES FROM ({start}) TO ({end})"
                ).format(
                    name=sql.Identifier(partition_name),
                    start=sql.Literal(old_month_start),
                    end=sql.Literal(old_month_end),
                )
            )
        conn.commit()
        try:
            before = {p["name"] for p in partition_manager.list_monthly_partitions(conn)}
            assert partition_name in before

            dry = partition_manager.drop_old_partitions(conn, retention_months=12, dry_run=True)
            assert partition_name in dry["would_drop"]

            applied = partition_manager.drop_old_partitions(
                conn, retention_months=12, dry_run=False
            )
            assert partition_name in applied["dropped"]

            after = {p["name"] for p in partition_manager.list_monthly_partitions(conn)}
            assert partition_name not in after
            assert retention_slo.check_default_partition_empty(conn) == {
                "ok": True,
                "row_count": 0,
            }
        finally:
            with conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS {partition_name}")
            conn.commit()
