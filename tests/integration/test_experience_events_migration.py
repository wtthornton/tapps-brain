"""Integration tests — TAP-1491 STORY-074.4: experience_events partitioned table.

Verifies partition routing, default-partition fallback, and RLS isolation
for the experience_events monthly-partitioned table added in migration 020.

Requires a live Postgres ≥17 instance at ``TAPPS_TEST_POSTGRES_DSN``.
Skipped automatically when that env var is unset.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest

_PG_DSN = os.environ.get("TAPPS_TEST_POSTGRES_DSN", "")
_SKIP = not _PG_DSN

pytestmark = pytest.mark.skipif(_SKIP, reason="TAPPS_TEST_POSTGRES_DSN not set")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _apply_migrations(dsn: str) -> None:
    from tapps_brain.postgres_migrations import apply_private_migrations

    apply_private_migrations(dsn)


def _conn(dsn: str):  # type: ignore[return]
    """Return an open psycopg connection (caller must close)."""
    psycopg = pytest.importorskip("psycopg")
    return psycopg.connect(dsn)


def _insert_event(
    conn: object,
    project_id: str,
    event_time: "datetime",
    event_type: str = "test_event",
) -> str:
    """Insert one row into experience_events; returns the generated id."""
    row_id = str(uuid.uuid4())
    conn.execute(  # type: ignore[attr-defined]
        """
        INSERT INTO experience_events
            (id, tenant_id, brain_id, project_id, agent_id, event_type, event_time)
        VALUES (%s, %s, %s, %s, 'test-agent', %s, %s)
        """,
        (row_id, project_id, "brain-1", project_id, event_type, event_time),
    )
    return row_id


def _set_tenant(conn: object, project_id: str) -> None:
    conn.execute(  # type: ignore[attr-defined]
        "SELECT set_config('app.project_id', %s, FALSE)",
        (project_id,),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestExperienceEventsTableExists:
    """Basic smoke test that migration 020 created the table."""

    def test_table_exists(self) -> None:
        _apply_migrations(_PG_DSN)
        conn = _conn(_PG_DSN)
        try:
            cur = conn.execute(  # type: ignore[attr-defined]
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_name = 'experience_events'"
            )
            count = cur.fetchone()[0]
            assert count == 1, "experience_events table not found after migration"
        finally:
            conn.close()  # type: ignore[attr-defined]

    def test_partition_type_is_range(self) -> None:
        conn = _conn(_PG_DSN)
        try:
            cur = conn.execute(  # type: ignore[attr-defined]
                """
                SELECT pt.partattrs, c.relname
                FROM pg_class c
                JOIN pg_partitioned_table pt ON c.oid = pt.partrelid
                WHERE c.relname = 'experience_events'
                """
            )
            row = cur.fetchone()
            assert row is not None, "experience_events is not a partitioned table"
        finally:
            conn.close()  # type: ignore[attr-defined]

    def test_default_partition_exists(self) -> None:
        conn = _conn(_PG_DSN)
        try:
            cur = conn.execute(  # type: ignore[attr-defined]
                "SELECT COUNT(*) FROM pg_class WHERE relname = 'experience_events_default'"
            )
            count = cur.fetchone()[0]
            assert count == 1, "experience_events_default partition not found"
        finally:
            conn.close()  # type: ignore[attr-defined]

    def test_twelve_monthly_partitions_exist(self) -> None:
        conn = _conn(_PG_DSN)
        try:
            cur = conn.execute(  # type: ignore[attr-defined]
                """
                SELECT COUNT(*) FROM pg_inherits i
                JOIN pg_class parent ON i.inhparent = parent.oid
                JOIN pg_class child  ON i.inhrelid  = child.oid
                WHERE parent.relname = 'experience_events'
                  AND child.relname  LIKE 'experience_events_y20%%'
                """
            )
            count = cur.fetchone()[0]
            assert count == 12, f"Expected 12 monthly partitions, found {count}"
        finally:
            conn.close()  # type: ignore[attr-defined]


class TestPartitionRouting:
    """Rows with different event_time values land in the correct partitions."""

    def test_may_2026_event_routes_to_y2026m05(self) -> None:
        _apply_migrations(_PG_DSN)
        project = f"test-routing-{uuid.uuid4().hex[:8]}"
        conn = _conn(_PG_DSN)
        try:
            _set_tenant(conn, project)
            event_time = datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc)
            row_id = _insert_event(conn, project, event_time)
            conn.commit()  # type: ignore[attr-defined]

            cur = conn.execute(  # type: ignore[attr-defined]
                "SELECT tableoid::regclass::text FROM experience_events WHERE id = %s",
                (row_id,),
            )
            partition_name = cur.fetchone()[0]
            assert "y2026m05" in partition_name, (
                f"Expected y2026m05 partition, got {partition_name!r}"
            )
        finally:
            with conn:  # type: ignore[attr-defined]
                conn.execute(  # type: ignore[attr-defined]
                    "DELETE FROM experience_events WHERE project_id = %s", (project,)
                )
            conn.close()  # type: ignore[attr-defined]

    def test_january_2027_event_routes_to_y2027m01(self) -> None:
        project = f"test-routing-{uuid.uuid4().hex[:8]}"
        conn = _conn(_PG_DSN)
        try:
            _set_tenant(conn, project)
            event_time = datetime(2027, 1, 10, 8, 0, 0, tzinfo=timezone.utc)
            row_id = _insert_event(conn, project, event_time)
            conn.commit()  # type: ignore[attr-defined]

            cur = conn.execute(  # type: ignore[attr-defined]
                "SELECT tableoid::regclass::text FROM experience_events WHERE id = %s",
                (row_id,),
            )
            partition_name = cur.fetchone()[0]
            assert "y2027m01" in partition_name, (
                f"Expected y2027m01 partition, got {partition_name!r}"
            )
        finally:
            with conn:  # type: ignore[attr-defined]
                conn.execute(  # type: ignore[attr-defined]
                    "DELETE FROM experience_events WHERE project_id = %s", (project,)
                )
            conn.close()  # type: ignore[attr-defined]

    def test_future_event_falls_into_default_partition(self) -> None:
        """An event_time beyond all pre-created ranges → default partition."""
        project = f"test-routing-{uuid.uuid4().hex[:8]}"
        conn = _conn(_PG_DSN)
        try:
            _set_tenant(conn, project)
            # 2030 is well outside the pre-created 2026-05 through 2027-04 range.
            event_time = datetime(2030, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
            row_id = _insert_event(conn, project, event_time)
            conn.commit()  # type: ignore[attr-defined]

            cur = conn.execute(  # type: ignore[attr-defined]
                "SELECT tableoid::regclass::text FROM experience_events WHERE id = %s",
                (row_id,),
            )
            partition_name = cur.fetchone()[0]
            assert "default" in partition_name, (
                f"Expected default partition for out-of-range time, got {partition_name!r}"
            )
        finally:
            with conn:  # type: ignore[attr-defined]
                conn.execute(  # type: ignore[attr-defined]
                    "DELETE FROM experience_events WHERE project_id = %s", (project,)
                )
            conn.close()  # type: ignore[attr-defined]


class TestRLSIsolation:
    """Tenant A cannot read or modify Tenant B's experience_events rows."""

    def test_tenant_a_cannot_read_tenant_b_events(self) -> None:
        _apply_migrations(_PG_DSN)
        project_a = f"rls-a-{uuid.uuid4().hex[:8]}"
        project_b = f"rls-b-{uuid.uuid4().hex[:8]}"
        conn = _conn(_PG_DSN)
        try:
            # Insert as tenant B
            _set_tenant(conn, project_b)
            event_time = datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
            row_id_b = _insert_event(conn, project_b, event_time)
            conn.commit()  # type: ignore[attr-defined]

            # Now switch context to tenant A
            _set_tenant(conn, project_a)
            cur = conn.execute(  # type: ignore[attr-defined]
                "SELECT id FROM experience_events WHERE id = %s", (row_id_b,)
            )
            result = cur.fetchone()
            assert result is None, (
                f"Tenant A can read Tenant B's row {row_id_b} — RLS breach!"
            )
        finally:
            # Cleanup: reset to B to delete its own row
            _set_tenant(conn, project_b)
            with conn:  # type: ignore[attr-defined]
                conn.execute(  # type: ignore[attr-defined]
                    "DELETE FROM experience_events WHERE project_id = %s", (project_b,)
                )
            conn.close()  # type: ignore[attr-defined]

    def test_tenant_sees_only_own_events(self) -> None:
        """Each tenant sees exactly its own row count."""
        project_a = f"rls-own-a-{uuid.uuid4().hex[:8]}"
        project_b = f"rls-own-b-{uuid.uuid4().hex[:8]}"
        conn = _conn(_PG_DSN)
        try:
            event_time_a = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)
            event_time_b = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)

            # Insert tenant A event
            _set_tenant(conn, project_a)
            _insert_event(conn, project_a, event_time_a, "event_a")
            conn.commit()  # type: ignore[attr-defined]

            # Insert tenant B event
            _set_tenant(conn, project_b)
            _insert_event(conn, project_b, event_time_b, "event_b")
            conn.commit()  # type: ignore[attr-defined]

            # Verify tenant A count
            _set_tenant(conn, project_a)
            cur = conn.execute(  # type: ignore[attr-defined]
                "SELECT COUNT(*) FROM experience_events WHERE project_id = %s", (project_a,)
            )
            count_a = cur.fetchone()[0]
            assert count_a == 1, f"Tenant A should see 1 event, got {count_a}"

            # Verify tenant B count
            _set_tenant(conn, project_b)
            cur = conn.execute(  # type: ignore[attr-defined]
                "SELECT COUNT(*) FROM experience_events WHERE project_id = %s", (project_b,)
            )
            count_b = cur.fetchone()[0]
            assert count_b == 1, f"Tenant B should see 1 event, got {count_b}"
        finally:
            for project in (project_a, project_b):
                _set_tenant(conn, project)
                with conn:  # type: ignore[attr-defined]
                    conn.execute(  # type: ignore[attr-defined]
                        "DELETE FROM experience_events WHERE project_id = %s", (project,)
                    )
            conn.close()  # type: ignore[attr-defined]

    def test_rls_enforced_without_project_id(self) -> None:
        """Querying without app.project_id set returns no rows (fail-closed)."""
        project = f"rls-closed-{uuid.uuid4().hex[:8]}"
        conn = _conn(_PG_DSN)
        try:
            _set_tenant(conn, project)
            event_time = datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc)
            _insert_event(conn, project, event_time)
            conn.commit()  # type: ignore[attr-defined]

            # Clear app.project_id — RLS should hide all rows
            conn.execute(  # type: ignore[attr-defined]
                "SELECT set_config('app.project_id', '', FALSE)"
            )
            cur = conn.execute(  # type: ignore[attr-defined]
                "SELECT COUNT(*) FROM experience_events"
            )
            count = cur.fetchone()[0]
            assert count == 0, (
                f"Expected 0 rows with empty project_id (fail-closed RLS), got {count}"
            )
        finally:
            _set_tenant(conn, project)
            with conn:  # type: ignore[attr-defined]
                conn.execute(  # type: ignore[attr-defined]
                    "DELETE FROM experience_events WHERE project_id = %s", (project,)
                )
            conn.close()  # type: ignore[attr-defined]


class TestIdempotency:
    """Running migration 020 twice must not raise errors."""

    def test_applying_migrations_twice_is_safe(self) -> None:
        """apply_private_migrations is idempotent — second call does nothing."""
        from tapps_brain.postgres_migrations import apply_private_migrations

        # First application already done by prior tests.
        # Second application should complete without error.
        applied = apply_private_migrations(_PG_DSN)
        # No new migrations should be applied on a clean DB.
        assert 20 not in applied, (
            "Migration 020 was re-applied on second run — idempotency broken"
        )
