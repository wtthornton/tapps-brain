"""Integration tests for migration 017_kg_edges.sql (TAP-1489 STORY-074.2).

Verifies that `kg_edges`:
  - Is created with the expected columns and constraints.
  - Enforces RLS: tenant A cannot see tenant B's edges.
  - Enforces the partial unique index (only one active+non-invalidated edge
    per (brain_id, subject_entity_id, predicate, object_entity_id)).
  - Allows superseded / invalidated duplicates of the same triple to coexist.
  - Records supersession and contradiction correctly.
  - Rolls back cleanly on constraint violations.

Requires: ``TAPPS_TEST_POSTGRES_DSN`` environment variable pointing to a live
pgvector/pg17 Postgres instance.  Tests are skipped when the variable is not set.

Schema dependencies
-------------------
Private migrations 001–017 are applied at setup via ``apply_private_migrations``.
The migration is idempotent (CREATE TABLE/INDEX IF NOT EXISTS) so re-running
on an already-migrated database is safe.
"""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Skip guard — all tests require a live Postgres instance
# ---------------------------------------------------------------------------

_PG_DSN = os.environ.get("TAPPS_TEST_POSTGRES_DSN", "")
_SKIP_PG = not _PG_DSN

# Runtime DSN: connects as tapps_runtime (non-superuser, RLS enforced).
_RUNTIME_DSN = _PG_DSN.replace("tapps:tapps@", "tapps_runtime:tapps_runtime@", 1) if _PG_DSN else ""

pytestmark = pytest.mark.skipif(_SKIP_PG, reason="TAPPS_TEST_POSTGRES_DSN not set")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _apply_migrations() -> None:
    from tapps_brain.postgres_migrations import apply_private_migrations

    apply_private_migrations(_PG_DSN)


def _owner_conn() -> object:
    """Return a psycopg connection as the migrator/owner role (bypasses RLS)."""
    import psycopg

    return psycopg.connect(_PG_DSN, autocommit=False)


def _runtime_conn() -> object:
    """Return a psycopg connection as tapps_runtime (RLS enforced)."""
    import psycopg

    return psycopg.connect(_RUNTIME_DSN, autocommit=False)


def _uid() -> str:
    return uuid.uuid4().hex[:12]


def _insert_entity(
    conn: object,
    *,
    tenant_id: str,
    brain_id: str,
    project_id: str,
    entity_type: str = "concept",
    canonical_name: str | None = None,
) -> str:
    """Insert a kg_entities row and return its UUID id (as a string)."""
    if canonical_name is None:
        canonical_name = f"Entity-{_uid()}"
    with conn.cursor() as cur:  # type: ignore[union-attr]
        cur.execute(
            """
            INSERT INTO kg_entities
                (tenant_id, brain_id, project_id, entity_type, canonical_name)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (tenant_id, brain_id, project_id, entity_type, canonical_name),
        )
        row = cur.fetchone()
        assert row is not None
        return str(row[0])


def _insert_edge(
    conn: object,
    *,
    tenant_id: str,
    brain_id: str,
    project_id: str,
    subject_id: str,
    predicate: str,
    object_id: str,
    status: str = "active",
    invalid_at: str | None = None,
) -> str:
    """Insert a kg_edges row. Caller must commit. Returns the new edge id."""
    with conn.cursor() as cur:  # type: ignore[union-attr]
        cur.execute(
            """
            INSERT INTO kg_edges
                (tenant_id, brain_id, project_id,
                 subject_entity_id, predicate, object_entity_id,
                 status, invalid_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                tenant_id, brain_id, project_id,
                subject_id, predicate, object_id,
                status, invalid_at,
            ),
        )
        row = cur.fetchone()
        assert row is not None
        return str(row[0])


def _set_project_id(conn: object, project_id: str) -> None:
    """Set the app.project_id session variable for RLS."""
    with conn.cursor() as cur:  # type: ignore[union-attr]
        cur.execute("SET LOCAL app.project_id = %s", (project_id,))


