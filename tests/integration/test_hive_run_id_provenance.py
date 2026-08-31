"""Postgres integration tests for hive_memories.run_id provenance (TAP-6815).

``private_memories`` has carried ``run_id`` since private migration 031, and
``/v1/remember`` fills it from the caller's invocation identity (VAL-19).  The
hive copy of the same write had nowhere to put it — ``PostgresHiveBackend.save``
did not accept the value, so a ``share=True`` remember produced one joinable row
and one permanently anonymous one.

These tests pin both halves of the contract:

* an invocation-scoped fan-out write persists the id, and the hive row joins
  back to its private counterpart on ``run_id``;
* a fan-out write with no invocation leaves the column NULL, and never inherits
  an id that happens to be reachable — from an earlier save on the same store,
  or from the row it overwrites.

The second half is the one that matters most: a wrong ``run_id`` is strictly
worse than none, because it silently poisons the join this column exists for.

Requires: ``TAPPS_BRAIN_DATABASE_URL`` (skipped otherwise). Mark:
``requires_postgres``.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest

pytestmark = pytest.mark.requires_postgres

_PG_DSN = os.environ.get("TAPPS_BRAIN_DATABASE_URL", "")


def _cm() -> Any:
    from tapps_brain.postgres_connection import PostgresConnectionManager

    return PostgresConnectionManager(_PG_DSN)


def _apply_migrations() -> None:
    from tapps_brain.postgres_migrations import apply_hive_migrations, apply_private_migrations

    apply_private_migrations(_PG_DSN)
    apply_hive_migrations(_PG_DSN)


def _hive(cm: Any) -> Any:
    from tapps_brain.postgres_hive import PostgresHiveBackend

    return PostgresHiveBackend(cm)


def _store(cm: Any, tmp_path: Any, *, project_id: str, agent_id: str, group: str) -> Any:
    """A ``MemoryStore`` on real Postgres with a real Hive backend attached.

    ``groups=[group]`` is what makes a bare ``agent_scope="group"`` save (the
    scope ``brain_remember(share=True)`` derives) actually fan out.
    """
    from tapps_brain.postgres_private import PostgresPrivateBackend
    from tapps_brain.store import MemoryStore

    backend = PostgresPrivateBackend(cm, project_id=project_id, agent_id=agent_id)
    return MemoryStore(
        tmp_path,
        private_backend=backend,
        hive_store=_hive(cm),
        hive_agent_id=agent_id,
        groups=[group],
        auto_register=False,
    )


def _hive_run_ids(cm: Any, namespace: str) -> dict[str, str | None]:
    with cm.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT key, run_id FROM hive_memories WHERE namespace = %s",
            (namespace,),
        )
        return dict(cur.fetchall())


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_migration_adds_nullable_run_id_column() -> None:
    """Migration 005 lands an additive, nullable TEXT column."""
    _apply_migrations()
    cm = _cm()
    try:
        with cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT data_type, is_nullable FROM information_schema.columns "
                "WHERE table_name = 'hive_memories' AND column_name = 'run_id'"
            )
            row = cur.fetchone()
        assert row is not None, "hive_memories.run_id was not created by migration 005"
        assert row[0] == "text"
        assert row[1] == "YES", "run_id must be nullable — absent provenance stays absent"
    finally:
        cm.close()


# ---------------------------------------------------------------------------
# Backend contract
# ---------------------------------------------------------------------------


def test_hive_save_persists_run_id() -> None:
    _apply_migrations()
    cm = _cm()
    try:
        hive = _hive(cm)
        namespace = f"ns-{uuid.uuid4().hex[:8]}"
        run_id = f"inv-{uuid.uuid4().hex[:12]}"

        saved = hive.save(key="attributed", value="v", namespace=namespace, run_id=run_id)

        assert saved is not None
        assert saved["run_id"] == run_id
        assert _hive_run_ids(cm, namespace) == {"attributed": run_id}
    finally:
        cm.close()


def test_hive_save_without_run_id_leaves_null() -> None:
    _apply_migrations()
    cm = _cm()
    try:
        hive = _hive(cm)
        namespace = f"ns-{uuid.uuid4().hex[:8]}"

        hive.save(key="anonymous", value="v", namespace=namespace)

        assert _hive_run_ids(cm, namespace) == {"anonymous": None}
    finally:
        cm.close()


def test_overwrite_without_run_id_does_not_inherit_the_previous_one() -> None:
    """The ON CONFLICT path assigns ``run_id``; it must not COALESCE it.

    Keeping the prior invocation's id on an overwrite would attribute the *new*
    content to an invocation that never produced it — the exact false
    provenance this column exists to prevent.
    """
    _apply_migrations()
    cm = _cm()
    try:
        hive = _hive(cm)
        namespace = f"ns-{uuid.uuid4().hex[:8]}"
        run_id = f"inv-{uuid.uuid4().hex[:12]}"

        # last_write_wins drives the UPSERT branch rather than versioned supersede.
        hive.save(
            key="k",
            value="first",
            namespace=namespace,
            run_id=run_id,
            conflict_policy="last_write_wins",
        )
        assert _hive_run_ids(cm, namespace) == {"k": run_id}

        hive.save(key="k", value="second", namespace=namespace, conflict_policy="last_write_wins")

        assert _hive_run_ids(cm, namespace) == {"k": None}
    finally:
        cm.close()


# ---------------------------------------------------------------------------
# End-to-end fan-out (the share=True path)
# ---------------------------------------------------------------------------


def test_shared_write_inside_an_invocation_joins_back_to_its_private_row(
    tmp_path: Any,
) -> None:
    """A ``share=True``-shaped save carries the invocation id to the hive copy.

    Proven by an actual SQL join of ``hive_memories`` to ``private_memories``
    on ``run_id`` — the join the feature exists to support — not by reading the
    two rows separately and comparing in Python.
    """
    _apply_migrations()
    cm = _cm()
    try:
        suffix = uuid.uuid4().hex[:8]
        project_id = f"rid-proj-{suffix}"
        agent_id = f"rid-agent-{suffix}"
        group = f"rid-group-{suffix}"
        run_id = f"inv-{uuid.uuid4().hex[:12]}"
        key = f"shared-{suffix}"

        store = _store(cm, tmp_path, project_id=project_id, agent_id=agent_id, group=group)
        # agent_scope="group" is what brain_remember(share=True) derives.
        store.save(key, "shared under an invocation", agent_scope="group", run_id=run_id)

        # ``private_memories`` carries FORCE ROW LEVEL SECURITY (private
        # migration 012) and its policy is fail-closed: a connection without
        # ``app.project_id`` sees an empty view, not an empty table, so a blind
        # cursor here would "prove" the join fails for the wrong reason.
        with cm.project_context(project_id) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT h.key, p.key FROM hive_memories h "
                "JOIN private_memories p ON p.run_id = h.run_id "
                "WHERE h.namespace = %s AND p.project_id = %s AND p.agent_id = %s",
                (group, project_id, agent_id),
            )
            joined = cur.fetchall()

        assert joined == [(key, key)], (
            "the hive fan-out copy must be joinable back to the private row on run_id"
        )
        assert _hive_run_ids(cm, group) == {key: run_id}
    finally:
        cm.close()


def test_shared_write_outside_an_invocation_leaves_run_id_null(tmp_path: Any) -> None:
    """Negative control, including the no-inherit case.

    The first save pins a real ``run_id`` on the *same store instance*, so an
    id is demonstrably reachable in scope when the second, invocation-less save
    runs.  The second row must still be NULL: absent stays absent.
    """
    _apply_migrations()
    cm = _cm()
    try:
        suffix = uuid.uuid4().hex[:8]
        project_id = f"rid-proj-{suffix}"
        agent_id = f"rid-agent-{suffix}"
        group = f"rid-group-{suffix}"
        run_id = f"inv-{uuid.uuid4().hex[:12]}"

        store = _store(cm, tmp_path, project_id=project_id, agent_id=agent_id, group=group)
        store.save(f"attributed-{suffix}", "inside", agent_scope="group", run_id=run_id)
        store.save(f"anonymous-{suffix}", "outside", agent_scope="group")

        rows = _hive_run_ids(cm, group)

        assert rows[f"attributed-{suffix}"] == run_id
        assert rows[f"anonymous-{suffix}"] is None, (
            "a fan-out write outside any invocation must not inherit the id "
            f"from an earlier save on the same store (got {rows[f'anonymous-{suffix}']!r})"
        )
    finally:
        cm.close()
