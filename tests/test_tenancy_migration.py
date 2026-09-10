"""Tests for tapps_brain.maintenance.tenancy_migrate (TAP-7279, VAL-08).

Two layers:

- Pure classification-rule unit tests (no DB, fast) — ``TestClassifyRules``.
- Postgres integration tests against the disposable fixture container
  (``tests/_pg_fixture.py`` — never the deployed brain). RLS is enabled
  and FORCED on ``private_memories`` with no admin-bypass policy
  (``migrations/private/012_rls_force.sql``), so these tests connect as the
  fixture's ``postgres`` superuser, which bypasses RLS the same way a real
  ``--dsn`` for this tool must (see the module docstring on
  ``tenancy_migrate``). Skip-free per lane policy — see ``tests/_pg_fixture.py``.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest

from tapps_brain import _postgres_private_sql
from tapps_brain.maintenance import tenancy_migrate as tm
from tests._pg_fixture import resolve_fixture_dsn

# NOTE: no `requires_postgres` mark anywhere in this file. The TestClassify*/
# TestParseJsonbList classes are pure-Python unit tests with no DB dependency.
# The Postgres-integration classes below (TestDryRunPlan, TestDryRunEmptyDbRefusal,
# TestDryRunIsReadOnly, TestApply) follow the same skip-free lane policy as
# tests/test_retention_slo.py and tests/test_maintenance_cycle.py: they call
# resolve_fixture_dsn() directly, which starts a disposable container and fails
# loudly (never skips) when neither a DSN nor docker is available — marking them
# requires_postgres would let conftest.py's default-skip path silently skip them
# instead, which is exactly what lane policy forbids for tenancy coverage.

_T0 = datetime(2026, 6, 1, tzinfo=UTC)


def _ts(offset_days: int) -> datetime:
    return _T0 + timedelta(days=offset_days)


# ---------------------------------------------------------------------------
# Pure classification-rule unit tests — no DB.
# ---------------------------------------------------------------------------


class TestClassifyR1AuditLog:
    def test_fires_when_earliest_event_project_is_approved(self) -> None:
        target = tm.classify_r1_audit_log(
            "k1",
            audit_earliest_project={"k1": "agentforge"},
            approved_projects=frozenset({"agentforge", "nlt-ideas-scout"}),
        )
        assert target == "agentforge"

    def test_does_not_fire_when_earliest_event_project_is_not_approved(self) -> None:
        """Earliest, not any-approved: a later approved event must not override an
        earlier unapproved one (TAP-7279's literal 'earliest ... whose project is
        approved' — see _load_audit_earliest_project for the SQL side)."""
        target = tm.classify_r1_audit_log(
            "k1",
            audit_earliest_project={"k1": "api"},
            approved_projects=frozenset({"agentforge"}),
        )
        assert target is None

    def test_does_not_fire_with_no_audit_history(self) -> None:
        target = tm.classify_r1_audit_log(
            "k1", audit_earliest_project={}, approved_projects=frozenset({"agentforge"})
        )
        assert target is None


class TestClassifyR2SourceAgent:
    def test_fires_when_agent_maps_to_approved_project(self) -> None:
        target = tm.classify_r2_source_agent(
            "forge-worker-7",
            agent_project_map={"forge-worker-7": "agentforge"},
            approved_projects=frozenset({"agentforge"}),
        )
        assert target == "agentforge"

    def test_skipped_when_map_absent(self) -> None:
        target = tm.classify_r2_source_agent(
            "forge-worker-7", agent_project_map=None, approved_projects=frozenset({"agentforge"})
        )
        assert target is None

    def test_does_not_fire_when_mapped_project_not_approved(self) -> None:
        target = tm.classify_r2_source_agent(
            "some-worker",
            agent_project_map={"some-worker": "nlt-orchestrator"},
            approved_projects=frozenset({"agentforge"}),
        )
        assert target is None


class TestClassifyR3IngestWindow:
    def _row(self, **overrides: Any) -> tm.MemoryRow:
        base: dict[str, Any] = {
            "project_id": "nlt-ideas-scout",
            "agent_id": "default",
            "key": "mem-scout-run-abc123",
            "created_at": "2026-06-15T00:00:00+00:00",
            "updated_at": "2026-06-15T00:00:00+00:00",
            "source_agent": "unknown",
        }
        base.update(overrides)
        return tm.MemoryRow(**base)

    def test_fires_inside_window(self) -> None:
        window = ("2026-06-01T00:00:00+00:00", "2026-06-30T00:00:00+00:00")
        assert tm.classify_r3_ingest_window(self._row(), ingest_window=window) is True

    def test_skipped_when_window_absent(self) -> None:
        assert tm.classify_r3_ingest_window(self._row(), ingest_window=None) is False

    def test_does_not_fire_outside_window(self) -> None:
        window = ("2026-01-01T00:00:00+00:00", "2026-01-31T00:00:00+00:00")
        assert tm.classify_r3_ingest_window(self._row(), ingest_window=window) is False

    def test_does_not_fire_for_wrong_project(self) -> None:
        window = ("2026-06-01T00:00:00+00:00", "2026-06-30T00:00:00+00:00")
        row = self._row(project_id="api")
        assert tm.classify_r3_ingest_window(row, ingest_window=window) is False

    def test_does_not_fire_for_non_ingest_key_shape(self) -> None:
        window = ("2026-06-01T00:00:00+00:00", "2026-06-30T00:00:00+00:00")
        row = self._row(key="not-an-ingest-key")
        assert tm.classify_r3_ingest_window(row, ingest_window=window) is False


class TestClassifyR4LegacyUnattributed:
    def test_fires_for_real_project_unattributed_agent(self) -> None:
        assert tm.classify_r4_legacy_unattributed("agentforge", "default") is True

    def test_does_not_fire_for_s3_placeholder_project(self) -> None:
        assert tm.classify_r4_legacy_unattributed("api", "default") is False

    def test_does_not_fire_for_attributed_agent(self) -> None:
        assert tm.classify_r4_legacy_unattributed("agentforge", "some-real-agent") is False


class TestClassifyMemoryRowOrder:
    """R1 beats R2 beats R3 beats R4 beats R5 when more than one could apply."""

    def test_r1_wins_over_r4(self) -> None:
        row = tm.MemoryRow(
            project_id="agentforge",
            agent_id="default",
            key="k1",
            created_at="2026-06-01T00:00:00+00:00",
            updated_at="2026-06-01T00:00:00+00:00",
            source_agent="unknown",
        )
        cls = tm.classify_memory_row(
            row,
            approved_projects=frozenset({"agentforge"}),
            audit_earliest_project={"k1": "agentforge"},
            agent_project_map=None,
            ingest_window=None,
        )
        assert cls.rule == "R1"
        assert cls.action == "re_home"
        assert cls.target_project == "agentforge"
        assert cls.target_agent == "default"  # R1 changes project only, not agent_id

    def test_r5_is_the_fallback(self) -> None:
        row = tm.MemoryRow(
            project_id="default",
            agent_id="agentforge",
            key="exec",
            created_at="2026-06-01T00:00:00+00:00",
            updated_at="2026-06-01T00:00:00+00:00",
            source_agent="agentforge",
        )
        cls = tm.classify_memory_row(
            row,
            approved_projects=frozenset({"agentforge"}),
            audit_earliest_project={},
            agent_project_map=None,
            ingest_window=None,
        )
        assert cls == tm.Classification(rule="R5", action="archive")


class TestClassifyRelationRow:
    def test_r4_for_real_project(self) -> None:
        row = tm.RelationRow(
            project_id="agentforge",
            agent_id="unknown",
            subject="s",
            predicate="p",
            object_entity="o",
            created_at="2026-06-01T00:00:00+00:00",
        )
        cls = tm.classify_relation_row(row)
        assert cls.rule == "R4"
        assert cls.target_project == "agentforge"
        assert cls.target_agent == "legacy-unattributed"

    def test_r5_for_s3_placeholder_project(self) -> None:
        row = tm.RelationRow(
            project_id="api",
            agent_id="someone",
            subject="s",
            predicate="p",
            object_entity="o",
            created_at="2026-06-01T00:00:00+00:00",
        )
        assert tm.classify_relation_row(row) == tm.Classification(rule="R5", action="archive")


class TestParseJsonbList:
    def test_list_passthrough(self) -> None:
        assert tm._parse_jsonb_list(["a", "b"]) == ["a", "b"]

    def test_json_string_fallback(self) -> None:
        assert tm._parse_jsonb_list('["a", "b"]') == ["a", "b"]

    def test_none_and_garbage(self) -> None:
        assert tm._parse_jsonb_list(None) == []
        assert tm._parse_jsonb_list("not json") == []
        assert tm._parse_jsonb_list('"a string, not a list"') == []


# ---------------------------------------------------------------------------
# Postgres integration fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def fixture_dsn() -> str:
    return resolve_fixture_dsn()


@pytest.fixture()
def conn(fixture_dsn: str):
    with psycopg.connect(fixture_dsn) as c:
        yield c


@pytest.fixture(autouse=True)
def _clean_state(fixture_dsn: str):
    """Empty every table this module touches, before and after each test.

    Also (re)creates ``private_relations`` (normally created lazily on first
    use by ``PostgresPrivateBackend._ensure_relations_table``) and drops any
    archive tables a previous test left behind.
    """

    def _reset() -> None:
        with psycopg.connect(fixture_dsn) as c, c.cursor() as cur:
            cur.execute(_postgres_private_sql.RELATIONS_DDL)
            cur.execute("DELETE FROM private_memories")
            cur.execute("DELETE FROM private_relations")
            cur.execute("DELETE FROM project_profiles")
            cur.execute("DELETE FROM audit_log")
            cur.execute(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename LIKE 'tap7279_%'"
            )
            for (tablename,) in cur.fetchall():
                cur.execute(f'DROP TABLE IF EXISTS "{tablename}"')

    _reset()
    yield
    _reset()


def _insert_memory(
    conn: psycopg.Connection,
    *,
    project_id: str,
    agent_id: str,
    key: str,
    source_agent: str = "unknown",
    created_at: datetime,
    updated_at: datetime | None = None,
    value: str = "v",
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO private_memories "
            "(project_id, agent_id, key, value, source_agent, created_at, updated_at, tags) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, '[]'::jsonb)",
            (project_id, agent_id, key, value, source_agent, created_at, updated_at or created_at),
        )
    conn.commit()


def _insert_relation(
    conn: psycopg.Connection,
    *,
    project_id: str,
    agent_id: str,
    subject: str,
    predicate: str,
    object_entity: str,
    created_at: datetime,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO private_relations "
            "(project_id, agent_id, subject, predicate, object_entity, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (project_id, agent_id, subject, predicate, object_entity, created_at),
        )
    conn.commit()


def _insert_project(conn: psycopg.Connection, project_id: str, *, approved: bool) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO project_profiles (project_id, profile, approved, source) "
            "VALUES (%s, '{}'::jsonb, %s, 'admin')",
            (project_id, approved),
        )
    conn.commit()


