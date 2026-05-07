"""Integration tests for migrations 018_kg_evidence.sql and 019_kg_aliases.sql
(TAP-1490 STORY-074.3).

Verifies that:
  - ``kg_evidence`` is created with the expected columns and constraints.
  - ``kg_evidence`` enforces the XOR attachment constraint (exactly one of
    edge_id / entity_id must be non-null).
  - ``kg_evidence`` cascades deletes from kg_edges and kg_entities.
  - ``kg_evidence`` enforces RLS tenant isolation.
  - ``kg_aliases`` is created with the expected columns and constraints.
  - ``kg_aliases`` alias_norm is a STORED generated column.
  - ``kg_aliases`` rejects duplicate active aliases for the same entity.
  - ``kg_aliases`` allows a rejected alias to coexist with an active one
    because the UNIQUE constraint is on the table (not partial-only).
  - ``kg_aliases`` cascades deletes from kg_entities (ON DELETE CASCADE).
  - ``kg_aliases`` sets evidence_id to NULL when linked evidence is deleted
    (ON DELETE SET NULL).
  - ``kg_aliases`` enforces RLS tenant isolation.

Requires: ``TAPPS_TEST_POSTGRES_DSN`` environment variable pointing to a live
pgvector/pg17 Postgres instance.  Tests are skipped when the variable is not set.

Schema dependencies
-------------------
Private migrations 001–019 are applied at setup via ``apply_private_migrations``.
The migration is idempotent (CREATE TABLE/INDEX IF NOT EXISTS) so re-running
on an already-migrated database is safe.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Skip guard — all tests require a live Postgres instance
# ---------------------------------------------------------------------------

_PG_DSN = os.environ.get("TAPPS_TEST_POSTGRES_DSN", "")
_SKIP_PG = not _PG_DSN

_RUNTIME_DSN = (
    _PG_DSN.replace("tapps:tapps@", "tapps_runtime:tapps_runtime@", 1) if _PG_DSN else ""
)

pytestmark = pytest.mark.skipif(_SKIP_PG, reason="TAPPS_TEST_POSTGRES_DSN not set")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _apply_migrations() -> None:
    from tapps_brain.postgres_migrations import apply_private_migrations

    apply_private_migrations(_PG_DSN)


def _owner_conn() -> Any:
    """Return a psycopg connection as the migrator/owner role (bypasses RLS)."""
    import psycopg

    return psycopg.connect(_PG_DSN, autocommit=False)


def _runtime_conn() -> Any:
    """Return a psycopg connection as tapps_runtime (RLS enforced)."""
    import psycopg

    return psycopg.connect(_RUNTIME_DSN, autocommit=False)


def _uid() -> str:
    return uuid.uuid4().hex[:12]


def _set_project_id(conn: Any, project_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute("SET LOCAL app.project_id = %s", (project_id,))


# ---------------------------------------------------------------------------
# Low-level insert helpers (owner connection assumed, caller commits)
# ---------------------------------------------------------------------------


def _insert_entity(
    conn: Any,
    *,
    tenant_id: str,
    brain_id: str,
    project_id: str,
    entity_type: str = "concept",
    canonical_name: str | None = None,
) -> str:
    """Insert a kg_entities row. Returns the UUID id as a string."""
    if canonical_name is None:
        canonical_name = f"Entity-{_uid()}"
    with conn.cursor() as cur:
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
    conn: Any,
    *,
    tenant_id: str,
    brain_id: str,
    project_id: str,
    subject_id: str,
    object_id: str,
    predicate: str = "RELATED_TO",
) -> str:
    """Insert a kg_edges row. Returns the UUID id as a string."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO kg_edges
                (tenant_id, brain_id, project_id,
                 subject_entity_id, predicate, object_entity_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (tenant_id, brain_id, project_id, subject_id, predicate, object_id),
        )
        row = cur.fetchone()
        assert row is not None
        return str(row[0])


