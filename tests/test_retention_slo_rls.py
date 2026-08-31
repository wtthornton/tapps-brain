"""SLO 1 must see the table it grades (TAP-6698, Ruling 15).

``private_memories`` has FORCED, fail-closed row-level security with **no admin
bypass** (``migrations/private/009_project_rls.sql``): a row is visible only when
``current_setting('app.project_id')`` names its tenant.  The deployed service
connects as ``tapps_runtime``, a non-superuser with ``rolbypassrls = false``, and
the retention probe never set that variable — so SLO 1 ran a correct query
against an empty view of the table and reported ``ok=True, violating_total=0``.
Measured live on 2026-08-31: 0 under the runtime role, 6,047 for the identical
predicate under a bypassing role.

Everything in this file therefore runs under a **deliberately non-bypassing
role**, created in the disposable fixture database.  A test that grades this
behaviour from a superuser connection cannot fail — RLS simply does not apply —
which is exactly how the defect survived a green test suite.

The controls are the point:

* ``test_old_unscoped_query_is_blind_under_rls`` reproduces the false green, so
  the fixture is proven capable of hiding rows before any assertion trusts it.
* ``test_per_tenant_scan_matches_an_unlimited_count`` compares the scan against
  an independent count taken under the bypassing owner — the two must agree.
* ``test_setting_does_not_leak_between_tenants`` and
  ``test_setting_is_gone_after_the_scan`` cover the risk the fix introduces.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from tapps_brain.services import retention_slo
from tests._pg_fixture import ensure_rls_role, resolve_fixture_dsn

_RLS_ROLE = "tap6698_rls_probe"
_RLS_PASSWORD = "tap6698-fixture-only"  # disposable fixture container, never a real credential
_TENANT_A = "tap-6698-rls-tenant-a"
_TENANT_B = "tap-6698-rls-tenant-b"
_AGENT = "rls-probe-agent"
#: ``project_profiles.profile`` is JSONB; the scan only reads ``project_id``,
#: so the smallest valid document is enough to make the tenant discoverable.
_PROFILE_JSON = '{"name": "repo-brain"}'

#: 2 x the ``context`` half-life is 28 days; 40 is unambiguously past it.
_OVERDUE_AGE = timedelta(days=40)


@pytest.fixture(scope="module")
def owner_dsn() -> str:
    return resolve_fixture_dsn()


@pytest.fixture(scope="module")
def rls_dsn(owner_dsn: str) -> str:
    """DSN for a role that RLS actually applies to.

    The fixture database's default user is a superuser, and a superuser bypasses
    every policy — so a test that connected with it would be grading nothing.
    This creates a plain ``LOGIN`` role (no ``BYPASSRLS``, not the table owner),
    which is the shape ``tapps_runtime`` has in the deployed cluster.
    """
    return ensure_rls_role(owner_dsn, role=_RLS_ROLE, password=_RLS_PASSWORD)


@pytest.fixture(scope="module")
def seeded_tenants(owner_dsn: str) -> list[str]:
    """Two tenants with one overdue row each, plus their registry rows.

    The ``project_profiles`` inserts matter: they are what makes the tenants
    *discoverable* by :func:`retention_slo.list_known_tenants`.  A tenant with
    rows and no entry in any enumerable source table is invisible to the scan —
    the residual blind spot this fix narrows but cannot close, asserted
    explicitly by ``test_a_tenant_absent_from_every_source_is_not_scanned``.
    """
    suffix = uuid.uuid4().hex[:8]
    keys = [f"rls-overdue-{suffix}-a", f"rls-overdue-{suffix}-b"]
    old_ts = datetime.now(UTC) - _OVERDUE_AGE
    with psycopg.connect(owner_dsn) as conn:
        with conn.cursor() as cur:
            for tenant, key in zip((_TENANT_A, _TENANT_B), keys, strict=True):
                cur.execute(
                    "INSERT INTO project_profiles (project_id, profile) "
                    "VALUES (%s, %s::jsonb) ON CONFLICT (project_id) DO NOTHING",
                    (tenant, _PROFILE_JSON),
                )
                cur.execute(
                    "INSERT INTO private_memories "
                    "(project_id, agent_id, key, value, tier, updated_at) "
                    "VALUES (%s, %s, %s, 'v', 'context', %s)",
                    (tenant, _AGENT, key, old_ts),
                )
        conn.commit()
    yield keys
    with psycopg.connect(owner_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM private_memories WHERE project_id = ANY(%s)",
                ([_TENANT_A, _TENANT_B],),
            )
            cur.execute(
                "DELETE FROM project_profiles WHERE project_id = ANY(%s)",
                ([_TENANT_A, _TENANT_B],),
            )
        conn.commit()


@pytest.fixture()
def rls_conn(rls_dsn: str):
    with psycopg.connect(rls_dsn) as conn:
        yield conn


_OLD_UNSCOPED_SQL = """
    WITH tier_half_life(tier, half_life_days) AS (
        VALUES ('context', 14::double precision))
    SELECT count(*)
    FROM private_memories pm
    LEFT JOIN tier_half_life t ON t.tier = pm.tier
    WHERE pm.status = 'active'
      AND (t.tier IS NULL
           OR EXTRACT(EPOCH FROM (now() - COALESCE(pm.last_reinforced, pm.updated_at)))
                  / 86400.0 > 2 * t.half_life_days)
