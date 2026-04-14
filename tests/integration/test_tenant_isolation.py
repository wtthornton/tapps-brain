"""Live-Postgres integration tests for EPIC-069 STORY-069.8 tenant RLS.

Verifies that the RLS policies shipped in
``migrations/private/009_project_rls.sql`` actually block cross-tenant
access at the database layer — not just at the app layer.

Requires ``TAPPS_TEST_POSTGRES_DSN`` pointing to a live Postgres; the
whole module is skipped otherwise (mirrors the pattern used by
``test_rls_spike.py``).

The DSN in ``TAPPS_TEST_POSTGRES_DSN`` is expected to connect as
``tapps:tapps`` (table owner / migrator).  The runtime role
``tapps_runtime`` (non-superuser, non-owner) is where RLS is actually
enforced, so the isolation assertions swap the user:password segment to
``tapps_runtime:tapps_runtime`` before connecting — same pattern as
``test_rls_spike.py``.

Policy contract under test
--------------------------
private_memories — fail-closed.
  USING/WITH CHECK require ``app.project_id`` to be set AND equal to the
  row's project_id.  Missing session var → zero rows visible; cross-tenant
  read → zero rows; cross-tenant INSERT → raises.

project_profiles — tenant isolation + admin bypass.
  Admin bypass requires ``app.is_admin = 'true'``.  Tenant isolation
  matches rows on project_id.
"""

from __future__ import annotations

import json
import os
import uuid

import pytest
from psycopg import sql as pgsql

_PG_DSN = os.environ.get("TAPPS_TEST_POSTGRES_DSN", "")
_SKIP_PG = not _PG_DSN

# Non-superuser role — RLS is only enforced against this identity.  Same
# transform as tests/integration/test_rls_spike.py.
_RUNTIME_DSN = (
    _PG_DSN.replace("tapps:tapps@", "tapps_runtime:tapps_runtime@", 1)
    if _PG_DSN
    else ""
)

pytestmark = pytest.mark.skipif(_SKIP_PG, reason="TAPPS_TEST_POSTGRES_DSN not set")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _apply_migrations() -> None:
    from tapps_brain.postgres_migrations import apply_private_migrations

    apply_private_migrations(_PG_DSN)


def _owner_manager() -> object:
    """Manager connected as the owner/migrator — bypasses RLS."""
    from tapps_brain.postgres_connection import PostgresConnectionManager

    return PostgresConnectionManager(_PG_DSN)


def _runtime_manager() -> object:
    """Manager connected as tapps_runtime — subject to RLS."""
    from tapps_brain.postgres_connection import PostgresConnectionManager

    return PostgresConnectionManager(_RUNTIME_DSN)


def _unique_project() -> str:
    return f"tenant-{uuid.uuid4().hex[:8]}"


def _unique_key() -> str:
    return f"key-{uuid.uuid4().hex[:8]}"


