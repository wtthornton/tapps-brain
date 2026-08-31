"""Namespace reaper — dry-run scan only (TAP-6698).

Detects two shapes of leaked/abandoned data:

1. **Single-entry projects.** A ``private_memories`` ``project_id`` whose
   entire history (across every ``agent_id``) is exactly one row, older than
   *single_entry_age_days*. Typically a smoke-test or one-shot CLI run that
   never got cleaned up.
2. **``ns-*`` namespace residue.** Hive ``hive_memories`` namespaces matching
   the ``ns-<hex>`` pattern that ``tests/`` fixtures generate for isolation
   and never delete.

This module only *lists* candidates — never archives or deletes. Per the
lane scope (Ruling 14 / KB-3.4), live apply for this pass is Wave 4's job;
this lane's maintenance cycle always calls it in dry-run mode.
"""

from __future__ import annotations

from typing import Any

_DEFAULT_SINGLE_ENTRY_AGE_DAYS = 30


def find_single_entry_projects(
    conn: Any, *, age_days: int = _DEFAULT_SINGLE_ENTRY_AGE_DAYS
) -> list[dict[str, Any]]:
    """List ``project_id``s whose total private_memories history is one old row."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT project_id, count(*) AS entry_count, min(created_at) AS created_at
            FROM private_memories
            GROUP BY project_id
            HAVING count(*) = 1
               AND min(created_at) < now() - make_interval(days => %s)
            ORDER BY project_id
            """,
            (age_days,),
        )
        rows = cur.fetchall()
    return [{"project_id": r[0], "entry_count": r[1], "created_at": r[2].isoformat()} for r in rows]


def find_ns_prefix_namespaces(conn: Any) -> list[dict[str, Any]]:
    """List Hive namespaces matching the ``ns-*`` test-fixture pattern."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT namespace, count(*) AS entry_count
            FROM hive_memories
            WHERE namespace LIKE 'ns-%'
            GROUP BY namespace
            ORDER BY namespace
            """
        )
        rows = cur.fetchall()
    return [{"namespace": r[0], "entry_count": r[1]} for r in rows]


def scan_reapable(conn: Any, *, age_days: int = _DEFAULT_SINGLE_ENTRY_AGE_DAYS) -> dict[str, Any]:
    """Dry-run-only scan: both candidate shapes, never a delete path."""
    single_entry = find_single_entry_projects(conn, age_days=age_days)
    ns_prefix = find_ns_prefix_namespaces(conn)
    return {
        "dry_run": True,
        "single_entry_projects": single_entry,
        "ns_prefix_namespaces": ns_prefix,
        "single_entry_count": len(single_entry),
        "ns_prefix_count": len(ns_prefix),
    }