def _insert_audit_event(
    conn: psycopg.Connection, *, project_id: str, agent_id: str, key: str, timestamp: datetime
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO audit_log (project_id, agent_id, event_type, key, timestamp) "
            "VALUES (%s, %s, 'save', %s, %s)",
            (project_id, agent_id, key, timestamp),
        )
    conn.commit()


def _seed_s3_fixture(conn: psycopg.Connection) -> None:
    """6-row S3 population covering R1-R5, plus one pre-existing row that
    collides with the R4 row's re-home target (an 8th, non-S3, row)."""
    _insert_project(conn, "agentforge", approved=True)
    _insert_project(conn, "nlt-ideas-scout", approved=True)
    _insert_project(conn, "nlt-orchestrator", approved=False)

    # R1: earliest save event for this key names an approved project.
    _insert_memory(
        conn,
        project_id="api",
        agent_id="default",
        key="r1-key",
        source_agent="unknown",
        created_at=_ts(0),
    )
    _insert_audit_event(
        conn, project_id="agentforge", agent_id="ingest-job", key="r1-key", timestamp=_ts(-5)
    )

    # R2: source_agent maps to an approved project via the caller-supplied map.
    _insert_memory(
        conn,
        project_id="api",
        agent_id="default",
        key="r2-key",
        source_agent="forge-worker-7",
        created_at=_ts(1),
    )

    # R3: /ingest key shape for nlt-ideas-scout, inside the caller-supplied window.
    _insert_memory(
        conn,
        project_id="nlt-ideas-scout",
        agent_id="default",
        key="mem-scout-run-abc123",
        source_agent="unknown",
        created_at=_ts(10),
    )

    # R4: real project, unattributed agent, no rule above fires. updated_at is
    # newer than the pre-existing collision row below, so this row wins.
    _insert_memory(
        conn,
        project_id="agentforge",
        agent_id="default",
        key="r4-key",
        source_agent="someone",
        created_at=_ts(2),
        updated_at=_ts(20),
    )

    # R5 (x2): api/main/default/repo-brain junk that no rule re-homes.
    _insert_memory(
        conn,
        project_id="default",
        agent_id="agentforge",
        key="exec",
        source_agent="agentforge",
        created_at=_ts(3),
    )
    _insert_memory(
        conn,
        project_id="repo-brain",
        agent_id="default",
        key="r5b-key",
        source_agent="unknown",
        created_at=_ts(4),
    )

    # Non-S3 pre-existing row already at R4's re-home target — a collision.
    # Older than the R4 row above, so the R4 (source) row wins.
    _insert_memory(
        conn,
        project_id="agentforge",
        agent_id="legacy-unattributed",
        key="r4-key",
        source_agent="n/a",
        created_at=_ts(-30),
        updated_at=_ts(-30),
        value="pre-existing",
    )

    # private_relations: one R4, one R5.
    _insert_relation(
        conn,
        project_id="agentforge",
        agent_id="unknown",
        subject="s1",
        predicate="p1",
        object_entity="o1",
        created_at=_ts(0),
    )
    _insert_relation(
        conn,
        project_id="api",
        agent_id="someone",
        subject="s2",
        predicate="p2",
        object_entity="o2",
        created_at=_ts(0),
    )