def _count_edges(conn: object, brain_id: str) -> int:
    with conn.cursor() as cur:  # type: ignore[union-attr]
        cur.execute("SELECT COUNT(*) FROM kg_edges WHERE brain_id = %s", (brain_id,))
        row = cur.fetchone()
        return int(row[0]) if row else 0


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _apply_private_migrations() -> None:
    """Apply all private migrations (including 017) once per test module."""
    if _SKIP_PG:
        return
    _apply_migrations()


# ---------------------------------------------------------------------------
# Tests: table structure
# ---------------------------------------------------------------------------


class TestKgEdgesTableStructure:
    """Verify the table exists and has the expected columns."""

    def test_table_exists(self) -> None:
        """kg_edges table must be present in the public schema."""
        with _owner_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT EXISTS (SELECT FROM information_schema.tables"
                    " WHERE table_name = 'kg_edges')"
                )
                row = cur.fetchone()
                assert row is not None and row[0] is True

    def test_required_columns_present(self) -> None:
        """Spot-check key columns are present."""
        required = {
            "id",
            "tenant_id",
            "brain_id",
            "project_id",
            "subject_entity_id",
            "predicate",
            "object_entity_id",
            "edge_class",
            "layer",
            "profile_name",
            "metadata",
            "confidence",
            "confidence_floor",
            "status",
            "stability",
            "difficulty",
            "temporal_sensitivity",
            "valid_at",
            "invalid_at",
            "superseded_by",
            "reinforce_count",
            "last_reinforced",
            "contradicted",
            "contradiction_reason",
            "created_by_agent",
            "last_reinforced_by_agent",
            "positive_feedback_count",
            "negative_feedback_count",
        }
        with _owner_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_name = 'kg_edges'"
                )
                found = {row[0] for row in cur.fetchall()}
        missing = required - found
        assert not missing, f"Missing columns: {missing}"

    def test_rls_is_enabled_and_forced(self) -> None:
        """Both ENABLE and FORCE RLS must be set on kg_edges."""
        with _owner_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT relrowsecurity, relforcerowsecurity"
                    " FROM pg_class WHERE relname = 'kg_edges'"
                )
                row = cur.fetchone()
        assert row is not None, "pg_class row for kg_edges not found"
        relrowsecurity, relforcerowsecurity = row
        assert relrowsecurity is True, "RLS must be ENABLED"
        assert relforcerowsecurity is True, "RLS must be FORCED"

    def test_schema_version_17_recorded(self) -> None:
        """private_schema_version must contain version=17."""
        with _owner_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT version FROM private_schema_version WHERE version = 17"
                )
                row = cur.fetchone()
        assert row is not None, "Schema version 17 not recorded"

    def test_partial_unique_index_exists(self) -> None:
        """uix_kg_edges_active_triple partial unique index must exist."""
        with _owner_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT indexname FROM pg_indexes"
                    " WHERE tablename = 'kg_edges'"
                    "   AND indexname = 'uix_kg_edges_active_triple'"
                )
                row = cur.fetchone()
        assert row is not None, "Partial unique index uix_kg_edges_active_triple not found"

    def test_status_check_constraint(self) -> None:
        """Inserting an invalid status value must raise CheckViolation."""
        import psycopg

        tid = f"proj-{_uid()}"
        bid = f"brain-{_uid()}"
        with _owner_conn() as conn:
            sub_id = _insert_entity(conn, tenant_id=tid, brain_id=bid, project_id=tid)
            obj_id = _insert_entity(conn, tenant_id=tid, brain_id=bid, project_id=tid)
            conn.commit()
            try:
                with pytest.raises(psycopg.errors.CheckViolation):
                    with conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO kg_edges"
                            " (tenant_id, brain_id, project_id,"
                            "  subject_entity_id, predicate, object_entity_id, status)"
                            " VALUES (%s, %s, %s, %s, 'USES', %s, 'bad_status')",
                            (tid, bid, tid, sub_id, obj_id),
                        )
                conn.rollback()
            finally:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM kg_entities WHERE brain_id = %s", (bid,))
                conn.commit()


