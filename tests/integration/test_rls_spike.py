"""Integration tests for the RLS spike on hive_memories (EPIC-063 STORY-063.3).

Verifies that namespace-based Row Level Security (RLS) on ``hive_memories``
prevents cross-namespace reads when the ``tapps.current_namespace`` session
variable is set, and that the admin-bypass policy allows full access when the
variable is absent or empty.

Requires: ``TAPPS_TEST_POSTGRES_DSN`` environment variable pointing to a live
Postgres instance.  Tests are skipped when the variable is not set.

Policy behaviour under test:
    hive_admin_bypass   — passes when session var is NULL or '' (all rows visible)
    hive_namespace_isolation — passes when namespace = session var (isolation)

Two permissive policies are OR-combined by Postgres: a row is visible when at
least one policy passes.  When the session var is set to a non-empty value the
admin bypass fails, so only the isolation policy applies.

Migration dependency:
    Both migrations are applied at test start via ``apply_hive_migrations``:
      hive/001_initial.sql   — base schema
      hive/002_rls_spike.sql — RLS policies (this story)
"""

from __future__ import annotations

import os
import uuid

import pytest

# ---------------------------------------------------------------------------
# Skip guard — all tests require a live Postgres instance
# ---------------------------------------------------------------------------

_PG_DSN = os.environ.get("TAPPS_TEST_POSTGRES_DSN", "")
_SKIP_PG = not _PG_DSN

pytestmark = pytest.mark.skipif(_SKIP_PG, reason="TAPPS_TEST_POSTGRES_DSN not set")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _apply_migrations() -> None:
    """Apply hive migrations (includes 002_rls_spike.sql) to the test DB."""
    from tapps_brain.postgres_migrations import apply_hive_migrations

    apply_hive_migrations(_PG_DSN)


def _make_manager() -> "PostgresConnectionManager":  # type: ignore[name-defined]
    from tapps_brain.postgres_connection import PostgresConnectionManager

    return PostgresConnectionManager(_PG_DSN)


def _unique_ns() -> str:
    """Generate a unique namespace so parallel runs do not interfere."""
    return f"test-rls-ns-{uuid.uuid4().hex[:8]}"


def _unique_key() -> str:
    return f"key-{uuid.uuid4().hex[:8]}"


def _insert_row(conn: object, namespace: str, key: str, value: str) -> None:
    """Insert (or upsert) a hive_memories row with admin bypass (session var = '')."""
    with conn.cursor() as cur:  # type: ignore[union-attr]
        cur.execute("SET LOCAL tapps.current_namespace = %s", ("",))
        cur.execute(
            """
            INSERT INTO hive_memories (namespace, key, value)
            VALUES (%s, %s, %s)
            ON CONFLICT (namespace, key) DO UPDATE SET value = EXCLUDED.value
            """,
            (namespace, key, value),
        )


def _cleanup_rows(conn: object, ns_a: str, ns_b: str, key: str) -> None:
    """Delete test rows using admin bypass (session var = '')."""
    with conn.cursor() as cur:  # type: ignore[union-attr]
        cur.execute("SET LOCAL tapps.current_namespace = %s", ("",))
        cur.execute(
            "DELETE FROM hive_memories WHERE namespace IN (%s, %s) AND key = %s",
            (ns_a, ns_b, key),
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_rls_namespace_isolation_select() -> None:
    """Cross-namespace reads are blocked when session namespace is set.

    Inserts one row each in ns_a and ns_b.  Queries with session var = ns_a
    must return only the ns_a row.  Queries with session var = ns_b must return
    only the ns_b row.
    """
    _apply_migrations()
    cm = _make_manager()

    ns_a = _unique_ns()
    ns_b = _unique_ns()
    key = _unique_key()

    try:
        # --- Setup: insert rows using admin bypass -------------------------
        with cm.get_connection() as conn:
            _insert_row(conn, ns_a, key, "value-for-A")
            _insert_row(conn, ns_b, key, "value-for-B")

        # --- Query under ns_a context ------------------------------------
        with cm.namespace_context(ns_a) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT namespace, value FROM hive_memories WHERE key = %s",
                    (key,),
                )
                rows = cur.fetchall()

        visible_ns = {r[0] for r in rows}
        assert ns_a in visible_ns, f"ns_a row not visible under ns_a context: {rows}"
        assert ns_b not in visible_ns, (
            f"ns_b row leaked into ns_a context (RLS not enforced): {rows}"
        )

        # --- Query under ns_b context ------------------------------------
        with cm.namespace_context(ns_b) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT namespace, value FROM hive_memories WHERE key = %s",
                    (key,),
                )
                rows = cur.fetchall()

        visible_ns = {r[0] for r in rows}
        assert ns_b in visible_ns, f"ns_b row not visible under ns_b context: {rows}"
        assert ns_a not in visible_ns, (
            f"ns_a row leaked into ns_b context (RLS not enforced): {rows}"
        )

    finally:
        with cm.get_connection() as conn:
            _cleanup_rows(conn, ns_a, ns_b, key)
        cm.close()


