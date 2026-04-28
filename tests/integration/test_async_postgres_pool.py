"""STORY-072.1 — integration tests for the async-native Postgres pool.

Requires a live Postgres instance pointed to by ``TAPPS_TEST_POSTGRES_DSN``
(set in CI; locally `make hive-deploy` brings up the stack and exports
the DSN via `docker/.env`).

These tests verify the wiring against a real ``psycopg_pool.AsyncConnectionPool``;
mocked unit-level behaviour lives in ``tests/unit/test_postgres_connection.py``.
"""

from __future__ import annotations

import os

import pytest

_PG_DSN = os.environ.get("TAPPS_TEST_POSTGRES_DSN", "")
_SKIP_PG = not _PG_DSN

pytestmark = pytest.mark.skipif(_SKIP_PG, reason="TAPPS_TEST_POSTGRES_DSN not set")


@pytest.mark.asyncio
async def test_async_pool_round_trip_query() -> None:
    """Async pool opens, executes a real query, and closes cleanly."""
    from tapps_brain.postgres_connection import PostgresConnectionManager

    cm = PostgresConnectionManager(_PG_DSN)
    try:
        async with cm.get_async_connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 42")
                row = await cur.fetchone()
                assert row is not None
                assert row[0] == 42
        assert cm.is_async_open is True
    finally:
        await cm.close_async()
    assert cm.is_async_open is False


@pytest.mark.asyncio
async def test_async_pool_get_async_pool_returns_pool_object() -> None:
    """``get_async_pool()`` exposes the raw pool for advanced callers."""
    from tapps_brain.postgres_connection import PostgresConnectionManager

    cm = PostgresConnectionManager(_PG_DSN)
    try:
        pool = await cm.get_async_pool()
        assert pool is not None
        # Pool surface that EPIC-072 callers will rely on.
        assert hasattr(pool, "connection")
        assert hasattr(pool, "close")
        # Stats should now report the pool as available.
        stats = cm.get_async_pool_stats()
        assert stats["pool_stats_available"] is True
        assert stats["pool_size"] >= cm._min_size
    finally:
        await cm.close_async()


@pytest.mark.asyncio
async def test_session_var_reset_isolates_borrows() -> None:
    """The async reset callback wipes ``app.project_id`` between checkouts.

    Sets ``app.project_id`` on one borrow, returns the connection to the
    pool, then re-borrows and asserts the variable is empty.  Same
    invariant the sync pool enforces in ``_reset_session_vars`` (TAP-514) —
    if this regresses, tenant identity leaks across agent calls.
    """
    from tapps_brain.postgres_connection import PostgresConnectionManager

    cm = PostgresConnectionManager(
        _PG_DSN,
        # Force min_size=max_size=1 so we provably reuse the same physical
        # connection on the second checkout — otherwise the assertion is
        # ambiguous (a fresh connection would also report empty).
        min_size=1,
        max_size=1,
    )
    try:
        async with cm.get_async_connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SET app.project_id = 'tenant-A'")
                await cur.execute("SELECT current_setting('app.project_id', true)")
                row = await cur.fetchone()
                assert row is not None and row[0] == "tenant-A"

        async with cm.get_async_connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT current_setting('app.project_id', true)")
                row = await cur.fetchone()
                # `true` flag returns NULL or '' when the GUC is unset/RESET;
                # both indicate the reset callback wiped it.
                assert row is not None
                assert row[0] in (None, "")
    finally:
        await cm.close_async()