"""


class TestRlsHidesTheTableFromTheProbe:
    def test_role_under_test_really_is_subject_to_rls(self, rls_conn) -> None:
        """Guard the guard: if this role bypassed RLS, every test below is vacuous."""
        with rls_conn.cursor() as cur:
            cur.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            is_super, bypasses = cur.fetchone()
        assert is_super is False
        assert bypasses is False

    def test_old_unscoped_query_is_blind_under_rls(self, rls_conn, seeded_tenants) -> None:
        """The false green, reproduced: a correct predicate over an empty view."""
        with rls_conn.cursor() as cur:
            cur.execute(_OLD_UNSCOPED_SQL)
            assert cur.fetchone()[0] == 0
        rls_conn.rollback()


class TestPerTenantScanSeesTheRows:
    def test_per_tenant_scan_matches_an_unlimited_count(
        self, rls_conn, owner_dsn, seeded_tenants
    ) -> None:
        """The by-effect assertion: same role, same data, count now agrees with truth.

        The reference figure is taken through a *separate* connection as the
        bypassing owner, so the scan is graded against something it did not
        produce.
        """
        result = retention_slo.check_no_overdue_active_rows(
            rls_conn, project_ids=[_TENANT_A, _TENANT_B]
        )
        with psycopg.connect(owner_dsn) as ref, ref.cursor() as cur:
            cur.execute(
                _OLD_UNSCOPED_SQL + " AND pm.project_id = ANY(%s)",
                ([_TENANT_A, _TENANT_B],),
            )
            unlimited_total = cur.fetchone()[0]
        assert unlimited_total == len(seeded_tenants)
        assert result["violating_total"] == unlimited_total
        assert result["ok"] is False
        assert {v["key"] for v in result["violations"]} == set(seeded_tenants)

    def test_totals_are_not_multiplied_by_the_tenant_count(self, owner_dsn, seeded_tenants) -> None:
        """The scan must stay exact for a role RLS does *not* constrain.

        ``SET LOCAL app.project_id`` is a no-op for a bypassing role, so without
        the explicit ``project_id = %s`` predicate every iteration would count
        the whole table and the total would come out multiplied by the number of
        tenants.  CI's fixture role is exactly such a role, which is how this
        would have shipped unnoticed.
        """
        with psycopg.connect(owner_dsn) as conn:
            result = retention_slo.check_no_overdue_active_rows(
                conn, project_ids=[_TENANT_A, _TENANT_B]
            )
        assert result["violating_total"] == len(seeded_tenants)

    def test_a_tenant_absent_from_every_source_is_not_scanned(
        self, rls_conn, seeded_tenants
    ) -> None:
        """Name the residual blind spot instead of pretending it is closed.

        The tenant list is a superset heuristic over enumerable tables; a tenant
        that appears in none of them keeps its rows out of SLO 1's view.  The
        scan reports ``rows_scanned`` so an operator can compare it against the
        catalog estimate, but it cannot invent rows it was never pointed at.
        """
        result = retention_slo.check_no_overdue_active_rows(rls_conn, project_ids=[_TENANT_A])
        assert result["violating_total"] == 1
        assert result["tenants_scanned"] == 1


class TestTenantSettingCannotLeak:
    def test_setting_does_not_leak_between_tenants(self, rls_conn, seeded_tenants) -> None:
        """Tenant A's identity must not still be in force while B is counted.

        If it leaked, scanning ``[A, A, A]`` and ``[A, B]`` would be
        indistinguishable — both would count A three times or A twice.  Scanning
        A twice must double A's count and nothing else.
        """
        one = retention_slo.check_no_overdue_active_rows(rls_conn, project_ids=[_TENANT_A])
        both = retention_slo.check_no_overdue_active_rows(
            rls_conn, project_ids=[_TENANT_A, _TENANT_B]
        )
        assert one["violating_total"] == 1
        assert both["violating_total"] == 2
        assert {v["project_id"] for v in both["violations"]} == {_TENANT_A, _TENANT_B}

    def test_setting_is_gone_after_the_scan(self, rls_conn, seeded_tenants) -> None:
        """The connection must go back to seeing nothing once the scan returns."""
        retention_slo.check_no_overdue_active_rows(rls_conn, project_ids=[_TENANT_A, _TENANT_B])
        with rls_conn.cursor() as cur:
            cur.execute(
                "SELECT current_setting('app.project_id', TRUE), count(*) FROM private_memories"
            )
            setting, visible = cur.fetchone()
        rls_conn.rollback()
        assert not setting
        assert visible == 0

    def test_autocommit_connection_fails_loudly(self, rls_dsn) -> None:
        """A silent zero is the failure mode this whole fix exists to kill.

        ``SET LOCAL`` outside a transaction is a no-op Postgres only warns about,
        so an autocommit connection would scan every tenant with no identity and
        report a clean 0 — the original bug, wearing the fix's clothes.
        """
        with psycopg.connect(rls_dsn, autocommit=True) as conn:
            with pytest.raises(RuntimeError, match="did not take effect"):
                retention_slo.check_no_overdue_active_rows(conn, project_ids=[_TENANT_A])


class TestTenantDiscovery:
    def test_registered_tenants_are_discovered_without_a_memory_bypass(
        self, rls_conn, seeded_tenants
    ) -> None:
        """``project_profiles`` is read under its own admin bypass, not one on memories.

        Migration 009 gives ``project_profiles`` an ``app.is_admin`` bypass for
        registry bookkeeping and deliberately gives ``private_memories`` none.
        The scan uses the first and never asks for the second.
        """
        tenants = retention_slo.list_known_tenants(rls_conn)
        assert _TENANT_A in tenants
        assert _TENANT_B in tenants

    def test_discovery_does_not_leave_the_admin_flag_set(self, rls_conn, seeded_tenants) -> None:
        retention_slo.list_known_tenants(rls_conn)
        with rls_conn.cursor() as cur:
            cur.execute("SELECT current_setting('app.is_admin', TRUE)")
            flag = cur.fetchone()[0]
        rls_conn.rollback()
        assert not flag

    def test_default_scan_finds_the_seeded_tenants(self, rls_conn, seeded_tenants) -> None:
        """End to end: no explicit tenant list, and the rows are still counted."""
        result = retention_slo.check_no_overdue_active_rows(rls_conn)
        assert result["violating_total"] >= len(seeded_tenants)
        assert {v["key"] for v in result["violations"]} >= set(seeded_tenants)
        assert result["tenants_scanned"] >= 2
        assert result["rows_scanned"] >= len(seeded_tenants)