_AGENT_PROJECT_MAP = {"forge-worker-7": "agentforge"}
_INGEST_WINDOW = ("2026-06-05T00:00:00+00:00", "2026-06-20T00:00:00+00:00")


def _build_fixture_plan(conn: psycopg.Connection) -> tm.MigrationPlan:
    return tm.build_plan(conn, agent_project_map=_AGENT_PROJECT_MAP, ingest_window=_INGEST_WINDOW)


# ---------------------------------------------------------------------------
# VAL-08: dry-run plan on the fixture names every row with the expected rule.
# ---------------------------------------------------------------------------


class TestDryRunPlan:
    def test_plan_names_every_row_with_expected_rule(self, conn: psycopg.Connection) -> None:
        _seed_s3_fixture(conn)
        plan = _build_fixture_plan(conn)

        memories = plan.tables["private_memories"]
        assert memories.live_count == 6  # the collision partner is NOT part of the S3 population

        by_key: dict[str, tm.PlanGroup] = {}
        for group in memories.groups:
            for row_id in group.row_ids:
                by_key[row_id[0]] = group

        assert by_key["r1-key"].rule == "R1"
        assert by_key["r1-key"].target_project == "agentforge"
        assert by_key["r1-key"].target_agent == "default"

        assert by_key["r2-key"].rule == "R2"
        assert by_key["r2-key"].target_project == "agentforge"

        assert by_key["mem-scout-run-abc123"].rule == "R3"
        assert by_key["mem-scout-run-abc123"].target_project == "nlt-ideas-scout"
        assert by_key["mem-scout-run-abc123"].target_agent == "ingest"

        assert by_key["r4-key"].rule == "R4"
        assert by_key["r4-key"].target_project == "agentforge"
        assert by_key["r4-key"].target_agent == "legacy-unattributed"

        assert by_key["exec"].rule == "R5"
        assert by_key["exec"].action == "archive"

        assert by_key["r5b-key"].rule == "R5"
        assert by_key["r5b-key"].action == "archive"

        # This is the JSON plan (positive control) — must serialize cleanly.
        text = plan.model_dump_json(indent=2)
        assert '"rule": "R1"' in text
        assert '"rule": "R4"' in text

    def test_plan_predicts_the_collision(self, conn: psycopg.Connection) -> None:
        _seed_s3_fixture(conn)
        plan = _build_fixture_plan(conn)
        collisions = plan.tables["private_memories"].collisions
        assert len(collisions) == 1
        assert collisions[0].row_id == ["r4-key"]
        assert collisions[0].winner == "source"

    def test_totals_reconcile_to_live_select_count(self, conn: psycopg.Connection) -> None:
        _seed_s3_fixture(conn)
        plan = _build_fixture_plan(conn)
        live_count = tm.live_predicate_count(conn, "private_memories")
        assert plan.tables["private_memories"].live_count == live_count
        assert live_count == 6

    def test_r2_and_r3_report_skipped_when_inputs_absent(self, conn: psycopg.Connection) -> None:
        _seed_s3_fixture(conn)
        plan = tm.build_plan(conn)  # no agent_project_map, no ingest_window
        assert plan.rule_config["r2_skipped"] is True
        assert plan.rule_config["r3_skipped"] is True
        # r2-key falls through to R4 (agentforge is not S3, agent_id default -> legacy-unattributed)... wait
        # r2-key's project_id is still 'api' (an S3 placeholder) with no map, so it archives (R5).
        memories = plan.tables["private_memories"]
        r2_group = next(g for g in memories.groups if any(rid == ["r2-key"] for rid in g.row_ids))
        assert r2_group.rule == "R5"

    def test_deferred_tables_are_reported_not_written(self, conn: psycopg.Connection) -> None:
        _seed_s3_fixture(conn)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO audit_log (project_id, agent_id, event_type, key) "
                "VALUES ('api', 'default', 'save', 'audit-noise')"
            )
        conn.commit()
        plan = _build_fixture_plan(conn)
        deferred_by_table = {d.table: d for d in plan.deferred_tables}
        assert "audit_log" in deferred_by_table
        assert deferred_by_table["audit_log"].count is not None
        assert deferred_by_table["audit_log"].count >= 1
        for table in tm.DEFERRED_TABLES:
            assert table in deferred_by_table
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM audit_log")
            assert cur.fetchone()[0] >= 1  # nothing deleted from a deferred table


