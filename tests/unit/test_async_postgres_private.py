"""STORY-072.2 — unit tests for AsyncPostgresPrivateBackend (mocked).

These tests cover the cursor / connection wiring, parameter shapes, and
the SQL constants the async backend issues.  Real-DB behavioral parity
with the sync backend lives in
``tests/integration/test_async_private_backend.py``.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Async-aware mock factories
# ---------------------------------------------------------------------------


def _make_async_cursor(
    *,
    fetchone: Any = None,
    fetchmany_chunks: list[list[Any]] | None = None,
    fetchall: list[Any] | None = None,
    rowcount: int = 1,
    description: list[tuple[str, ...]] | None = None,
) -> MagicMock:
    """Build a MagicMock async cursor with awaitable execute / fetch methods.

    *fetchmany_chunks* models successive ``fetchmany(chunk_size)`` calls;
    each call returns the next list, then ``[]`` to signal end-of-stream.
    """
    cur = MagicMock()
    cur.execute = AsyncMock(return_value=None)
    cur.fetchone = AsyncMock(return_value=fetchone)
    cur.fetchall = AsyncMock(return_value=fetchall or [])
    cur.rowcount = rowcount
    cur.description = description
    if fetchmany_chunks is not None:
        chunks = [*fetchmany_chunks, []]
        cur.fetchmany = AsyncMock(side_effect=chunks)
    else:
        cur.fetchmany = AsyncMock(return_value=[])
    cur.__aenter__ = AsyncMock(return_value=cur)
    cur.__aexit__ = AsyncMock(return_value=False)
    return cur


def _make_async_conn_cm(cur: MagicMock) -> MagicMock:
    """Build a MagicMock async connection-context-manager that yields *cur*."""
    conn = MagicMock()
    conn.cursor = MagicMock(return_value=cur)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _make_manager(cur: MagicMock) -> MagicMock:
    """Build a fake ``PostgresConnectionManager`` whose async_project_context
    yields a connection that returns *cur* from ``cursor()``."""
    cm = MagicMock()
    cm.async_project_context = MagicMock(return_value=_make_async_conn_cm(cur))
    cm.get_async_connection = MagicMock(return_value=_make_async_conn_cm(cur))
    cm.close_async = AsyncMock(return_value=None)
    return cm


def _make_entry(key: str = "k1") -> Any:
    """Build a minimal MemoryEntry for save() tests."""
    from tapps_brain.models import MemoryEntry, MemoryScope, MemorySource, MemoryTier

    return MemoryEntry(
        key=key,
        value="hello world",
        tier=MemoryTier.pattern,
        confidence=0.7,
        source=MemorySource.agent,
        source_agent="test-agent",
        scope=MemoryScope.project,
        tags=["a", "b"],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAsyncPostgresPrivateBackendBasics:
    """Constructor + property + scoped-conn delegation."""

    def test_constructor_stores_identity(self) -> None:
        from tapps_brain.async_postgres_private import AsyncPostgresPrivateBackend

        cm = MagicMock()
        b = AsyncPostgresPrivateBackend(cm, project_id="p1", agent_id="a1")
        assert b._project_id == "p1"
        assert b._agent_id == "a1"
        assert b._cm is cm
        # Sentinel paths — Postgres backend, no on-disk files.
        assert str(b.db_path) == "/dev/null"
        assert b.encryption_key is None

    def test_scoped_conn_uses_async_project_context_when_available(self) -> None:
        from tapps_brain.async_postgres_private import AsyncPostgresPrivateBackend

        cm = MagicMock()
        cm.async_project_context = MagicMock(return_value="apc-result")
        b = AsyncPostgresPrivateBackend(cm, project_id="proj-x", agent_id="a")
        assert b._scoped_conn() == "apc-result"
        cm.async_project_context.assert_called_once_with("proj-x")

    def test_scoped_conn_falls_back_to_get_async_connection(self) -> None:
        from tapps_brain.async_postgres_private import AsyncPostgresPrivateBackend

        # Mocked test-only manager without async_project_context.
        cm = MagicMock(spec=["get_async_connection"])
        cm.get_async_connection = MagicMock(return_value="gac-result")
        b = AsyncPostgresPrivateBackend(cm, project_id="p", agent_id="a")
        assert b._scoped_conn() == "gac-result"


class TestAsyncSave:
    def test_save_executes_upsert_with_built_params(self) -> None:
        from tapps_brain import _postgres_private_sql as _sql
        from tapps_brain.async_postgres_private import AsyncPostgresPrivateBackend

        cur = _make_async_cursor()
        cm = _make_manager(cur)
        b = AsyncPostgresPrivateBackend(cm, project_id="p1", agent_id="a1")
        entry = _make_entry("save-key")

        asyncio.run(b.save(entry))

        cur.execute.assert_awaited_once()
        sql_arg, params_arg = cur.execute.await_args.args
        # SQL must come from the shared module — single source of truth.
        assert sql_arg is _sql.SAVE_UPSERT_SQL
        # First three params are tenant identity + key — confirms
        # build_save_params was invoked with our project/agent.
        assert params_arg[0] == "p1"
        assert params_arg[1] == "a1"
        assert params_arg[2] == "save-key"


class TestAsyncDelete:
    def test_delete_returns_true_when_rowcount_positive(self) -> None:
        from tapps_brain.async_postgres_private import AsyncPostgresPrivateBackend

        cur = _make_async_cursor(rowcount=1)
        cm = _make_manager(cur)
        b = AsyncPostgresPrivateBackend(cm, project_id="p", agent_id="a")
        assert asyncio.run(b.delete("k")) is True

    def test_delete_returns_false_when_rowcount_zero(self) -> None:
        from tapps_brain.async_postgres_private import AsyncPostgresPrivateBackend

        cur = _make_async_cursor(rowcount=0)
        cm = _make_manager(cur)
        b = AsyncPostgresPrivateBackend(cm, project_id="p", agent_id="a")
        assert asyncio.run(b.delete("k")) is False


class TestAsyncSearch:
    def test_empty_query_returns_empty_without_db_call(self) -> None:
        from tapps_brain.async_postgres_private import AsyncPostgresPrivateBackend

        cur = _make_async_cursor()
        cm = _make_manager(cur)
        b = AsyncPostgresPrivateBackend(cm, project_id="p", agent_id="a")
        assert asyncio.run(b.search("   ")) == []
        cur.execute.assert_not_awaited()

    def test_search_passes_built_sql_and_params(self) -> None:
        from tapps_brain.async_postgres_private import AsyncPostgresPrivateBackend

        cur = _make_async_cursor(fetchall=[], description=[("key",), ("value",)])
        cm = _make_manager(cur)
        b = AsyncPostgresPrivateBackend(cm, project_id="p", agent_id="a")
        asyncio.run(b.search("foo", memory_group="g1", as_of="2026-01-01T00:00:00Z"))

        cur.execute.assert_awaited_once()
        sql_arg, params_arg = cur.execute.await_args.args
        assert "plainto_tsquery" in sql_arg
        # Fixed [query, project_id, agent_id, query] head + builder filters.
        assert params_arg[:4] == ["foo", "p", "a", "foo"]
        # memory_group + as_of pushed into the trailing params.
        assert "g1" in params_arg
        assert params_arg.count("2026-01-01T00:00:00Z") == 2


class TestAsyncKnnSearch:
    def test_empty_embedding_returns_empty_without_db_call(self) -> None:
        from tapps_brain.async_postgres_private import AsyncPostgresPrivateBackend

        cur = _make_async_cursor()
        cm = _make_manager(cur)
        b = AsyncPostgresPrivateBackend(cm, project_id="p", agent_id="a")
        assert asyncio.run(b.knn_search([], k=5)) == []
        cur.execute.assert_not_awaited()

    def test_knn_search_returns_key_distance_pairs(self) -> None:
        from tapps_brain.async_postgres_private import AsyncPostgresPrivateBackend

        cur = _make_async_cursor(fetchall=[("k1", 0.1), ("k2", 0.2)])
        cm = _make_manager(cur)
        b = AsyncPostgresPrivateBackend(cm, project_id="p", agent_id="a")
        out = asyncio.run(b.knn_search([0.0, 1.0, 0.0], k=5))
        assert out == [("k1", 0.1), ("k2", 0.2)]

    def test_knn_search_swallows_db_errors(self) -> None:
        from tapps_brain.async_postgres_private import AsyncPostgresPrivateBackend

        cur = _make_async_cursor()
        cur.execute = AsyncMock(side_effect=RuntimeError("pg down"))
        cm = _make_manager(cur)
        b = AsyncPostgresPrivateBackend(cm, project_id="p", agent_id="a")
        assert asyncio.run(b.knn_search([0.1], k=3)) == []


class TestAsyncVectorRowCount:
    def test_returns_count_from_db(self) -> None:
        from tapps_brain.async_postgres_private import AsyncPostgresPrivateBackend

        cur = _make_async_cursor(fetchone=(42,))
        cm = _make_manager(cur)
        b = AsyncPostgresPrivateBackend(cm, project_id="p", agent_id="a")
        assert asyncio.run(b.vector_row_count()) == 42


class TestAsyncRelations:
    def test_ensure_relations_table_runs_ddl_when_absent(self) -> None:
        from tapps_brain import _postgres_private_sql as _sql
        from tapps_brain.async_postgres_private import AsyncPostgresPrivateBackend

        # First fetchone returns None (table absent) → DDL fires.
        cur = _make_async_cursor(fetchone=None)
        cm = _make_manager(cur)
        b = AsyncPostgresPrivateBackend(cm, project_id="p", agent_id="a")
        asyncio.run(b._ensure_relations_table())

        executed_sqls = [c.args[0] for c in cur.execute.await_args_list]
        assert _sql.PROBE_RELATIONS_TABLE_SQL in executed_sqls
        assert _sql.RELATIONS_DDL in executed_sqls
        assert b._relations_ensured is True

    def test_ensure_relations_table_skips_ddl_when_present(self) -> None:
        from tapps_brain import _postgres_private_sql as _sql
        from tapps_brain.async_postgres_private import AsyncPostgresPrivateBackend

        cur = _make_async_cursor(fetchone=(1,))
        cm = _make_manager(cur)
        b = AsyncPostgresPrivateBackend(cm, project_id="p", agent_id="a")
        asyncio.run(b._ensure_relations_table())

        executed_sqls = [c.args[0] for c in cur.execute.await_args_list]
        assert _sql.PROBE_RELATIONS_TABLE_SQL in executed_sqls
        assert _sql.RELATIONS_DDL not in executed_sqls

    def test_count_relations(self) -> None:
        from tapps_brain.async_postgres_private import AsyncPostgresPrivateBackend

        # First fetchone for probe (table exists), second for count.
        cur = _make_async_cursor()
        cur.fetchone = AsyncMock(side_effect=[(1,), (7,)])
        cm = _make_manager(cur)
        b = AsyncPostgresPrivateBackend(cm, project_id="p", agent_id="a")
        assert asyncio.run(b.count_relations()) == 7


class TestAsyncSchemaVersion:
    def test_returns_db_version(self) -> None:
        from tapps_brain.async_postgres_private import AsyncPostgresPrivateBackend

        cur = _make_async_cursor(fetchone=(5,))
        cm = _make_manager(cur)
        b = AsyncPostgresPrivateBackend(cm, project_id="p", agent_id="a")
        assert asyncio.run(b.get_schema_version()) == 5

    def test_returns_module_default_on_db_error(self) -> None:
        from tapps_brain import _postgres_private_sql as _sql
        from tapps_brain.async_postgres_private import AsyncPostgresPrivateBackend

        cur = _make_async_cursor()
        cur.execute = AsyncMock(side_effect=RuntimeError("db down"))
        cm = _make_manager(cur)
        b = AsyncPostgresPrivateBackend(cm, project_id="p", agent_id="a")
        assert asyncio.run(b.get_schema_version()) == _sql.PRIVATE_SCHEMA_VERSION


class TestAsyncFlywheelMeta:
    def test_get_returns_db_value(self) -> None:
        from tapps_brain.async_postgres_private import AsyncPostgresPrivateBackend

        cur = _make_async_cursor(fetchone=("checkpoint-2",))
        cm = _make_manager(cur)
        b = AsyncPostgresPrivateBackend(cm, project_id="p", agent_id="a")
        assert asyncio.run(b.flywheel_meta_get("ck")) == "checkpoint-2"

    def test_get_returns_none_when_missing(self) -> None:
        from tapps_brain.async_postgres_private import AsyncPostgresPrivateBackend

        cur = _make_async_cursor(fetchone=None)
        cm = _make_manager(cur)
        b = AsyncPostgresPrivateBackend(cm, project_id="p", agent_id="a")
        assert asyncio.run(b.flywheel_meta_get("ck")) is None

    def test_set_swallows_db_errors(self) -> None:
        from tapps_brain.async_postgres_private import AsyncPostgresPrivateBackend

        cur = _make_async_cursor()
        cur.execute = AsyncMock(side_effect=RuntimeError("db down"))
        cm = _make_manager(cur)
        b = AsyncPostgresPrivateBackend(cm, project_id="p", agent_id="a")
        # Must not raise — flywheel set is best-effort.
        asyncio.run(b.flywheel_meta_set("ck", "v"))


class TestAsyncArchive:
    def test_archive_entry_returns_byte_count_on_success(self) -> None:
        from tapps_brain.async_postgres_private import AsyncPostgresPrivateBackend

        cur = _make_async_cursor()
        cm = _make_manager(cur)
        b = AsyncPostgresPrivateBackend(cm, project_id="p", agent_id="a")
        n = asyncio.run(b.archive_entry(_make_entry("gc-key")))
        assert n > 0  # JSON payload always non-empty
        cur.execute.assert_awaited_once()

    def test_archive_entry_returns_zero_on_failure(self) -> None:
        from tapps_brain.async_postgres_private import AsyncPostgresPrivateBackend

        cur = _make_async_cursor()
        cur.execute = AsyncMock(side_effect=RuntimeError("disk full"))
        cm = _make_manager(cur)
        b = AsyncPostgresPrivateBackend(cm, project_id="p", agent_id="a")
        assert asyncio.run(b.archive_entry(_make_entry())) == 0

    def test_total_archive_bytes(self) -> None:
        from tapps_brain.async_postgres_private import AsyncPostgresPrivateBackend

        cur = _make_async_cursor(fetchone=(12345,))
        cm = _make_manager(cur)
        b = AsyncPostgresPrivateBackend(cm, project_id="p", agent_id="a")
        assert asyncio.run(b.total_archive_bytes()) == 12345


class TestAsyncCloseDelegates:
    def test_close_calls_cm_close_async(self) -> None:
        from tapps_brain.async_postgres_private import AsyncPostgresPrivateBackend

        cur = _make_async_cursor()
        cm = _make_manager(cur)
        b = AsyncPostgresPrivateBackend(cm, project_id="p", agent_id="a")
        asyncio.run(b.close())
        cm.close_async.assert_awaited_once()


class TestSqlSharedWithSyncBackend:
    """Belt-and-suspenders: these are the assertions that catch SQL drift.

    If anyone hand-edits a query string into either backend instead of
    updating ``_postgres_private_sql``, one of these references will not
    point at the same constant and the test will fail loudly.
    """

    def test_save_sql_is_shared_constant(self) -> None:
        from tapps_brain import _postgres_private_sql as _sql
        from tapps_brain.async_postgres_private import AsyncPostgresPrivateBackend

        cur = _make_async_cursor()
        cm = _make_manager(cur)
        b = AsyncPostgresPrivateBackend(cm, project_id="p", agent_id="a")
        asyncio.run(b.save(_make_entry()))
        # Identity (not equality) — the literal must be the imported constant.
        assert cur.execute.await_args.args[0] is _sql.SAVE_UPSERT_SQL

    @pytest.mark.parametrize(
        ("call", "expected_sql_attr"),
        [
            ("delete", "DELETE_BY_KEY_SQL"),
            ("vector_row_count", "VECTOR_ROW_COUNT_SQL"),
            ("flywheel_meta_get", "FLYWHEEL_META_GET_SQL"),
        ],
    )
    def test_simple_methods_use_shared_sql(self, call: str, expected_sql_attr: str) -> None:
        from tapps_brain import _postgres_private_sql as _sql
        from tapps_brain.async_postgres_private import AsyncPostgresPrivateBackend

        cur = _make_async_cursor(fetchone=(0,))
        cm = _make_manager(cur)
        b = AsyncPostgresPrivateBackend(cm, project_id="p", agent_id="a")
        method = getattr(b, call)
        if call == "delete":
            asyncio.run(method("k"))
        elif call == "flywheel_meta_get":
            asyncio.run(method("k"))
        else:
            asyncio.run(method())
        assert cur.execute.await_args.args[0] is getattr(_sql, expected_sql_attr)