# ---------------------------------------------------------------------------
# Tests: basic CRUD
# ---------------------------------------------------------------------------


class TestKgEdgesCRUD:
    """Verify basic insert / read / delete via the owner connection."""

    def test_insert_and_read(self) -> None:
        """Insert an edge and read it back as the owner."""
        tid = f"proj-{_uid()}"
        bid = f"brain-{_uid()}"
        with _owner_conn() as conn:
            sub_id = _insert_entity(conn, tenant_id=tid, brain_id=bid, project_id=tid)
            obj_id = _insert_entity(conn, tenant_id=tid, brain_id=bid, project_id=tid)
            conn.commit()
            try:
                edge_id = _insert_edge(
                    conn,
                    tenant_id=tid, brain_id=bid, project_id=tid,
                    subject_id=sub_id, predicate="USES", object_id=obj_id,
                )
                conn.commit()
                with conn.cursor() as cur:
                    cur.execute("SELECT predicate, status FROM kg_edges WHERE id = %s", (edge_id,))
                    row = cur.fetchone()
                assert row is not None
                assert row[0] == "USES"
                assert row[1] == "active"
            finally:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM kg_entities WHERE brain_id = %s", (bid,))
                conn.commit()


# ---------------------------------------------------------------------------
# Tests: partial unique index
# ---------------------------------------------------------------------------


class TestKgEdgesPartialUnique:
    """Verify the partial unique index semantics."""

    def test_duplicate_active_edge_raises(self) -> None:
        """Inserting two active+non-invalidated edges with the same triple must raise."""
        import psycopg

        tid = f"proj-{_uid()}"
        bid = f"brain-{_uid()}"
        with _owner_conn() as conn:
            sub_id = _insert_entity(conn, tenant_id=tid, brain_id=bid, project_id=tid)
            obj_id = _insert_entity(conn, tenant_id=tid, brain_id=bid, project_id=tid)
            conn.commit()
            try:
                _insert_edge(
                    conn,
                    tenant_id=tid, brain_id=bid, project_id=tid,
                    subject_id=sub_id, predicate="DEPENDS_ON", object_id=obj_id,
                )
                conn.commit()
                with pytest.raises(psycopg.errors.UniqueViolation):
                    _insert_edge(
                        conn,
                        tenant_id=tid, brain_id=bid, project_id=tid,
                        subject_id=sub_id, predicate="DEPENDS_ON", object_id=obj_id,
                    )
                conn.rollback()
            finally:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM kg_entities WHERE brain_id = %s", (bid,))
                conn.commit()

    def test_superseded_edge_coexists_with_active(self) -> None:
        """A superseded edge with the same triple can coexist (partial index skips it)."""
        tid = f"proj-{_uid()}"
        bid = f"brain-{_uid()}"
        with _owner_conn() as conn:
            sub_id = _insert_entity(conn, tenant_id=tid, brain_id=bid, project_id=tid)
            obj_id = _insert_entity(conn, tenant_id=tid, brain_id=bid, project_id=tid)
            conn.commit()
            try:
                # Insert a superseded edge first.
                _insert_edge(
                    conn,
                    tenant_id=tid, brain_id=bid, project_id=tid,
                    subject_id=sub_id, predicate="IS_A", object_id=obj_id,
                    status="superseded",
                )
                conn.commit()
                # Then insert an active edge with the same triple — should succeed.
                _insert_edge(
                    conn,
                    tenant_id=tid, brain_id=bid, project_id=tid,
                    subject_id=sub_id, predicate="IS_A", object_id=obj_id,
                    status="active",
                )
                conn.commit()
            finally:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM kg_entities WHERE brain_id = %s", (bid,))
                conn.commit()

    def test_invalidated_edge_coexists_with_active(self) -> None:
        """An invalidated edge (invalid_at IS NOT NULL) coexists with an active one."""
        tid = f"proj-{_uid()}"
        bid = f"brain-{_uid()}"
        with _owner_conn() as conn:
            sub_id = _insert_entity(conn, tenant_id=tid, brain_id=bid, project_id=tid)
            obj_id = _insert_entity(conn, tenant_id=tid, brain_id=bid, project_id=tid)
            conn.commit()
            try:
                # Insert an invalidated edge (valid but with invalid_at set).
                _insert_edge(
                    conn,
                    tenant_id=tid, brain_id=bid, project_id=tid,
                    subject_id=sub_id, predicate="CAUSES", object_id=obj_id,
                    status="active",
                    invalid_at="2020-01-01T00:00:00Z",
                )
                conn.commit()
                # Active edge with same triple but no invalid_at should coexist.
                _insert_edge(
                    conn,
                    tenant_id=tid, brain_id=bid, project_id=tid,
                    subject_id=sub_id, predicate="CAUSES", object_id=obj_id,
                    status="active",
                    invalid_at=None,
                )
                conn.commit()
            finally:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM kg_entities WHERE brain_id = %s", (bid,))
                conn.commit()