def _seed_memory(owner_cm: object, project_id: str, agent_id: str, key: str, value: str) -> None:
    """Seed a private_memories row using the owner role (RLS bypass).

    Uses the raw columns needed for the NOT NULL subset — the table has
    many columns but most have defaults (see 001_initial.sql).
    """
    with owner_cm.get_connection() as conn:  # type: ignore[attr-defined]
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO private_memories
                    (project_id, agent_id, key, value)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (project_id, agent_id, key) DO UPDATE SET
                    value = EXCLUDED.value
                """,
                (project_id, agent_id, key, value),
            )


def _cleanup_memories(owner_cm: object, project_ids: list[str]) -> None:
    with owner_cm.get_connection() as conn:  # type: ignore[attr-defined]
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM private_memories WHERE project_id = ANY(%s)",
                (project_ids,),
            )


def _cleanup_profiles(owner_cm: object, project_ids: list[str]) -> None:
    with owner_cm.get_connection() as conn:  # type: ignore[attr-defined]
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM project_profiles WHERE project_id = ANY(%s)",
                (project_ids,),
            )


# ---------------------------------------------------------------------------
# Tests — private_memories
# ---------------------------------------------------------------------------


def test_private_memories_cross_tenant_read_blocked() -> None:
    """Alpha writes a memory; beta must not see it via any query path."""
    _apply_migrations()
    owner_cm = _owner_manager()
    runtime_cm = _runtime_manager()

    alpha = _unique_project()
    beta = _unique_project()
    key = _unique_key()

    try:
        _seed_memory(owner_cm, alpha, "agent-1", key, "alpha-secret")
        _seed_memory(owner_cm, beta, "agent-1", key, "beta-secret")

        # Beta's scoped runtime connection must not see alpha's row.
        with runtime_cm.project_context(beta) as conn:  # type: ignore[attr-defined]
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT project_id, value FROM private_memories WHERE key = %s",
                    (key,),
                )
                rows = cur.fetchall()

        projects_seen = {r[0] for r in rows}
        assert alpha not in projects_seen, (
            f"alpha row leaked into beta context: {rows}"
        )
        assert beta in projects_seen, f"beta could not see its own row: {rows}"
    finally:
        _cleanup_memories(owner_cm, [alpha, beta])
        owner_cm.close()  # type: ignore[attr-defined]
        runtime_cm.close()  # type: ignore[attr-defined]


def test_private_memories_missing_project_id_is_fail_closed() -> None:
    """With app.project_id unset, the runtime role sees zero rows.

    Proves the "no identity = no access" contract of the fail-closed
    isolation policy on private_memories.
    """
    _apply_migrations()
    owner_cm = _owner_manager()
    runtime_cm = _runtime_manager()

    alpha = _unique_project()
    key = _unique_key()

    try:
        _seed_memory(owner_cm, alpha, "agent-1", key, "alpha-secret")

        # Runtime role with NO SET LOCAL — current_setting returns NULL.
        with runtime_cm.get_connection() as conn:  # type: ignore[attr-defined]
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM private_memories WHERE project_id = %s",
                    (alpha,),
                )
                count = cur.fetchone()[0]
        assert count == 0, (
            f"private_memories visible without app.project_id: {count} rows "
            "(fail-closed policy not enforced)"
        )
    finally:
        _cleanup_memories(owner_cm, [alpha])
        owner_cm.close()  # type: ignore[attr-defined]
        runtime_cm.close()  # type: ignore[attr-defined]


def test_private_memories_cross_tenant_write_blocked() -> None:
    """Session bound to alpha cannot INSERT a row whose project_id = beta.

    WITH CHECK on the isolation policy must reject the write.
    """
    _apply_migrations()
    owner_cm = _owner_manager()
    runtime_cm = _runtime_manager()

    alpha = _unique_project()
    beta = _unique_project()
    key = _unique_key()

    try:
        # Cross-tenant INSERT must raise.
        with pytest.raises(Exception):
            with runtime_cm.project_context(alpha) as conn:  # type: ignore[attr-defined]
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO private_memories
                            (project_id, agent_id, key, value)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (beta, "agent-1", key, "cross-tenant-write"),
                    )

        # Verify the row was not written (check as owner, bypasses RLS).
        with owner_cm.get_connection() as conn:  # type: ignore[attr-defined]
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM private_memories "
                    "WHERE project_id = %s AND key = %s",
                    (beta, key),
                )
                count = cur.fetchone()[0]
        assert count == 0, "cross-tenant write succeeded despite RLS"
    finally:
        _cleanup_memories(owner_cm, [alpha, beta])
        owner_cm.close()  # type: ignore[attr-defined]
        runtime_cm.close()  # type: ignore[attr-defined]


def test_memory_store_isolation_via_public_api() -> None:
    """Two PostgresPrivateBackend stores (alpha, beta) cannot see each other.

    Exercises the full stack: MemoryStore → _scoped_conn →
    project_context → RLS.  Confirms the wiring from STORY-069.8 Part B
    is in place end-to-end.
    """
    _apply_migrations()

    from tapps_brain.models import MemoryEntry
    from tapps_brain.postgres_private import PostgresPrivateBackend

    owner_cm = _owner_manager()
    runtime_cm = _runtime_manager()

    alpha = _unique_project()
    beta = _unique_project()
    key = _unique_key()

    try:
        alpha_backend = PostgresPrivateBackend(
            runtime_cm, project_id=alpha, agent_id="agent-1"
        )
        beta_backend = PostgresPrivateBackend(
            runtime_cm, project_id=beta, agent_id="agent-1"
        )

        alpha_backend.save(MemoryEntry(key=key, value="alpha-only"))

        # Beta's store must not see alpha's memory by key.
        beta_rows = [e for e in beta_backend.load_all() if e.key == key]
        assert beta_rows == [], f"beta store saw alpha's row: {beta_rows!r}"

        # Alpha's store sees its own.
        alpha_rows = [e for e in alpha_backend.load_all() if e.key == key]
        assert len(alpha_rows) == 1, "alpha store could not read its own row"
        assert alpha_rows[0].value == "alpha-only"
    finally:
        _cleanup_memories(owner_cm, [alpha, beta])
        owner_cm.close()  # type: ignore[attr-defined]
        runtime_cm.close()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Tests — project_profiles admin bypass
# ---------------------------------------------------------------------------


def test_project_profiles_admin_bypass_lists_all() -> None:
    """Registry (admin_context) sees every row regardless of tenant."""
    _apply_migrations()

    from tapps_brain.profile import get_builtin_profile
    from tapps_brain.project_registry import ProjectRegistry

    owner_cm = _owner_manager()
    runtime_cm = _runtime_manager()

    alpha = _unique_project()
    beta = _unique_project()

    try:
        profile = get_builtin_profile("repo-brain")
        registry = ProjectRegistry(runtime_cm)
        registry.register(alpha, profile, source="admin", approved=True)
        registry.register(beta, profile, source="admin", approved=True)

        rows = registry.list_all()
        project_ids = {r.project_id for r in rows}
        assert alpha in project_ids, (
            f"admin list_all missed alpha: {project_ids}"
        )
        assert beta in project_ids, (
            f"admin list_all missed beta: {project_ids}"
        )
    finally:
        _cleanup_profiles(owner_cm, [alpha, beta])
        owner_cm.close()  # type: ignore[attr-defined]
        runtime_cm.close()  # type: ignore[attr-defined]


def test_project_profiles_tenant_isolation_without_admin() -> None:
    """A plain project_context connection only sees its own profile row."""
    _apply_migrations()

    from tapps_brain.profile import get_builtin_profile
    from tapps_brain.project_registry import ProjectRegistry

    owner_cm = _owner_manager()
    runtime_cm = _runtime_manager()

    alpha = _unique_project()
    beta = _unique_project()

    try:
        registry = ProjectRegistry(runtime_cm)
        profile = get_builtin_profile("repo-brain")
        registry.register(alpha, profile, source="admin", approved=True)
        registry.register(beta, profile, source="admin", approved=True)

        # Scoped (non-admin) read must only see own row.
        with runtime_cm.project_context(alpha) as conn:  # type: ignore[attr-defined]
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT project_id FROM project_profiles WHERE project_id IN (%s, %s)",
                    (alpha, beta),
                )
                rows = {r[0] for r in cur.fetchall()}
        assert alpha in rows
        assert beta not in rows, (
            f"beta's profile leaked into alpha's project_context: {rows}"
        )
    finally:
        _cleanup_profiles(owner_cm, [alpha, beta])
        owner_cm.close()  # type: ignore[attr-defined]
        runtime_cm.close()  # type: ignore[attr-defined]
