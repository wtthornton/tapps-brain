"""Live-Postgres tenant-isolation tests for the KG predicate registry (TAP-5508).

Verifies that the RLS policy shipped in
``migrations/private/032_kg_predicate_registry.sql`` actually blocks
cross-tenant access at the database layer — not just in application code.

This matters more than the usual RLS test because `kg_predicates` is a
*governance* table: a predicate declaration says "this edge may hold at most N
objects", and TAP-5510 enforces it on the write path. One tenant reading or
overwriting another's declaration would let it silently change the invariants a
different project's ledger is enforcing.

Requires ``TAPPS_TEST_POSTGRES_DSN`` pointing at a live Postgres; the whole
module skips otherwise — the same pattern as ``test_tenant_isolation.py`` and
``test_rls_spike.py``.

The DSN is expected to connect as ``tapps:tapps`` (table owner / migrator).
RLS is only enforced against a **non-superuser, non-owner** identity, so the
isolation assertions swap in ``tapps_runtime:tapps_runtime``. A superuser
bypasses RLS even under FORCE, so running these as the owner would pass
vacuously — which is exactly the trap this module exists to avoid.

Policy contract under test
--------------------------
Fail-closed. ``USING``/``WITH CHECK`` require ``app.project_id`` to be set AND
equal to the row's ``tenant_id``. Missing session var → zero rows visible.
Cross-tenant read → zero rows. Cross-tenant UPDATE/DELETE → zero rows affected.

Uniqueness is per ``(tenant_id, brain_id, predicate)``, so two tenants may
declare the *same predicate name* with *different* cardinality without
colliding.
"""

from __future__ import annotations

import os
import uuid

import pytest

_PG_DSN = os.environ.get("TAPPS_TEST_POSTGRES_DSN", "")
_SKIP_PG = not _PG_DSN

#: Non-superuser role — RLS is only enforced against this identity.
_RUNTIME_DSN = _PG_DSN.replace("tapps:tapps@", "tapps_runtime:tapps_runtime@", 1) if _PG_DSN else ""

pytestmark = pytest.mark.skipif(_SKIP_PG, reason="TAPPS_TEST_POSTGRES_DSN not set")

_BRAIN_ID = "isolation-test-brain"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _owner_conn() -> object:
    """Raw connection as the owner/migrator — used to seed cross-tenant rows."""
    import psycopg

    return psycopg.connect(_PG_DSN)


def _runtime_conn() -> object:
    """Raw connection as tapps_runtime — subject to RLS."""
    import psycopg

    return psycopg.connect(_RUNTIME_DSN)


def _unique_tenant() -> str:
    return f"tenant-{uuid.uuid4().hex[:8]}"