# ---------------------------------------------------------------------------
# Tests: supersession
# ---------------------------------------------------------------------------


class TestKgEdgesSupersession:
    """Verify superseded_by self-FK works correctly."""

    def test_superseded_by_self_fk(self) -> None:
        """superseded_by can reference another edge in the same table."""
        tid = f"proj-{_uid()}"
        bid = f"brain-{_uid()}"
        with _owner_conn() as conn:
            sub_id = _insert_entity(conn, tenant_id=tid, brain_id=bid, project_id=tid)
            obj_id = _insert_entity(conn, tenant_id=tid, brain_id=bid, project_id=tid)
            conn.commit()
            try:
                # Old superseded edge.
                old_id = _insert_edge(
                    conn,
                    tenant_id=tid, brain_id=bid, project_id=tid,
                    subject_id=sub_id, predicate="USES", object_id=obj_id,
                    status="superseded",
                )
                conn.commit()
                # New active edge that supersedes the old one.
                new_id = _insert_edge(
                    conn,
                    tenant_id=tid, brain_id=bid, project_id=tid,
                    subject_id=sub_id, predicate="USES", object_id=obj_id,
                    status="active",
                )
                conn.commit()
                # Link old → new via superseded_by.
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE kg_edges SET superseded_by = %s WHERE id = %s",
                        (new_id, old_id),
                    )
                conn.commit()
                # Verify the link.
                with conn.cursor() as cur:
                    cur.execute("SELECT superseded_by FROM kg_edges WHERE id = %s", (old_id,))
                    row = cur.fetchone()
                assert row is not None
                assert str(row[0]) == new_id
            finally:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM kg_entities WHERE brain_id = %s", (bid,))
                conn.commit()


# ---------------------------------------------------------------------------
# Tests: contradiction
# ---------------------------------------------------------------------------


class TestKgEdgesContradiction:
    """Verify contradiction fields work correctly."""

    def test_contradiction_fields(self) -> None:
        """contradicted=True and contradiction_reason can be set on an edge."""
        tid = f"proj-{_uid()}"
        bid = f"brain-{_uid()}"
        with _owner_conn() as conn:
            sub_id = _insert_entity(conn, tenant_id=tid, brain_id=bid, project_id=tid)
            obj_id = _insert_entity(conn, tenant_id=tid, brain_id=bid, project_id=tid)
            conn.commit()
            try:
                edge_id = _insert_edge(
                    conn,
                    tenant_id=tid, brain_id=bid, project_id=tid,
                    subject_id=sub_id, predicate="SUPPORTS", object_id=obj_id,
                )
                conn.commit()
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE kg_edges"
                        " SET contradicted = TRUE,"
                        "     contradiction_reason = 'new evidence contradicts this link'"
                        " WHERE id = %s",
                        (edge_id,),
                    )
                conn.commit()
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT contradicted, contradiction_reason FROM kg_edges WHERE id = %s",
                        (edge_id,),
                    )
                    row = cur.fetchone()
                assert row is not None
                assert row[0] is True
                assert "contradicts" in row[1]
            finally:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM kg_entities WHERE brain_id = %s", (bid,))
                conn.commit()


