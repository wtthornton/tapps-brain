"""Integration tests for migration 016_kg_entities.sql (TAP-1488 STORY-074.1).

Verifies that `kg_entities`:
  - Is created with the expected columns and constraints.
  - Enforces RLS: tenant A cannot see tenant B's entities.
  - Rejects rows that violate the partial unique constraint.
  - Rolls back cleanly on conflict.

Requires: ``TAPPS_TEST_POSTGRES_DSN`` environment variable pointing to a live
pgvector/pg17 Postgres instance.  Tests are skipped when the variable is not set.

Schema dependencies
-------------------
Private migrations 001–016 are applied at setup via ``apply_private_migrations``.
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

# Runtime DSN: same host/db but connects as tapps_runtime (non-superuser, RLS
# is enforced because FORCE ROW LEVEL SECURITY is set).
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
    """Insert a kg_entities row. Caller must commit. Returns canonical_name used."""
    if canonical_name is None:
        canonical_name = f"Entity-{_uid()}"
    with conn.cursor() as cur:  # type: ignore[union-attr]
        cur.execute(
            """
            INSERT INTO kg_entities
                (tenant_id, brain_id, project_id, entity_type, canonical_name)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (tenant_id, brain_id, project_id, entity_type, canonical_name),
        )
    return canonical_name


def _set_project_id(conn: object, project_id: str) -> None:
    """Set the app.project_id session variable for RLS."""
    with conn.cursor() as cur:  # type: ignore[union-attr]
        cur.execute("SET LOCAL app.project_id = %s", (project_id,))


def _count_entities(conn: object, brain_id: str) -> int:
    with conn.cursor() as cur:  # type: ignore[union-attr]
        cur.execute("SELECT COUNT(*) FROM kg_entities WHERE brain_id = %s", (brain_id,))
        row = cur.fetchone()
        return int(row[0]) if row else 0


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _apply_private_migrations() -> None:
    """Apply all private migrations (including 016) once per test module."""
    if _SKIP_PG:
        return
    _apply_migrations()


# ---------------------------------------------------------------------------
# Tests: table structure
# ---------------------------------------------------------------------------


class TestKgEntitiesTableStructure:
    """Verify the table exists and has the expected columns."""

    def test_table_exists(self) -> None:
        """kg_entities table must be present in the public schema."""
        with _owner_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT EXISTS (SELECT FROM information_schema.tables"
                    " WHERE table_name = 'kg_entities')"
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
            "entity_type",
            "canonical_name",
            "canonical_name_norm",
            "aliases",
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
            "positive_feedback_count",
            "negative_feedback_count",
        }
        with _owner_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_name = 'kg_entities'"
                )
                found = {row[0] for row in cur.fetchall()}
        missing = required - found
        assert not missing, f"Missing columns: {missing}"

    def test_canonical_name_norm_is_stored_generated(self) -> None:
        """canonical_name_norm must be a STORED generated column."""
        with _owner_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT is_generated, generation_expression"
                    " FROM information_schema.columns"
                    " WHERE table_name = 'kg_entities'"
                    "   AND column_name = 'canonical_name_norm'"
                )
                row = cur.fetchone()
        assert row is not None, "canonical_name_norm column not found"
        is_generated, expr = row
        assert is_generated == "ALWAYS", "canonical_name_norm must be GENERATED ALWAYS"
        assert expr is not None and "lower" in expr.lower()

    def test_rls_is_enabled_and_forced(self) -> None:
        """Both ENABLE and FORCE RLS must be set on kg_entities."""
        with _owner_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT relrowsecurity, relforcerowsecurity"
                    " FROM pg_class WHERE relname = 'kg_entities'"
                )
                row = cur.fetchone()
        assert row is not None, "pg_class row for kg_entities not found"
        relrowsecurity, relforcerowsecurity = row
        assert relrowsecurity is True, "RLS must be ENABLED"
        assert relforcerowsecurity is True, "RLS must be FORCED"

    def test_schema_version_16_recorded(self) -> None:
        """private_schema_version must contain version=16."""
        with _owner_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT version FROM private_schema_version WHERE version = 16"
                )
                row = cur.fetchone()
        assert row is not None, "Schema version 16 not recorded"


# ---------------------------------------------------------------------------
# Tests: basic CRUD
# ---------------------------------------------------------------------------