class TestDryRunEmptyDbRefusal:
    def test_build_plan_raises_on_empty_db(self, conn: psycopg.Connection) -> None:
        with pytest.raises(tm.EmptyEnumerationError):
            tm.build_plan(conn)

    def test_cli_exits_2_on_empty_db(
        self, fixture_dsn: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        exit_code = tm.main(["--dsn", fixture_dsn, "--dry-run"])
        assert exit_code == 2
        captured = capsys.readouterr()
        assert "zero rows" in captured.err


class TestDryRunIsReadOnly:
    def test_no_write_statement_is_issued(self, fixture_dsn: str, conn: psycopg.Connection) -> None:
        _seed_s3_fixture(conn)

        statements: list[str] = []

        class _RecordingCursor:
            def __init__(self, real_cursor: Any) -> None:
                self._cursor = real_cursor

            def execute(self, query: Any, params: Any = None) -> Any:
                statements.append(query if isinstance(query, str) else str(query))
                return self._cursor.execute(query, params)

            def fetchall(self) -> Any:
                return self._cursor.fetchall()

            def fetchone(self) -> Any:
                return self._cursor.fetchone()

            def __enter__(self) -> _RecordingCursor:
                return self

            def __exit__(self, *exc: Any) -> None:
                self._cursor.close()

        class _RecordingConnection:
            def __init__(self, real_conn: psycopg.Connection) -> None:
                self._conn = real_conn

            def cursor(self) -> _RecordingCursor:
                return _RecordingCursor(self._conn.cursor())

        with psycopg.connect(fixture_dsn) as raw_conn:
            recording_conn = _RecordingConnection(raw_conn)
            tm.build_plan(
                recording_conn, agent_project_map=_AGENT_PROJECT_MAP, ingest_window=_INGEST_WINDOW
            )  # type: ignore[arg-type]

        assert statements, "expected at least one recorded statement"
        write_verbs = re.compile(
            r"^\s*(INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|TRUNCATE)\b", re.IGNORECASE
        )
        for stmt in statements:
            assert not write_verbs.match(stmt), f"dry run issued a write statement: {stmt!r}"
            assert re.match(r"^\s*(SELECT|WITH)\b", stmt, re.IGNORECASE), (
                f"unexpected statement shape: {stmt!r}"
            )


# ---------------------------------------------------------------------------
# VAL-08: apply.
# ---------------------------------------------------------------------------


class TestApply:
    def test_apply_moves_rows_and_verifies_totals(
        self, fixture_dsn: str, conn: psycopg.Connection
    ) -> None:
        _seed_s3_fixture(conn)
        plan = _build_fixture_plan(conn)

        with psycopg.connect(fixture_dsn) as apply_conn:
            result = tm.apply_plan(apply_conn, plan, archive_table="tap7279_apply_test")

        memories_result = result.tables["private_memories"]
        assert memories_result.pre_apply_total == 6
        assert memories_result.remaining_after == 0
        assert memories_result.collisions == 1
        assert (
            memories_result.archived_count == 6
        )  # snapshot of the whole S3 population, by construction
        assert memories_result.re_homed_count == 4  # r1-key, r2-key, mem-scout-run-abc123, r4-key

        with conn.cursor() as cur:
            # Live rows are gone from the old identities.
            cur.execute(
                "SELECT count(*) FROM private_memories WHERE project_id = 'api' AND key = 'r1-key'"
            )
            assert cur.fetchone()[0] == 0

            # R1 landed at (agentforge, default, r1-key) with the migrated_from tag.
            cur.execute(
                "SELECT agent_id, tags FROM private_memories WHERE project_id = 'agentforge' AND key = 'r1-key'"
            )
            row = cur.fetchone()
            assert row is not None
            agent_id, tags = row
            assert agent_id == "default"
            assert "migrated_from:api/default" in tags

            # R4's collision: the source row won, the pre-existing row is gone.
            cur.execute(
                "SELECT value FROM private_memories "
                "WHERE project_id = 'agentforge' AND agent_id = 'legacy-unattributed' AND key = 'r4-key'"
            )
            row = cur.fetchone()
            assert row is not None
            assert row[0] == "v"  # the source row's value, not "pre-existing"

            # R5 rows are gone entirely (archived, not re-homed anywhere).
            cur.execute("SELECT count(*) FROM private_memories WHERE key IN ('exec', 'r5b-key')")
            assert cur.fetchone()[0] == 0

            # Archive table physically holds the 6-row S3 snapshot plus the one
            # collision loser (a pre-existing, non-S3 row destroyed by the re-home).
            cur.execute("SELECT count(*) FROM tap7279_apply_test")
            assert cur.fetchone()[0] == 7
            cur.execute("SELECT count(*) FROM tap7279_apply_test_relations")
            assert cur.fetchone()[0] == 2

        relations_result = result.tables["private_relations"]
        assert relations_result.pre_apply_total == 2
        assert relations_result.remaining_after == 0
        assert relations_result.archived_count == 2

    def test_apply_with_stale_plan_is_refused(
        self, fixture_dsn: str, conn: psycopg.Connection
    ) -> None:
        _seed_s3_fixture(conn)
        plan = _build_fixture_plan(conn)

        # Mutate the live table after the dry run — a new S3 row appears.
        _insert_memory(
            conn,
            project_id="api",
            agent_id="default",
            key="late-arrival",
            source_agent="unknown",
            created_at=_ts(99),
        )

        with pytest.raises(tm.StalePlanError):
            with psycopg.connect(fixture_dsn) as apply_conn:
                tm.apply_plan(apply_conn, plan, archive_table="tap7279_stale_test")

        # Refused before any write: nothing moved, no archive table left behind.
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM private_memories WHERE project_id = 'api' AND key = 'r1-key'"
            )
            assert cur.fetchone()[0] == 1
            cur.execute(
                "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tablename = 'tap7279_stale_test'"
            )
            assert cur.fetchone()[0] == 0

    def test_apply_rejects_unsafe_archive_table_name(
        self, fixture_dsn: str, conn: psycopg.Connection
    ) -> None:
        _seed_s3_fixture(conn)
        plan = _build_fixture_plan(conn)
        with pytest.raises(tm.InvalidArchiveTableNameError):
            with psycopg.connect(fixture_dsn) as apply_conn:
                tm.apply_plan(
                    apply_conn, plan, archive_table="bad; drop table private_memories; --"
                )


