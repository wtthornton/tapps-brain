"""Purge tenant rows by project_id — test/load cleanup utility (TAP-4465).

Test and load harnesses write rows under unique ``(project_id, agent_id)`` keys.
Against a persistent or shared Postgres those rows leak indefinitely unless they
are explicitly removed (a 72h evaluation found 9,280 leaked rows from fixtures
that ran against the live deployment). This module deletes every row for a set
of project_ids across all tables that carry a ``project_id`` column, using the
admin-bypass context so cross-tenant deletes are permitted under RLS.

Two entry points:

- :func:`purge_projects` — delete exact project_ids (used by test finalizers and
  the load-smoke harness, which know the ids they created).
- :func:`purge_by_prefix` — delete every tenant whose project_id starts with one
  of a set of reserved prefixes (the operational safety valve / CLI + make
  target). Harnesses MUST name their tenants with a reserved prefix so this can
  mop up anything a finalizer missed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tapps_brain.postgres_connection import PostgresConnectionManager

#: project_id prefixes reserved for throwaway test / load / smoke tenants.
#: Harnesses must name their projects with one of these so leaked rows are
#: recoverable by :func:`purge_by_prefix`. Documented in ``AGENTS.md``.
RESERVED_TEST_PROJECT_PREFIXES: tuple[str, ...] = ("smoke-", "test-")

#: Table-name prefixes never touched by a purge — manual backup/safety-net
#: tables must stay immutable even though they carry a ``project_id`` column.
_EXCLUDED_TABLE_PREFIXES: tuple[str, ...] = ("purge_backup_",)


def is_reserved_test_project(
    project_id: str,
    prefixes: tuple[str, ...] = RESERVED_TEST_PROJECT_PREFIXES,
) -> bool:
    """Return ``True`` when *project_id* uses a reserved test/load prefix."""
    return any(project_id.startswith(prefix) for prefix in prefixes)


def discover_tenant_tables(cm: PostgresConnectionManager) -> list[str]:
    """Return top-level public tables that carry a ``project_id`` column.

    Only ordinary tables (``relkind='r'``) and partitioned-table parents
    (``relkind='p'``) are returned; partition *children* are excluded so a
    ``DELETE`` on the parent cascades to them exactly once. Backup/safety-net
    tables (see :data:`_EXCLUDED_TABLE_PREFIXES`) are never returned.
    """
    sql = (
        "SELECT c.relname "
        "FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "JOIN pg_attribute a ON a.attrelid = c.oid "
        "WHERE n.nspname = 'public' "
        "AND a.attname = 'project_id' AND a.attnum > 0 AND NOT a.attisdropped "
        "AND c.relkind IN ('r', 'p') "
        "AND NOT EXISTS (SELECT 1 FROM pg_inherits i WHERE i.inhrelid = c.oid) "
        "ORDER BY c.relname"
    )
    with cm.get_connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        names = [str(row[0]) for row in cur.fetchall()]
    return [
        name
        for name in names
        if not any(name.startswith(prefix) for prefix in _EXCLUDED_TABLE_PREFIXES)
    ]


def purge_projects(
    cm: PostgresConnectionManager,
    project_ids: list[str],
) -> dict[str, int]:
    """Delete all rows for *project_ids* across every tenant table.

    Returns a ``{table: rows_deleted}`` map for tables where rows were removed.
    Uses :meth:`PostgresConnectionManager.admin_context` so the delete bypasses
    per-tenant RLS. A no-op (empty dict) when *project_ids* is empty.
    """
    ids = [pid for pid in dict.fromkeys(project_ids) if pid]
    if not ids:
        return {}
    return _delete_where(cm, "project_id = ANY(%s)", (ids,))


def purge_by_prefix(
    cm: PostgresConnectionManager,
    prefixes: tuple[str, ...] = RESERVED_TEST_PROJECT_PREFIXES,
) -> dict[str, int]:
    """Delete all rows whose ``project_id`` starts with any reserved *prefix*.

    Returns a ``{table: rows_deleted}`` map. Intended as the operational safety
    valve for the live DB, where only intentional harness tenants ever carry a
    reserved prefix.
    """
    cleaned = tuple(p for p in prefixes if p)
    if not cleaned:
        return {}

    # Escape LIKE wildcards so a literal ``_``/``%`` in a (CLI-supplied) prefix
    # cannot over-match and delete unrelated tenants (``test_`` would otherwise
    # match ``testX...``). ``\`` is the default LIKE escape character.
    def _escape_like(s: str) -> str:
        return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    patterns = [f"{_escape_like(prefix)}%" for prefix in cleaned]
    clause = " OR ".join("project_id LIKE %s" for _ in patterns)
    return _delete_where(cm, clause, tuple(patterns))


def _delete_where(
    cm: PostgresConnectionManager,
    where_clause: str,
    params: tuple[object, ...],
) -> dict[str, int]:
    """Delete from every tenant table using *where_clause* under admin bypass."""
    from psycopg import sql as pgsql

    tables = discover_tenant_tables(cm)
    deleted: dict[str, int] = {}
    with cm.admin_context() as conn:
        for table in tables:
            stmt = pgsql.SQL("DELETE FROM {table} WHERE {where}").format(
                table=pgsql.Identifier(table),
                where=pgsql.SQL(where_clause),  # internal constant, never user input
            )
            with conn.cursor() as cur:
                cur.execute(stmt, params)
                if cur.rowcount and cur.rowcount > 0:
                    deleted[table] = cur.rowcount
        conn.commit()
    return deleted