class TestKgEntitiesCRUD:
    """Verify basic insert / read / delete via the owner connection."""

    def test_insert_and_read(self) -> None:
        """Insert an entity and read it back as the owner."""
        tid = f"proj-{_uid()}"
        bid = f"brain-{_uid()}"
        name = f"SomeEntity-{_uid()}"
        with _owner_conn() as conn:
            _insert_entity(conn, tenant_id=tid, brain_id=bid, project_id=tid, canonical_name=name)
            conn.commit()
            with conn.cursor() as cur:
                cur.execute("SELECT canonical_name_norm FROM kg_entities WHERE brain_id = %s", (bid,))
                row = cur.fetchone()
            assert row is not None
            assert row[0] == name.lower()
            # Cleanup
            with conn.cursor() as cur:
                cur.execute("DELETE FROM kg_entities WHERE brain_id = %s", (bid,))
            conn.commit()

    def test_unique_constraint_active_entity(self) -> None:
        """Inserting a duplicate (brain_id, entity_type, canonical_name_norm) raises."""
        import psycopg

        tid = f"proj-{_uid()}"
        bid = f"brain-{_uid()}"
        name = f"DupeEntity-{_uid()}"
        with _owner_conn() as conn:
            _insert_entity(conn, tenant_id=tid, brain_id=bid, project_id=tid, canonical_name=name)
            conn.commit()
            with pytest.raises(psycopg.errors.UniqueViolation):
                _insert_entity(conn, tenant_id=tid, brain_id=bid, project_id=tid, canonical_name=name)
            conn.rollback()
            # Cleanup
            with conn.cursor() as cur:
                cur.execute("DELETE FROM kg_entities WHERE brain_id = %s", (bid,))
            conn.commit()

    def test_status_check_constraint(self) -> None:
        """Inserting an invalid status value must raise."""
        import psycopg

        tid = f"proj-{_uid()}"
        bid = f"brain-{_uid()}"
        with _owner_conn() as conn:
            with pytest.raises(psycopg.errors.CheckViolation):
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO kg_entities"
                        " (tenant_id, brain_id, project_id, entity_type, canonical_name, status)"
                        " VALUES (%s, %s, %s, 'concept', 'Bad', 'invalid_status')",
                        (tid, bid, tid),
                    )
            conn.rollback()


# ---------------------------------------------------------------------------
# Tests: RLS isolation
# ---------------------------------------------------------------------------


class TestKgEntitiesRLS:
    """Verify that tenant A cannot see tenant B's entities via tapps_runtime."""

    def test_tenant_isolation(self) -> None:
        """Entities inserted for tenant_A are invisible when app.project_id=tenant_B."""
        tenant_a = f"proj-a-{_uid()}"
        tenant_b = f"proj-b-{_uid()}"
        brain_a = f"brain-a-{_uid()}"

        # Insert entity as owner (bypasses RLS).
        with _owner_conn() as conn:
            _insert_entity(conn, tenant_id=tenant_a, brain_id=brain_a, project_id=tenant_a)
            conn.commit()

        try:
            # Query as tapps_runtime with tenant_A context — should see the row.
            # psycopg3 autocommit=False means we are already in an implicit transaction;
            # SET LOCAL scopes to it, conn.rollback() resets everything cleanly.
            with _runtime_conn() as conn:
                _set_project_id(conn, tenant_a)
                count_a = _count_entities(conn, brain_a)
                conn.rollback()
            assert count_a == 1, f"Expected 1 entity for tenant_a, got {count_a}"

            # Query as tapps_runtime with tenant_B context — must see 0 rows.
            with _runtime_conn() as conn:
                _set_project_id(conn, tenant_b)
                count_b = _count_entities(conn, brain_a)
                conn.rollback()
            assert count_b == 0, f"RLS breach: tenant_b can see tenant_a's entities ({count_b} rows)"
        finally:
            # Cleanup via owner connection.
            with _owner_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM kg_entities WHERE brain_id = %s", (brain_a,))
                conn.commit()

    def test_rls_blocks_insert_for_wrong_tenant(self) -> None:
        """tapps_runtime cannot insert an entity whose tenant_id != app.project_id."""
        import psycopg

        tenant_a = f"proj-a-{_uid()}"
        tenant_b = f"proj-b-{_uid()}"
        brain_x = f"brain-x-{_uid()}"

        with _runtime_conn() as conn:
            try:
                _set_project_id(conn, tenant_a)
                # Attempt to insert a row belonging to tenant_b — WITH CHECK must fail.
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    with conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO kg_entities"
                            " (tenant_id, brain_id, project_id, entity_type, canonical_name)"
                            " VALUES (%s, %s, %s, 'concept', 'LeakEntity')",
                            (tenant_b, brain_x, tenant_b),
                        )
            finally:
                conn.rollback()