class TestPrivateRelationsNotYetCreated:
    """private_relations is created lazily on first use, not by a versioned
    migration (PostgresPrivateBackend._ensure_relations_table) — a genuinely
    untouched database has private_memories but no private_relations at all.
    A manual CLI run against a fixture database before any relation was ever
    saved reproduced psycopg.errors.UndefinedTable here; both build_plan and
    apply_plan must treat an absent private_relations as zero rows, not crash.
    """

    def test_dry_run_and_apply_survive_missing_relations_table(
        self, fixture_dsn: str, conn: psycopg.Connection
    ) -> None:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS private_relations")
        conn.commit()
        assert tm.table_exists(conn, "private_relations") is False

        _insert_project(conn, "agentforge", approved=True)
        _insert_memory(
            conn,
            project_id="agentforge",
            agent_id="default",
            key="only-memories-key",
            source_agent="someone",
            created_at=_ts(0),
        )

        plan = tm.build_plan(conn)
        assert plan.tables["private_relations"].live_count == 0
        assert plan.tables["private_relations"].groups == []

        with psycopg.connect(fixture_dsn) as apply_conn:
            result = tm.apply_plan(apply_conn, plan, archive_table="tap7279_no_relations_test")

        relations_result = result.tables["private_relations"]
        assert relations_result == tm.ApplyTableResult(
            pre_apply_total=0, re_homed_count=0, archived_count=0, collisions=0, remaining_after=0
        )
        memories_result = result.tables["private_memories"]
        assert memories_result.re_homed_count == 1  # R4: agentforge/default -> legacy-unattributed

        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM private_memories "
                "WHERE project_id = 'agentforge' AND agent_id = 'legacy-unattributed' "
                "AND key = 'only-memories-key'"
            )
            assert cur.fetchone()[0] == 1
            # No _relations archive table was created — nothing to snapshot.
            cur.execute(
                "SELECT count(*) FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename = 'tap7279_no_relations_test_relations'"
            )
            assert cur.fetchone()[0] == 0
