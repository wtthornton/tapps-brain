"""Privileged-role guard vs FORCE ROW LEVEL SECURITY (TAP-2673).

The guard refuses to start as a role that can bypass tenant RLS.  Owning a
tenanted table only bypasses RLS when the table does NOT have FORCE ROW LEVEL
SECURITY — with FORCE on (migration 012), the owner is subject to the policies
like any role.  These tests prove the guard treats a de-privileged
``NOSUPERUSER NOBYPASSRLS`` owner of FORCE-RLS tables as non-privileged (so the
migrate sidecar can eventually drop the override flag) while still flagging an
owner of a non-FORCE table.

Requires: ``TAPPS_BRAIN_DATABASE_URL`` (owner DSN) + a Postgres where we can
create a login role. Skipped otherwise. Mark: ``requires_postgres``.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlparse, urlunparse

import pytest

pytestmark = pytest.mark.requires_postgres

_PG_DSN = os.environ.get("TAPPS_BRAIN_DATABASE_URL", "")
_MIGRATOR_ROLE = "tb_guard_probe_migrator"
_MIGRATOR_PW = "probe-pw"  # nosec B105 - throwaway test-only credential


def _migrator_dsn() -> str:
    parts = urlparse(_PG_DSN)
    netloc = f"{_MIGRATOR_ROLE}:{_MIGRATOR_PW}@{parts.hostname}:{parts.port}"
    return urlunparse(parts._replace(netloc=netloc))


@contextlib.contextmanager
def _owner_override() -> Iterator[None]:
    """Scope the privileged-role override to owner-side setup only.

    The owner DSN connects as a superuser, which the guard rejects without the
    override.  We must NOT leak the override into the migrator connection (that
    is the connection under test), so set it locally and restore on exit.
    """
    prev = os.environ.get("TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE")
    os.environ["TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE"] = "1"
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE", None)
        else:
            os.environ["TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE"] = prev


def _exec_as_owner(statements: list[str]) -> None:
    from tapps_brain.postgres_connection import PostgresConnectionManager

    with _owner_override():
        cm = PostgresConnectionManager(_PG_DSN)
        try:
            with cm.get_connection() as conn, conn.cursor() as cur:
                for stmt in statements:
                    cur.execute(stmt)
                conn.commit()
        finally:
            cm.close()


def _apply_migrations() -> None:
    from tapps_brain.postgres_migrations import apply_private_migrations

    with _owner_override():
        apply_private_migrations(_PG_DSN)


@pytest.fixture
def _migrator_owns_tenant_tables() -> Any:
    """Create a de-privileged login role that owns the tenanted tables."""
    _apply_migrations()
    _exec_as_owner(
        [
            f"DROP ROLE IF EXISTS {_MIGRATOR_ROLE}",
            f"CREATE ROLE {_MIGRATOR_ROLE} LOGIN PASSWORD '{_MIGRATOR_PW}' "
            "NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE",
            f"ALTER TABLE private_memories OWNER TO {_MIGRATOR_ROLE}",
            f"ALTER TABLE project_profiles OWNER TO {_MIGRATOR_ROLE}",
        ]
    )
    yield
    # Restore ownership to the original owner and drop the probe role.
    owner = urlparse(_PG_DSN).username
    _exec_as_owner(
        [
            f"ALTER TABLE private_memories OWNER TO {owner}",
            f"ALTER TABLE project_profiles OWNER TO {owner}",
            f"DROP ROLE IF EXISTS {_MIGRATOR_ROLE}",
        ]
    )


def test_force_rls_owner_passes_guard(_migrator_owns_tenant_tables: Any) -> None:
    """A NOSUPERUSER/NOBYPASSRLS owner of FORCE-RLS tables is not privileged."""
    from tapps_brain.postgres_connection import PostgresConnectionManager

    cm = PostgresConnectionManager(_migrator_dsn())
    try:
        # _ensure_pool() runs the guard; FORCE is on (migration 012) so the
        # ownership is not a violation and this must not raise.
        cm._ensure_pool()
    finally:
        cm.close()


def test_non_force_owner_still_raises(_migrator_owns_tenant_tables: Any) -> None:
    """Drop FORCE on one table and the owner is flagged as privileged again."""
    from tapps_brain.postgres_connection import PostgresConnectionManager

    _exec_as_owner(["ALTER TABLE private_memories NO FORCE ROW LEVEL SECURITY"])
    try:
        cm = PostgresConnectionManager(_migrator_dsn())
        with pytest.raises(RuntimeError, match="without FORCE ROW LEVEL SECURITY"):
            cm._ensure_pool()
        cm.close()
    finally:
        _exec_as_owner(["ALTER TABLE private_memories FORCE ROW LEVEL SECURITY"])
