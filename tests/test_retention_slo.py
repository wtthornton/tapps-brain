"""Retention SLO pytest nodes (TAP-6698, KB-3.6) — skip-free (see tests/_pg_fixture.py).

Five assertions, each mirrored by ``/healthz?deep=1``'s ``retention_ok`` field
(``tapps_brain.services.retention_slo``, ``tapps_brain.http.probe_cache._probe_retention_slos``):

1. No ``status='active'`` row is older than 2x its tier half-life.
2. The newest ``experience_events`` partition is >= 3 months ahead of ``now()``.
3. ``experience_events_default`` holds 0 rows.
4. No ``flywheel_meta`` feedback cursor is > 48h behind ``feedback_events``.
5. A configured retention window with no active manager is a violation.

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


@pytest.fixture(scope="session")
def retention_fixture_dsn() -> str:
    return resolve_fixture_dsn()


@pytest.fixture()
def conn(retention_fixture_dsn: str):
    with psycopg.connect(retention_fixture_dsn) as c:
        yield c


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
        assert result == {"ok": True, "violations": []}


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