def test_rls_admin_bypass_empty_string_sees_all() -> None:
    """Admin bypass (session var = '') allows all rows to be visible.

    This verifies the hive_admin_bypass policy: when the session variable is
    the empty string, both namespaces' rows are returned from a single query.
    """
    _apply_migrations()
    cm = _make_manager()

    ns_a = _unique_ns()
    ns_b = _unique_ns()
    key = _unique_key()

    try:
        # Insert both rows.
        with cm.get_connection() as conn:
            _insert_row(conn, ns_a, key, "value-A")
            _insert_row(conn, ns_b, key, "value-B")

        # Query with admin bypass (session var = '').
        with cm.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL tapps.current_namespace = %s", ("",))
                cur.execute(
                    "SELECT namespace FROM hive_memories"
                    " WHERE namespace IN (%s, %s) AND key = %s",
                    (ns_a, ns_b, key),
                )
                rows = cur.fetchall()

        visible_ns = {r[0] for r in rows}
        assert ns_a in visible_ns, f"ns_a row not visible under admin bypass: {rows}"
        assert ns_b in visible_ns, f"ns_b row not visible under admin bypass: {rows}"

    finally:
        with cm.get_connection() as conn:
            _cleanup_rows(conn, ns_a, ns_b, key)
        cm.close()


def test_rls_admin_bypass_unset_sees_all() -> None:
    """Admin bypass (session var never set) allows all rows to be visible.

    When no SET LOCAL has been called in the transaction, current_setting
    returns NULL (missing_ok=TRUE), and the admin_bypass policy passes.
    """
    _apply_migrations()
    cm = _make_manager()

    ns_a = _unique_ns()
    ns_b = _unique_ns()
    key = _unique_key()

    try:
        with cm.get_connection() as conn:
            _insert_row(conn, ns_a, key, "value-A")
            _insert_row(conn, ns_b, key, "value-B")

        # No SET LOCAL — session var is absent (NULL).
        with cm.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT namespace FROM hive_memories"
                    " WHERE namespace IN (%s, %s) AND key = %s",
                    (ns_a, ns_b, key),
                )
                rows = cur.fetchall()

        visible_ns = {r[0] for r in rows}
        assert ns_a in visible_ns, f"ns_a row not visible with unset session var: {rows}"
        assert ns_b in visible_ns, f"ns_b row not visible with unset session var: {rows}"

    finally:
        with cm.get_connection() as conn:
            _cleanup_rows(conn, ns_a, ns_b, key)
        cm.close()


def test_rls_namespace_context_write_isolation() -> None:
    """INSERT is restricted to the session namespace when session var is set.

    With RLS enforced and session var = ns_a, inserting a row with
    namespace=ns_b must fail the WITH CHECK policy and raise an error.
    """
    _apply_migrations()
    cm = _make_manager()

    ns_a = _unique_ns()
    ns_b = _unique_ns()
    key = _unique_key()

    try:
        # Attempt to INSERT a ns_b row while session var is ns_a.
        # The hive_namespace_isolation WITH CHECK will fail because
        # namespace='ns_b' != current_setting('tapps.current_namespace') = 'ns_a'.
        # The hive_admin_bypass WITH CHECK will also fail because ns_a != ''.
        # So the INSERT should raise an exception (policy violation).
        with pytest.raises(Exception):  # psycopg raises e.g. InFailedSqlTransaction
            with cm.namespace_context(ns_a) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO hive_memories (namespace, key, value)"
                        " VALUES (%s, %s, %s)",
                        (ns_b, key, "cross-namespace-write"),
                    )

        # Verify the row was not written (check under admin bypass).
        with cm.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL tapps.current_namespace = %s", ("",))
                cur.execute(
                    "SELECT COUNT(*) FROM hive_memories"
                    " WHERE namespace = %s AND key = %s",
                    (ns_b, key),
                )
                count = cur.fetchone()[0]
        assert count == 0, f"Cross-namespace write succeeded despite RLS: {count} row(s)"

    finally:
        with cm.get_connection() as conn:
            _cleanup_rows(conn, ns_a, ns_b, key)
        cm.close()


def test_namespace_context_helper_sets_session_var() -> None:
    """PostgresConnectionManager.namespace_context sets the session variable."""
    _apply_migrations()
    cm = _make_manager()

    namespace = _unique_ns()
    try:
        with cm.namespace_context(namespace) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT current_setting('tapps.current_namespace', TRUE)"
                )
                result = cur.fetchone()[0]

        assert result == namespace, (
            f"namespace_context did not set session var: expected {namespace!r}, got {result!r}"
        )
    finally:
        cm.close()