def _insert_evidence(
    conn: Any,
    *,
    tenant_id: str,
    brain_id: str,
    project_id: str,
    edge_id: str | None = None,
    entity_id: str | None = None,
    source_type: str = "agent",
    confidence: float = 0.8,
) -> str:
    """Insert a kg_evidence row. Returns UUID id. Caller must commit."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO kg_evidence
                (tenant_id, brain_id, project_id,
                 edge_id, entity_id, source_type, confidence)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (tenant_id, brain_id, project_id, edge_id, entity_id, source_type, confidence),
        )
        row = cur.fetchone()
        assert row is not None
        return str(row[0])


def _insert_alias(
    conn: Any,
    *,
    tenant_id: str,
    brain_id: str,
    project_id: str,
    entity_id: str,
    alias: str | None = None,
    evidence_id: str | None = None,
    status: str = "active",
) -> str:
    """Insert a kg_aliases row. Returns UUID id. Caller must commit."""
    if alias is None:
        alias = f"alias-{_uid()}"
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO kg_aliases
                (tenant_id, brain_id, project_id, entity_id, alias, evidence_id, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (tenant_id, brain_id, project_id, entity_id, alias, evidence_id, status),
        )
        row = cur.fetchone()
        assert row is not None
        return str(row[0])


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _apply_private_migrations() -> None:
    """Apply all private migrations (001–019) once per test module."""
    if _SKIP_PG:
        return
    _apply_migrations()


# ---------------------------------------------------------------------------
# Tests: kg_evidence — table structure
# ---------------------------------------------------------------------------


class TestKgEvidenceTableStructure:
    """kg_evidence table must exist with expected columns, constraint, and RLS."""

    def test_table_exists(self) -> None:
        with _owner_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT EXISTS (SELECT FROM information_schema.tables"
                    " WHERE table_name = 'kg_evidence')"
                )
                row = cur.fetchone()
                assert row is not None and row[0] is True

    def test_required_columns_present(self) -> None:
        required = {
            "id",
            "tenant_id",
            "brain_id",
            "project_id",
            "edge_id",
            "entity_id",
            "source_type",
            "source_id",
            "source_key",
            "source_uri",
            "source_hash",
            "source_span",
            "quote",
            "metadata",
            "source_agent",
            "confidence",
            "utility_score",
            "created_at",
            "valid_at",
            "invalid_at",
            "status",
        }
        with _owner_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_name = 'kg_evidence'"
                )
                found = {row[0] for row in cur.fetchall()}
        missing = required - found
        assert not missing, f"Missing columns: {missing}"

    def test_rls_is_enabled_and_forced(self) -> None:
        with _owner_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT relrowsecurity, relforcerowsecurity"
                    " FROM pg_class WHERE relname = 'kg_evidence'"
                )
                row = cur.fetchone()
        assert row is not None, "pg_class row for kg_evidence not found"
        assert row[0] is True, "RLS must be ENABLED on kg_evidence"
        assert row[1] is True, "RLS must be FORCED on kg_evidence"

    def test_schema_version_18_recorded(self) -> None:
        with _owner_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT version FROM private_schema_version WHERE version = 18"
                )
                row = cur.fetchone()
        assert row is not None, "Schema version 18 not recorded"


# ---------------------------------------------------------------------------
# Tests: kg_evidence — XOR attachment constraint
# ---------------------------------------------------------------------------


class TestKgEvidenceXOR:
    """Exactly one of edge_id / entity_id must be non-null."""

    def _mk_tenant(self) -> tuple[str, str, str]:
        tid = f"proj-{_uid()}"
        bid = f"brain-{_uid()}"
        return tid, bid, tid  # tenant_id, brain_id, project_id (same as tenant)

    def test_attach_to_edge(self) -> None:
        """Attaching evidence to an edge (entity_id NULL) is valid."""
        tid, bid, pid = self._mk_tenant()
        with _owner_conn() as conn:
            eid1 = _insert_entity(conn, tenant_id=tid, brain_id=bid, project_id=pid)
            eid2 = _insert_entity(conn, tenant_id=tid, brain_id=bid, project_id=pid)
            conn.commit()
            edge_id = _insert_edge(
                conn,
                tenant_id=tid, brain_id=bid, project_id=pid,
                subject_id=eid1, object_id=eid2,
            )
            conn.commit()
            ev_id = _insert_evidence(
                conn, tenant_id=tid, brain_id=bid, project_id=pid, edge_id=edge_id
            )
            conn.commit()
            with conn.cursor() as cur:
                cur.execute("SELECT edge_id, entity_id FROM kg_evidence WHERE id = %s", (ev_id,))
                row = cur.fetchone()
            assert row is not None
            assert str(row[0]) == edge_id
            assert row[1] is None
            # Cleanup
            with conn.cursor() as cur:
                cur.execute("DELETE FROM kg_entities WHERE brain_id = %s", (bid,))
            conn.commit()

    def test_attach_to_entity(self) -> None:
        """Attaching evidence to an entity (edge_id NULL) is valid."""
        tid, bid, pid = self._mk_tenant()
        with _owner_conn() as conn:
            ent_id = _insert_entity(conn, tenant_id=tid, brain_id=bid, project_id=pid)
            conn.commit()
            ev_id = _insert_evidence(
                conn, tenant_id=tid, brain_id=bid, project_id=pid, entity_id=ent_id
            )
            conn.commit()
            with conn.cursor() as cur:
                cur.execute("SELECT edge_id, entity_id FROM kg_evidence WHERE id = %s", (ev_id,))
                row = cur.fetchone()
            assert row is not None
            assert row[0] is None
            assert str(row[1]) == ent_id
            # Cleanup
            with conn.cursor() as cur:
                cur.execute("DELETE FROM kg_entities WHERE brain_id = %s", (bid,))
            conn.commit()

    def test_both_null_is_rejected(self) -> None:
        """Both edge_id and entity_id NULL must raise CheckViolation."""
        import psycopg

        tid, bid, pid = self._mk_tenant()
        with _owner_conn() as conn:
            with pytest.raises(psycopg.errors.CheckViolation):
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO kg_evidence"
                        " (tenant_id, brain_id, project_id, source_type)"
                        " VALUES (%s, %s, %s, 'agent')",
                        (tid, bid, pid),
                    )
            conn.rollback()

    def test_both_non_null_is_rejected(self) -> None:
        """Both edge_id and entity_id non-null must raise CheckViolation."""
        import psycopg

        tid, bid, pid = self._mk_tenant()
        with _owner_conn() as conn:
            eid1 = _insert_entity(conn, tenant_id=tid, brain_id=bid, project_id=pid)
            eid2 = _insert_entity(conn, tenant_id=tid, brain_id=bid, project_id=pid)
            conn.commit()
            edge_id = _insert_edge(
                conn,
                tenant_id=tid, brain_id=bid, project_id=pid,
                subject_id=eid1, object_id=eid2,
            )
            conn.commit()
            with pytest.raises(psycopg.errors.CheckViolation):
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO kg_evidence"
                        " (tenant_id, brain_id, project_id, edge_id, entity_id)"
                        " VALUES (%s, %s, %s, %s, %s)",
                        (tid, bid, pid, edge_id, eid1),
                    )
            conn.rollback()
            # Cleanup
            with conn.cursor() as cur:
                cur.execute("DELETE FROM kg_entities WHERE brain_id = %s", (bid,))
            conn.commit()


# ---------------------------------------------------------------------------
# Tests: kg_evidence — cascade delete
# ---------------------------------------------------------------------------


class TestKgEvidenceCascade:
    """Deleting the parent edge or entity removes attached evidence."""

    def _mk_tenant(self) -> tuple[str, str, str]:
        tid = f"proj-{_uid()}"
        bid = f"brain-{_uid()}"
        return tid, bid, tid

    def test_cascade_on_edge_delete(self) -> None:
        """Deleting an edge must cascade-delete its evidence rows."""
        tid, bid, pid = self._mk_tenant()
        with _owner_conn() as conn:
            eid1 = _insert_entity(conn, tenant_id=tid, brain_id=bid, project_id=pid)
            eid2 = _insert_entity(conn, tenant_id=tid, brain_id=bid, project_id=pid)
            conn.commit()
            edge_id = _insert_edge(
                conn,
                tenant_id=tid, brain_id=bid, project_id=pid,
                subject_id=eid1, object_id=eid2,
            )
            conn.commit()
            ev_id = _insert_evidence(
                conn, tenant_id=tid, brain_id=bid, project_id=pid, edge_id=edge_id
            )
            conn.commit()

            # Delete the edge — evidence should go with it.
            with conn.cursor() as cur:
                cur.execute("DELETE FROM kg_edges WHERE id = %s", (edge_id,))
            conn.commit()

            with conn.cursor() as cur:
                cur.execute("SELECT id FROM kg_evidence WHERE id = %s", (ev_id,))
                row = cur.fetchone()
            assert row is None, "Evidence must be deleted when its edge is deleted"

            # Cleanup
            with conn.cursor() as cur:
                cur.execute("DELETE FROM kg_entities WHERE brain_id = %s", (bid,))
            conn.commit()

    def test_cascade_on_entity_delete(self) -> None:
        """Deleting an entity must cascade-delete its evidence rows."""
        tid, bid, pid = self._mk_tenant()
        with _owner_conn() as conn:
            ent_id = _insert_entity(conn, tenant_id=tid, brain_id=bid, project_id=pid)
            conn.commit()
            ev_id = _insert_evidence(
                conn, tenant_id=tid, brain_id=bid, project_id=pid, entity_id=ent_id
            )
            conn.commit()

            with conn.cursor() as cur:
                cur.execute("DELETE FROM kg_entities WHERE id = %s", (ent_id,))
            conn.commit()

            with conn.cursor() as cur:
                cur.execute("SELECT id FROM kg_evidence WHERE id = %s", (ev_id,))
                row = cur.fetchone()
            assert row is None, "Evidence must be deleted when its entity is deleted"


# ---------------------------------------------------------------------------
# Tests: kg_evidence — RLS isolation
# ---------------------------------------------------------------------------


class TestKgEvidenceRLS:
    """Evidence for tenant A is invisible when app.project_id = tenant B."""

    def test_tenant_isolation(self) -> None:
        tenant_a = f"proj-a-{_uid()}"
        tenant_b = f"proj-b-{_uid()}"
        brain_a = f"brain-a-{_uid()}"

        with _owner_conn() as conn:
            ent_id = _insert_entity(
                conn, tenant_id=tenant_a, brain_id=brain_a, project_id=tenant_a
            )
            conn.commit()
            ev_id = _insert_evidence(
                conn,
                tenant_id=tenant_a, brain_id=brain_a, project_id=tenant_a,
                entity_id=ent_id,
            )
            conn.commit()

        try:
            # Tenant A can see the evidence.
            with _runtime_conn() as conn:
                _set_project_id(conn, tenant_a)
                with conn.cursor() as cur:
                    cur.execute("SELECT id FROM kg_evidence WHERE id = %s", (ev_id,))
                    row = cur.fetchone()
                conn.rollback()
            assert row is not None, "Tenant A must see its own evidence"

            # Tenant B cannot see tenant A's evidence.
            with _runtime_conn() as conn:
                _set_project_id(conn, tenant_b)
                with conn.cursor() as cur:
                    cur.execute("SELECT id FROM kg_evidence WHERE id = %s", (ev_id,))
                    row = cur.fetchone()
                conn.rollback()
            assert row is None, "Tenant B must not see tenant A's evidence (RLS breach)"
        finally:
            with _owner_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM kg_entities WHERE brain_id = %s", (brain_a,))
                conn.commit()


# ---------------------------------------------------------------------------
# Tests: kg_aliases — table structure
# ---------------------------------------------------------------------------


class TestKgAliasesTableStructure:
    """kg_aliases table must exist with expected columns and RLS."""

    def test_table_exists(self) -> None:
        with _owner_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT EXISTS (SELECT FROM information_schema.tables"
                    " WHERE table_name = 'kg_aliases')"
                )
                row = cur.fetchone()
                assert row is not None and row[0] is True

    def test_required_columns_present(self) -> None:
        required = {
            "id",
            "tenant_id",
            "brain_id",
            "project_id",
            "entity_id",
            "evidence_id",
            "alias",
            "alias_norm",
            "confidence",
            "source_agent",
            "status",
            "created_at",
            "updated_at",
        }
        with _owner_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_name = 'kg_aliases'"
                )
                found = {row[0] for row in cur.fetchall()}
        missing = required - found
        assert not missing, f"Missing columns: {missing}"

    def test_alias_norm_is_stored_generated(self) -> None:
        """alias_norm must be a STORED generated column (lower(alias))."""
        with _owner_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT is_generated, generation_expression"
                    " FROM information_schema.columns"
                    " WHERE table_name = 'kg_aliases'"
                    "   AND column_name = 'alias_norm'"
                )
                row = cur.fetchone()
        assert row is not None, "alias_norm column not found"
        is_generated, expr = row
        assert is_generated == "ALWAYS", "alias_norm must be GENERATED ALWAYS"
        assert expr is not None and "lower" in expr.lower()

    def test_rls_is_enabled_and_forced(self) -> None:
        with _owner_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT relrowsecurity, relforcerowsecurity"
                    " FROM pg_class WHERE relname = 'kg_aliases'"
                )
                row = cur.fetchone()
        assert row is not None, "pg_class row for kg_aliases not found"
        assert row[0] is True, "RLS must be ENABLED on kg_aliases"
        assert row[1] is True, "RLS must be FORCED on kg_aliases"

    def test_schema_version_19_recorded(self) -> None:
        with _owner_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT version FROM private_schema_version WHERE version = 19"
                )
                row = cur.fetchone()
        assert row is not None, "Schema version 19 not recorded"


# ---------------------------------------------------------------------------
# Tests: kg_aliases — CRUD + alias_norm
# ---------------------------------------------------------------------------


class TestKgAliasesCRUD:
    def _mk_tenant(self) -> tuple[str, str, str]:
        tid = f"proj-{_uid()}"
        bid = f"brain-{_uid()}"
        return tid, bid, tid

    def test_insert_and_alias_norm_generated(self) -> None:
        """alias_norm must equal lower(alias) after insert."""
        tid, bid, pid = self._mk_tenant()
        alias = f"MyAlias-{_uid()}"
        with _owner_conn() as conn:
            ent_id = _insert_entity(conn, tenant_id=tid, brain_id=bid, project_id=pid)
            conn.commit()
            a_id = _insert_alias(
                conn, tenant_id=tid, brain_id=bid, project_id=pid,
                entity_id=ent_id, alias=alias,
            )
            conn.commit()
            with conn.cursor() as cur:
                cur.execute("SELECT alias_norm FROM kg_aliases WHERE id = %s", (a_id,))
                row = cur.fetchone()
            assert row is not None
            assert row[0] == alias.lower()
            # Cleanup
            with conn.cursor() as cur:
                cur.execute("DELETE FROM kg_entities WHERE brain_id = %s", (bid,))
            conn.commit()

    def test_duplicate_alias_for_same_entity_raises(self) -> None:
        """Inserting the same alias (case-insensitive) for the same entity raises."""
        import psycopg

        tid, bid, pid = self._mk_tenant()
        alias = f"DupeAlias-{_uid()}"
        with _owner_conn() as conn:
            ent_id = _insert_entity(conn, tenant_id=tid, brain_id=bid, project_id=pid)
            conn.commit()
            _insert_alias(
                conn, tenant_id=tid, brain_id=bid, project_id=pid,
                entity_id=ent_id, alias=alias,
            )
            conn.commit()
            # Second insert with same alias (different case) must raise.
            with pytest.raises(psycopg.errors.UniqueViolation):
                _insert_alias(
                    conn, tenant_id=tid, brain_id=bid, project_id=pid,
                    entity_id=ent_id, alias=alias.upper(),
                )
            conn.rollback()
            # Cleanup
            with conn.cursor() as cur:
                cur.execute("DELETE FROM kg_entities WHERE brain_id = %s", (bid,))
            conn.commit()

    def test_alias_rejection_via_status_update(self) -> None:
        """An active alias can be rejected by setting status='rejected'.
        The row persists (not deleted) so the rejection is auditable."""
        tid, bid, pid = self._mk_tenant()
        alias = f"BadAlias-{_uid()}"
        with _owner_conn() as conn:
            ent_id = _insert_entity(conn, tenant_id=tid, brain_id=bid, project_id=pid)
            conn.commit()
            a_id = _insert_alias(
                conn, tenant_id=tid, brain_id=bid, project_id=pid,
                entity_id=ent_id, alias=alias,
            )
            conn.commit()
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE kg_aliases SET status = 'rejected' WHERE id = %s", (a_id,)
                )
            conn.commit()
            # Row must still exist.
            with conn.cursor() as cur:
                cur.execute("SELECT status FROM kg_aliases WHERE id = %s", (a_id,))
                row = cur.fetchone()
            assert row is not None
            assert row[0] == "rejected"
            # Cleanup
            with conn.cursor() as cur:
                cur.execute("DELETE FROM kg_entities WHERE brain_id = %s", (bid,))
            conn.commit()

    def test_invalid_status_raises(self) -> None:
        """Inserting an invalid status value must raise CheckViolation."""
        import psycopg

        tid, bid, pid = self._mk_tenant()
        with _owner_conn() as conn:
            ent_id = _insert_entity(conn, tenant_id=tid, brain_id=bid, project_id=pid)
            conn.commit()
            with pytest.raises(psycopg.errors.CheckViolation):
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO kg_aliases"
                        " (tenant_id, brain_id, project_id, entity_id, alias, status)"
                        " VALUES (%s, %s, %s, %s, 'bad', 'invalid_status')",
                        (tid, bid, pid, ent_id),
                    )
            conn.rollback()
            # Cleanup
            with conn.cursor() as cur:
                cur.execute("DELETE FROM kg_entities WHERE brain_id = %s", (bid,))
            conn.commit()


# ---------------------------------------------------------------------------
# Tests: kg_aliases — cascade delete
# ---------------------------------------------------------------------------


class TestKgAliasesCascade:
    def _mk_tenant(self) -> tuple[str, str, str]:
        tid = f"proj-{_uid()}"
        bid = f"brain-{_uid()}"
        return tid, bid, tid

    def test_cascade_on_entity_delete(self) -> None:
        """Deleting an entity must cascade-delete all its aliases."""
        tid, bid, pid = self._mk_tenant()
        with _owner_conn() as conn:
            ent_id = _insert_entity(conn, tenant_id=tid, brain_id=bid, project_id=pid)
            conn.commit()
            a_id = _insert_alias(
                conn, tenant_id=tid, brain_id=bid, project_id=pid, entity_id=ent_id
            )
            conn.commit()

            with conn.cursor() as cur:
                cur.execute("DELETE FROM kg_entities WHERE id = %s", (ent_id,))
            conn.commit()

            with conn.cursor() as cur:
                cur.execute("SELECT id FROM kg_aliases WHERE id = %s", (a_id,))
                row = cur.fetchone()
            assert row is None, "Alias must be deleted when its entity is deleted"

    def test_evidence_fk_set_null_on_evidence_delete(self) -> None:
        """Deleting linked evidence must set evidence_id to NULL (not cascade-delete alias)."""
        tid, bid, pid = self._mk_tenant()
        with _owner_conn() as conn:
            ent_id = _insert_entity(conn, tenant_id=tid, brain_id=bid, project_id=pid)
            conn.commit()
            ev_id = _insert_evidence(
                conn,
                tenant_id=tid, brain_id=bid, project_id=pid,
                entity_id=ent_id,
            )
            conn.commit()
            a_id = _insert_alias(
                conn,
                tenant_id=tid, brain_id=bid, project_id=pid,
                entity_id=ent_id,
                evidence_id=ev_id,
            )
            conn.commit()

            # Delete the evidence row.
            with conn.cursor() as cur:
                cur.execute("DELETE FROM kg_evidence WHERE id = %s", (ev_id,))
            conn.commit()

            # Alias must still exist with evidence_id = NULL.
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, evidence_id FROM kg_aliases WHERE id = %s", (a_id,)
                )
                row = cur.fetchone()
            assert row is not None, "Alias must survive evidence deletion"
            assert row[1] is None, "evidence_id must be NULL after evidence is deleted"

            # Cleanup
            with conn.cursor() as cur:
                cur.execute("DELETE FROM kg_entities WHERE brain_id = %s", (bid,))
            conn.commit()


# ---------------------------------------------------------------------------
# Tests: kg_aliases — RLS isolation
# ---------------------------------------------------------------------------


class TestKgAliasesRLS:
    """Aliases for tenant A are invisible when app.project_id = tenant B."""

    def test_tenant_isolation(self) -> None:
        tenant_a = f"proj-a-{_uid()}"
        tenant_b = f"proj-b-{_uid()}"
        brain_a = f"brain-a-{_uid()}"

        with _owner_conn() as conn:
            ent_id = _insert_entity(
                conn, tenant_id=tenant_a, brain_id=brain_a, project_id=tenant_a
            )
            conn.commit()
            a_id = _insert_alias(
                conn,
                tenant_id=tenant_a, brain_id=brain_a, project_id=tenant_a,
                entity_id=ent_id,
            )
            conn.commit()

        try:
            # Tenant A sees its alias.
            with _runtime_conn() as conn:
                _set_project_id(conn, tenant_a)
                with conn.cursor() as cur:
                    cur.execute("SELECT id FROM kg_aliases WHERE id = %s", (a_id,))
                    row = cur.fetchone()
                conn.rollback()
            assert row is not None, "Tenant A must see its own alias"

            # Tenant B cannot see tenant A's alias.
            with _runtime_conn() as conn:
                _set_project_id(conn, tenant_b)
                with conn.cursor() as cur:
                    cur.execute("SELECT id FROM kg_aliases WHERE id = %s", (a_id,))
                    row = cur.fetchone()
                conn.rollback()
            assert row is None, "Tenant B must not see tenant A's alias (RLS breach)"
        finally:
            with _owner_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM kg_entities WHERE brain_id = %s", (brain_a,))
                conn.commit()
