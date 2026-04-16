"""TAP-514 — verify project_context() RLS context survives across
transactions inside one pool borrow, and dies on connection release.

Before TAP-514, ``project_context`` used ``SET LOCAL`` which only lives
for one transaction.  A caller that committed mid-block and then ran a
second transaction on the same connection would silently lose
``app.project_id`` — fail-closed RLS hid every row, looking like the
tenant simply had no data.

The fix:
* ``project_context`` / ``agent_context`` / ``admin_context`` now use
  session-level ``SET`` so the binding survives the whole borrow.
* The pool's ``reset`` callback (``_reset_session_vars``) wipes the
  variables when the connection is returned to the pool, so identity
  cannot leak across borrows.

This module has two assertions:

1.  ``test_session_var_survives_intra_borrow_commit`` — set context,
    commit, run a second transaction, confirm RLS still sees the right
    tenant.
2.  ``test_session_var_cleared_on_pool_release`` — release the
    connection, re-acquire (likely same physical conn), confirm
    ``app.project_id`` is empty so RLS fails closed.

Requires ``TAPPS_TEST_POSTGRES_DSN``.
"""

from __future__ import annotations

import os
import uuid

import pytest

_PG_DSN = os.environ.get("TAPPS_TEST_POSTGRES_DSN", "")
_SKIP_PG = not _PG_DSN

# Same convention as test_tenant_isolation.py: connect as the runtime
# (non-owner) role so RLS is actually enforced on the assertion side.
_RUNTIME_DSN = _PG_DSN.replace("tapps:tapps@", "tapps_runtime:tapps_runtime@", 1) if _PG_DSN else ""

pytestmark = pytest.mark.skipif(_SKIP_PG, reason="TAPPS_TEST_POSTGRES_DSN not set")


def _apply_migrations() -> None:
    from tapps_brain.postgres_migrations import apply_private_migrations

    apply_private_migrations(_PG_DSN)


def _runtime_manager() -> object:
    from tapps_brain.postgres_connection import PostgresConnectionManager

    return PostgresConnectionManager(_RUNTIME_DSN, min_size=1, max_size=1)


def _owner_manager() -> object:
    """Owner manager — used to seed rows under admin context (RLS bypass via FORCE+
    admin_context for project_profiles; for private_memories we use SET app.project_id
    so the FORCE-on policy permits the insert)."""
    from tapps_brain.postgres_connection import PostgresConnectionManager

    return PostgresConnectionManager(_PG_DSN, min_size=1, max_size=1)


def _seed_row(owner_cm: object, project_id: str, key: str, value: str) -> None:
    """Seed a private_memories row.  Sets app.project_id so the WITH CHECK
    clause permits the INSERT under FORCE RLS."""
    with owner_cm.project_context(project_id) as conn:  # type: ignore[attr-defined]
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO private_memories (project_id, agent_id, key, value)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (project_id, agent_id, key) DO UPDATE SET value = EXCLUDED.value
                """,
                (project_id, "agent-test", key, value),
            )


def _cleanup_rows(owner_cm: object, project_ids: list[str]) -> None:
    for pid in project_ids:
        with owner_cm.project_context(pid) as conn:  # type: ignore[attr-defined]
            with conn.cursor() as cur:
                cur.execute("DELETE FROM private_memories WHERE project_id = %s", (pid,))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_session_var_survives_intra_borrow_commit() -> None:
    """SET (session) means app.project_id survives a mid-borrow commit;
    the second transaction still sees the right tenant under RLS."""
    _apply_migrations()
    owner_cm = _owner_manager()
    runtime_cm = _runtime_manager()

    pid = f"tap514-{uuid.uuid4().hex[:8]}"
    key1 = f"key-{uuid.uuid4().hex[:8]}"
    key2 = f"key-{uuid.uuid4().hex[:8]}"

    try:
        _seed_row(owner_cm, pid, key1, "value-A")
        _seed_row(owner_cm, pid, key2, "value-B")

        # One borrow — two transactions.  The first transaction reads key1
        # and commits; the second transaction reads key2 against the same
        # physical connection.  Under SET LOCAL the second read would see
        # zero rows (fail-closed); under SET (session) it sees the row.
        with runtime_cm.project_context(pid) as conn:  # type: ignore[attr-defined]
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM private_memories WHERE key = %s", (key1,))
                row1 = cur.fetchone()
            conn.commit()  # <- intentionally commits mid-borrow

            with conn.cursor() as cur:
                cur.execute("SELECT value FROM private_memories WHERE key = %s", (key2,))
                row2 = cur.fetchone()

        assert row1 is not None and row1[0] == "value-A"
        assert row2 is not None, (
            "second transaction in the same borrow lost app.project_id; "
            "TAP-514 regression — project_context must use SET, not SET LOCAL"
        )
        assert row2[0] == "value-B"
    finally:
        _cleanup_rows(owner_cm, [pid])
        runtime_cm.close()  # type: ignore[attr-defined]
        owner_cm.close()  # type: ignore[attr-defined]


def test_session_var_cleared_on_pool_release() -> None:
    """The pool's reset callback wipes app.project_id on connection
    release; a subsequent get_connection() (no project_context) sees the
    fail-closed policy and returns zero rows."""
    _apply_migrations()
    owner_cm = _owner_manager()
    runtime_cm = _runtime_manager()  # min_size=max_size=1 -> same conn re-used

    pid = f"tap514-{uuid.uuid4().hex[:8]}"
    key = f"key-{uuid.uuid4().hex[:8]}"

    try:
        _seed_row(owner_cm, pid, key, "value")

        # First borrow: bind project_id, then release.
        with runtime_cm.project_context(pid) as conn:  # type: ignore[attr-defined]
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM private_memories WHERE key = %s", (key,))
                assert cur.fetchone() is not None  # sanity

        # Second borrow: no project_context — same physical connection,
        # but reset callback should have cleared app.project_id.
        with runtime_cm.get_connection() as conn:  # type: ignore[attr-defined]
            with conn.cursor() as cur:
                cur.execute("SELECT current_setting('app.project_id', TRUE)")
                row = cur.fetchone()
                assert row is not None
                assert row[0] in (None, ""), (
                    f"app.project_id leaked across pool borrows: got {row[0]!r}; "
                    "TAP-514 regression — reset callback must run on release"
                )

                cur.execute("SELECT value FROM private_memories WHERE key = %s", (key,))
                rows = cur.fetchall()
                assert rows == [], (
                    "fail-closed RLS should hide every row when "
                    "app.project_id is unset; TAP-514 regression"
                )
    finally:
        _cleanup_rows(owner_cm, [pid])
        runtime_cm.close()  # type: ignore[attr-defined]
        owner_cm.close()  # type: ignore[attr-defined]