def _seed_predicate(tenant_id: str, predicate: str, max_count: int | None) -> None:
    """Insert a declaration as the owner, bypassing RLS.

    Seeding as the owner is deliberate: the point is to create data belonging
    to a tenant that the runtime role must then be unable to reach.
    """
    with _owner_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO kg_predicates
                (tenant_id, brain_id, project_id, predicate, max_count, registered_by)
            VALUES (%s, %s, %s, %s, %s, 'isolation-test')
            ON CONFLICT (tenant_id, brain_id, predicate) DO UPDATE SET
                max_count = EXCLUDED.max_count
            """,
            (tenant_id, _BRAIN_ID, tenant_id, predicate, max_count),
        )
        conn.commit()


def _as_tenant(tenant_id: str | None, sql: str, params: tuple[object, ...] = ()) -> list[tuple]:
    """Run *sql* as tapps_runtime with ``app.project_id`` set to *tenant_id*."""
    with _runtime_conn() as conn, conn.cursor() as cur:
        if tenant_id is not None:
            cur.execute("SELECT set_config('app.project_id', %s, false)", (tenant_id,))
        cur.execute(sql, params)
        rows = cur.fetchall() if cur.description is not None else []
        conn.commit()
        return rows


def _rowcount_as_tenant(tenant_id: str, sql: str, params: tuple[object, ...]) -> int:
    """Run a write as *tenant_id* and return how many rows it actually touched."""
    with _runtime_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.project_id', %s, false)", (tenant_id,))
        cur.execute(sql, params)
        affected = cur.rowcount
        conn.commit()
        return affected


@pytest.fixture
def two_tenants() -> tuple[str, str, str]:
    """Seed the same predicate for two tenants with different cardinality."""
    tenant_a = _unique_tenant()
    tenant_b = _unique_tenant()
    predicate = f"refunded_{uuid.uuid4().hex[:6]}"
    _seed_predicate(tenant_a, predicate, 1)
    _seed_predicate(tenant_b, predicate, 99)
    return tenant_a, tenant_b, predicate


# ---------------------------------------------------------------------------
# Read isolation
# ---------------------------------------------------------------------------


def test_tenant_sees_only_its_own_declaration(two_tenants: tuple[str, str, str]) -> None:
    tenant_a, _tenant_b, predicate = two_tenants
    rows = _as_tenant(
        tenant_a,
        "SELECT tenant_id, max_count FROM kg_predicates WHERE predicate = %s",
        (predicate,),
    )
    assert rows == [(tenant_a, 1)]


def test_same_predicate_name_holds_different_cardinality_per_tenant(
    two_tenants: tuple[str, str, str],
) -> None:
    """The load-bearing claim: one tenant's limit is not another's.

    If the unique index or the policy were not tenant-scoped, tenant B would
    read tenant A's ``max_count`` of 1 and start rejecting writes it should
    allow — a cross-tenant change to an enforced invariant.
    """
    tenant_a, tenant_b, predicate = two_tenants
    a_rows = _as_tenant(
        tenant_a, "SELECT max_count FROM kg_predicates WHERE predicate = %s", (predicate,)
    )
    b_rows = _as_tenant(
        tenant_b, "SELECT max_count FROM kg_predicates WHERE predicate = %s", (predicate,)
    )
    assert a_rows == [(1,)]
    assert b_rows == [(99,)]


def test_missing_tenant_context_sees_nothing(two_tenants: tuple[str, str, str]) -> None:
    """Fail-closed: an unset ``app.project_id`` must match no rows, not all rows."""
    _tenant_a, _tenant_b, predicate = two_tenants
    rows = _as_tenant(None, "SELECT count(*) FROM kg_predicates WHERE predicate = %s", (predicate,))
    assert rows == [(0,)]


# ---------------------------------------------------------------------------
# Write isolation
# ---------------------------------------------------------------------------


def test_cross_tenant_update_touches_nothing(two_tenants: tuple[str, str, str]) -> None:
    tenant_a, tenant_b, predicate = two_tenants
    affected = _rowcount_as_tenant(
        tenant_b,
        "UPDATE kg_predicates SET max_count = 999 WHERE tenant_id = %s AND predicate = %s",
        (tenant_a, predicate),
    )
    assert affected == 0

    surviving = _as_tenant(
        tenant_a, "SELECT max_count FROM kg_predicates WHERE predicate = %s", (predicate,)
    )
    assert surviving == [(1,)], "tenant A's declared cardinality was modified by tenant B"


def test_cross_tenant_delete_touches_nothing(two_tenants: tuple[str, str, str]) -> None:
    tenant_a, tenant_b, predicate = two_tenants
    affected = _rowcount_as_tenant(
        tenant_b,
        "DELETE FROM kg_predicates WHERE tenant_id = %s AND predicate = %s",
        (tenant_a, predicate),
    )
    assert affected == 0

    surviving = _as_tenant(
        tenant_a, "SELECT count(*) FROM kg_predicates WHERE predicate = %s", (predicate,)
    )
    assert surviving == [(1,)], "tenant A's declaration was deleted by tenant B"


def test_cannot_insert_a_row_for_another_tenant(two_tenants: tuple[str, str, str]) -> None:
    """WITH CHECK must reject a row whose tenant_id is not the caller's."""
    import psycopg

    tenant_a, tenant_b, _predicate = two_tenants
    with pytest.raises(psycopg.errors.Error):
        _rowcount_as_tenant(
            tenant_b,
            """
            INSERT INTO kg_predicates
                (tenant_id, brain_id, project_id, predicate, max_count, registered_by)
            VALUES (%s, %s, %s, 'smuggled', 1, 'attacker')
            """,
            (tenant_a, _BRAIN_ID, tenant_a),
        )


# ---------------------------------------------------------------------------
# Policy shape
# ---------------------------------------------------------------------------


def test_rls_is_enabled_and_forced_on_the_table() -> None:
    """FORCE matters: without it the table owner silently bypasses isolation."""
    with _owner_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT relrowsecurity, relforcerowsecurity "
            "FROM pg_class WHERE relname = 'kg_predicates'"
        )
        row = cur.fetchone()
    assert row == (True, True), f"expected RLS enabled and forced, got {row}"
