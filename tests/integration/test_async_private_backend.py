"""STORY-072.2 — integration tests for AsyncPostgresPrivateBackend.

Behavioral parity with :class:`PostgresPrivateBackend` against a real
Postgres instance pointed to by ``TAPPS_TEST_POSTGRES_DSN`` (set in CI).

Each test does the same operation through both the sync and async
backends against the same physical row, then asserts the observable
results match.  This catches SQL drift between the two backends — if
someone hand-edits a query into one backend without touching
``_postgres_private_sql`` (or the sync backend) the parity check fails.
"""

from __future__ import annotations

import os
import uuid

import pytest

_PG_DSN = os.environ.get("TAPPS_TEST_POSTGRES_DSN", "")
_SKIP_PG = not _PG_DSN

pytestmark = pytest.mark.skipif(_SKIP_PG, reason="TAPPS_TEST_POSTGRES_DSN not set")


def _unique_ids() -> tuple[str, str]:
    """Return per-test ``(project_id, agent_id)`` so tests don't collide."""
    suffix = uuid.uuid4().hex[:8]
    return f"async-test-{suffix}", f"agent-{suffix}"


def _make_entry(key: str, value: str = "hello world") -> object:
    from tapps_brain.models import (
        MemoryEntry,
        MemoryScope,
        MemorySource,
        MemoryTier,
    )

    return MemoryEntry(
        key=key,
        value=value,
        tier=MemoryTier.pattern,
        confidence=0.7,
        source=MemorySource.agent,
        source_agent="async-int-test",
        scope=MemoryScope.project,
        tags=["int", "async"],
    )


@pytest.fixture
def cm() -> object:
    """Per-test PostgresConnectionManager.  Closed at teardown."""
    from tapps_brain.postgres_connection import PostgresConnectionManager
    from tapps_brain.postgres_migrations import apply_private_migrations

    apply_private_migrations(_PG_DSN)
    manager = PostgresConnectionManager(_PG_DSN)
    yield manager
    manager.close()
    # close_async() is awaited inside the async tests where used.


@pytest.mark.asyncio
async def test_save_then_load_all_round_trip(cm: object) -> None:
    """Async save → async load_all returns the same entry."""
    from tapps_brain.async_postgres_private import AsyncPostgresPrivateBackend

    project_id, agent_id = _unique_ids()
    backend = AsyncPostgresPrivateBackend(cm, project_id=project_id, agent_id=agent_id)
    try:
        entry = _make_entry("rt-1")
        await backend.save(entry)
        loaded = await backend.load_all()
        assert any(e.key == "rt-1" and e.value == "hello world" for e in loaded)
    finally:
        await cm.close_async()


@pytest.mark.asyncio
async def test_async_save_visible_to_sync_load_all(cm: object) -> None:
    """An entry saved via the async backend must be visible to the sync backend.

    This is the key parity assertion: both backends hit the same physical
    row and use the same SQL.  Sync backend does not need to be opened
    against the same manager — separate manager instances against the
    same DSN see the same rows under MVCC.
    """
    from tapps_brain.async_postgres_private import AsyncPostgresPrivateBackend
    from tapps_brain.postgres_private import PostgresPrivateBackend

    project_id, agent_id = _unique_ids()
    async_backend = AsyncPostgresPrivateBackend(cm, project_id=project_id, agent_id=agent_id)
    try:
        await async_backend.save(_make_entry("parity-1", value="written-by-async"))

        sync_backend = PostgresPrivateBackend(cm, project_id=project_id, agent_id=agent_id)
        loaded = sync_backend.load_all()
        keys = {e.key: e.value for e in loaded}
        assert keys.get("parity-1") == "written-by-async"
    finally:
        await async_backend.close()


@pytest.mark.asyncio
async def test_delete_round_trip(cm: object) -> None:
    """Async delete returns True for present rows, False for absent."""
    from tapps_brain.async_postgres_private import AsyncPostgresPrivateBackend

    project_id, agent_id = _unique_ids()
    backend = AsyncPostgresPrivateBackend(cm, project_id=project_id, agent_id=agent_id)
    try:
        await backend.save(_make_entry("to-delete"))
        assert await backend.delete("to-delete") is True
        # Second delete is a no-op.
        assert await backend.delete("to-delete") is False
    finally:
        await cm.close_async()


@pytest.mark.asyncio
async def test_search_finds_matching_entry(cm: object) -> None:
    """Async FTS finds the entry written via async save."""
    from tapps_brain.async_postgres_private import AsyncPostgresPrivateBackend

    project_id, agent_id = _unique_ids()
    backend = AsyncPostgresPrivateBackend(cm, project_id=project_id, agent_id=agent_id)
    try:
        await backend.save(_make_entry("search-1", value="distinctive marker word"))
        results = await backend.search("distinctive marker")
        assert any(e.key == "search-1" for e in results)
    finally:
        await cm.close_async()


@pytest.mark.asyncio
async def test_tenant_isolation_via_async_project_context(cm: object) -> None:
    """RLS on private_memories restricts async reads to this tenant.

    Writes one row as project A, one as project B; the async backend
    bound to project A must not see project B's row.  This is the same
    invariant the sync backend gets via ``project_context``.
    """
    from tapps_brain.async_postgres_private import AsyncPostgresPrivateBackend

    suffix = uuid.uuid4().hex[:8]
    project_a = f"async-isolation-A-{suffix}"
    project_b = f"async-isolation-B-{suffix}"
    backend_a = AsyncPostgresPrivateBackend(cm, project_id=project_a, agent_id="a")
    backend_b = AsyncPostgresPrivateBackend(cm, project_id=project_b, agent_id="a")
    try:
        await backend_a.save(_make_entry("only-in-a", value="A's secret"))
        await backend_b.save(_make_entry("only-in-b", value="B's secret"))

        a_loaded = {e.key for e in await backend_a.load_all()}
        b_loaded = {e.key for e in await backend_b.load_all()}

        assert "only-in-a" in a_loaded
        assert "only-in-b" not in a_loaded  # <- the tenant-isolation assertion
        assert "only-in-b" in b_loaded
        assert "only-in-a" not in b_loaded
    finally:
        await cm.close_async()