# ---------------------------------------------------------------------------
# Tests: RLS isolation
# ---------------------------------------------------------------------------


class TestKgEdgesRLS:
    """Verify that tenant A cannot see tenant B's edges via tapps_runtime."""

    def test_tenant_isolation(self) -> None:
        """Edges inserted for tenant_A are invisible when app.project_id=tenant_B."""
        tenant_a = f"proj-a-{_uid()}"
        tenant_b = f"proj-b-{_uid()}"
        brain_a = f"brain-a-{_uid()}"

        with _owner_conn() as conn:
            sub_id = _insert_entity(
                conn, tenant_id=tenant_a, brain_id=brain_a, project_id=tenant_a
            )
            obj_id = _insert_entity(
                conn, tenant_id=tenant_a, brain_id=brain_a, project_id=tenant_a
            )
            _insert_edge(
                conn,
                tenant_id=tenant_a, brain_id=brain_a, project_id=tenant_a,
                subject_id=sub_id, predicate="KNOWS", object_id=obj_id,
            )
            conn.commit()

        try:
            # tapps_runtime with tenant_A context — should see the row.
            with _runtime_conn() as conn:
                _set_project_id(conn, tenant_a)
                count_a = _count_edges(conn, brain_a)
                conn.rollback()
            assert count_a == 1, f"Expected 1 edge for tenant_a, got {count_a}"

            # tapps_runtime with tenant_B context — must see 0 rows.
            with _runtime_conn() as conn:
                _set_project_id(conn, tenant_b)
                count_b = _count_edges(conn, brain_a)
                conn.rollback()
            assert count_b == 0, f"RLS breach: tenant_b can see tenant_a's edges ({count_b} rows)"
        finally:
            with _owner_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM kg_entities WHERE brain_id = %s", (brain_a,))
                conn.commit()

    def test_rls_blocks_insert_for_wrong_tenant(self) -> None:
        """tapps_runtime cannot insert an edge whose tenant_id != app.project_id."""
        import psycopg

        tenant_a = f"proj-a-{_uid()}"
        tenant_b = f"proj-b-{_uid()}"
        brain_x = f"brain-x-{_uid()}"

        # Create entities as owner so FK references work.
        with _owner_conn() as conn:
            sub_id = _insert_entity(
                conn, tenant_id=tenant_a, brain_id=brain_x, project_id=tenant_a
            )
            obj_id = _insert_entity(
                conn, tenant_id=tenant_a, brain_id=brain_x, project_id=tenant_a
            )
            conn.commit()

        try:
            with _runtime_conn() as conn:
                try:
                    _set_project_id(conn, tenant_a)
                    # Attempt to insert an edge claiming tenant_b — WITH CHECK must fail.
                    with pytest.raises(psycopg.errors.InsufficientPrivilege):
                        with conn.cursor() as cur:
                            cur.execute(
                                "INSERT INTO kg_edges"
                                " (tenant_id, brain_id, project_id,"
                                "  subject_entity_id, predicate, object_entity_id)"
                                " VALUES (%s, %s, %s, %s, 'LEAKS', %s)",
                                (tenant_b, brain_x, tenant_b, sub_id, obj_id),
                            )
                finally:
                    conn.rollback()
        finally:
            with _owner_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM kg_entities WHERE brain_id = %s", (brain_x,))
                conn.commit()
