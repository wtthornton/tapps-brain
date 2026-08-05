"""Unit tests — TAP-5460: roles/001_db_roles.sql must grant schema-wide.

``migrations/roles/001_db_roles.sql`` documents its own position as step 4,
*after* every schema migration.  That ordering makes an explicit table list
unsafe: by the time the file runs, the tables already exist, and the
``ALTER DEFAULT PRIVILEGES`` in section 3 cannot reach them — default
privileges only affect objects created *after* they are declared.

The file used to enumerate the 13 migration-001 tables.  The private schema is
now at migration 029 (~43 tables overall), so every table added by migrations
002-029 received no grant at all, and a ``tapps_runtime`` provisioned by the
documented order failed with ``permission denied`` on the first one it touched.

These assertions are static because the DB-backed integration suite is skipped
unless ``TAPPS_TEST_POSTGRES_DSN`` is set — which is the gap EPIC TAP-5459 is
about.  A static guard runs in CI today; it catches a regression to a
hardcoded list, which is the specific mistake that caused TAP-5460.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROLES_SQL = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "tapps_brain"
    / "migrations"
    / "roles"
    / "001_db_roles.sql"
)

# Tables that only ever existed in the migration-001 era.  If the runtime grant
# names one of these explicitly, the hardcoded-list regression is back.
_MIGRATION_001_TABLES = (
    "hive_memories",
    "hive_groups",
    "federated_memories",
    "private_memories",
)


@pytest.fixture(scope="module")
def roles_sql() -> str:
    assert _ROLES_SQL.is_file(), f"roles migration not found at {_ROLES_SQL}"
    return _ROLES_SQL.read_text(encoding="utf-8")


def _grant_statements(sql: str) -> list[str]:
    """Return each GRANT statement as one whitespace-collapsed string."""
    return [" ".join(m.split()) for m in re.findall(r"GRANT\b.*?;", sql, re.DOTALL)]


def test_runtime_receives_dml_on_all_tables(roles_sql: str) -> None:
    grants = _grant_statements(roles_sql)
    assert any(
        "ON ALL TABLES IN SCHEMA public TO tapps_runtime" in g
        and all(priv in g for priv in ("SELECT", "INSERT", "UPDATE", "DELETE"))
        for g in grants
    ), "tapps_runtime needs schema-wide DML; an explicit table list goes stale"


def test_readonly_receives_select_on_all_tables(roles_sql: str) -> None:
    grants = _grant_statements(roles_sql)
    assert any(
        "ON ALL TABLES IN SCHEMA public TO tapps_readonly" in g and "SELECT" in g for g in grants
    ), "tapps_readonly needs schema-wide SELECT"


def test_runtime_receives_sequence_usage(roles_sql: str) -> None:
    """Without USAGE the runtime role can read a table but every INSERT fails.

    ``audit_log.id`` is a bigserial, so this is load-bearing, not theoretical.
    """
    grants = _grant_statements(roles_sql)
    assert any(
        "ON ALL SEQUENCES IN SCHEMA public TO tapps_runtime" in g and "USAGE" in g for g in grants
    ), "tapps_runtime needs USAGE on sequences or inserts fail"


def test_runtime_grant_does_not_enumerate_tables(roles_sql: str) -> None:
    """The regression guard: no migration-001 table named in a runtime GRANT."""
    offenders = [
        (table, grant)
        for grant in _grant_statements(roles_sql)
        if "tapps_runtime" in grant
        for table in _MIGRATION_001_TABLES
        if re.search(rf"\b{table}\b", grant)
    ]
    assert not offenders, (
        "runtime GRANT enumerates specific tables, which goes stale as "
        f"migrations are added (TAP-5460): {offenders}"
    )


def test_runtime_holds_no_ddl_rights(roles_sql: str) -> None:
    """Least privilege: the runtime role must never be able to CREATE."""
    normalized = " ".join(roles_sql.split())
    assert "REVOKE CREATE ON SCHEMA public FROM tapps_runtime" in normalized
    assert not re.search(r"GRANT[^;]*\bCREATE\b[^;]*TO tapps_runtime", normalized), (
        "tapps_runtime must not be granted CREATE"
    )
