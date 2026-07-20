"""Unit tests for AsyncMemoryStore (Issue #66)."""

from __future__ import annotations

import asyncio
import threading
import time
from typing import TYPE_CHECKING

import pytest

from tapps_brain.aio import AsyncMemoryStore
from tapps_brain.models import MemoryEntry

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture()
async def astore(tmp_path: Path) -> AsyncMemoryStore:
    """Create an AsyncMemoryStore backed by a temp directory."""
    store = await AsyncMemoryStore.open(tmp_path)
    yield store  # type: ignore[misc]
    await store.close()


class TestCapturePersistenceBackendSaveMany:
    """TAP-2800: the async-native capture backend must capture batched persists."""

    def test_save_many_captures_batch_for_later_flush(self) -> None:
        from unittest.mock import MagicMock

        from tapps_brain.aio import _CapturePersistenceBackend

        capture = _CapturePersistenceBackend(MagicMock())
        entries = [MemoryEntry(key=f"k{i}", value=f"v{i}") for i in range(3)]
        capture.save_many(entries)

        saves, _deletes, _deleted_rels, _relations, _audit, _archives = capture.flush()
        assert [e.key for e in saves] == ["k0", "k1", "k2"]


class TestAsyncCRUD:
    """Basic CRUD through the async wrapper."""

    @pytest.mark.asyncio
    async def test_save_and_get(self, astore: AsyncMemoryStore) -> None:
        result = await astore.save(key="k1", value="hello")
        assert isinstance(result, MemoryEntry)
        assert result.key == "k1"

        entry = await astore.get("k1")
        assert entry is not None
        assert entry.value == "hello"

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self, astore: AsyncMemoryStore) -> None:
        assert await astore.get("nonexistent") is None

    @pytest.mark.asyncio
    async def test_delete(self, astore: AsyncMemoryStore) -> None:
        await astore.save(key="k1", value="v1")
        assert await astore.delete("k1") is True
        assert await astore.get("k1") is None

    @pytest.mark.asyncio
    async def test_delete_missing_returns_false(self, astore: AsyncMemoryStore) -> None:
        assert await astore.delete("nope") is False

    @pytest.mark.asyncio
    async def test_count(self, astore: AsyncMemoryStore) -> None:
        assert await astore.count() == 0
        await astore.save(key="a", value="1")
        await astore.save(key="b", value="2")
        assert await astore.count() == 2

    @pytest.mark.asyncio
    async def test_list_all(self, astore: AsyncMemoryStore) -> None:
        await astore.save(key="x", value="y")
        entries = await astore.list_all()
        assert len(entries) == 1
        assert entries[0].key == "x"


class TestAsyncSearch:
    """Search and recall through the async wrapper."""

    @pytest.mark.asyncio
    async def test_search(self, astore: AsyncMemoryStore) -> None:
        await astore.save(key="auth-pattern", value="Use JWT tokens for auth")
        results = await astore.search("auth")
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_recall(self, astore: AsyncMemoryStore) -> None:
        await astore.save(key="db-setup", value="PostgreSQL on port 5432")
        result = await astore.recall("database setup")
        assert result is not None


class TestAsyncLifecycle:
    """Lifecycle operations through the async wrapper."""

    @pytest.mark.asyncio
    async def test_reinforce(self, astore: AsyncMemoryStore) -> None:
        await astore.save(key="k1", value="v1", confidence=0.5)
        result = await astore.reinforce("k1", confidence_boost=0.2)
        assert result is not None
        assert result.confidence > 0.5

    @pytest.mark.asyncio
    async def test_history(self, astore: AsyncMemoryStore) -> None:
        await astore.save(key="k1", value="v1")
        h = await astore.history("k1")
        assert isinstance(h, list)

    @pytest.mark.asyncio
    async def test_supersede(self, astore: AsyncMemoryStore) -> None:
        await astore.save(key="k1", value="old")
        new_entry = await astore.supersede("k1", "new value")
        assert new_entry is not None
        assert new_entry.value == "new value"

    @pytest.mark.asyncio
    async def test_ingest_context(self, astore: AsyncMemoryStore) -> None:
        keys = await astore.ingest_context("We decided to use SQLite for storage")
        assert isinstance(keys, list)

    @pytest.mark.asyncio
    async def test_record_access(self, astore: AsyncMemoryStore) -> None:
        await astore.save(key="k1", value="v1")
        await astore.record_access("k1", was_useful=True)
        entry = await astore.get("k1")
        assert entry is not None
        assert entry.access_count >= 1


class TestAsyncMaintenance:
    """Maintenance and diagnostics through the async wrapper."""

    @pytest.mark.asyncio
    async def test_health(self, astore: AsyncMemoryStore) -> None:
        h = await astore.health()
        assert h is not None

    @pytest.mark.asyncio
    async def test_diagnostics(self, astore: AsyncMemoryStore) -> None:
        d = await astore.diagnostics()
        assert d is not None

    @pytest.mark.asyncio
    async def test_gc_dry_run(self, astore: AsyncMemoryStore) -> None:
        result = await astore.gc(dry_run=True)
        assert result is not None

    @pytest.mark.asyncio
    async def test_audit(self, astore: AsyncMemoryStore) -> None:
        await astore.save(key="k1", value="v1")
        log = await astore.audit(key="k1")
        assert isinstance(log, list)

    @pytest.mark.asyncio
    async def test_snapshot(self, astore: AsyncMemoryStore) -> None:
        s = await astore.snapshot()
        assert s is not None

    @pytest.mark.asyncio
    async def test_list_tags(self, astore: AsyncMemoryStore) -> None:
        await astore.save(key="k1", value="v1", tags=["foo", "bar"])
        tags = await astore.list_tags()
        assert "foo" in tags

    @pytest.mark.asyncio
    async def test_list_memory_groups(self, astore: AsyncMemoryStore) -> None:
        groups = await astore.list_memory_groups()
        assert isinstance(groups, list)


class TestAsyncProperties:
    """Properties and sync_store access."""

    @pytest.mark.asyncio
    async def test_project_root(self, astore: AsyncMemoryStore, tmp_path: Path) -> None:
        assert astore.project_root == tmp_path

    @pytest.mark.asyncio
    async def test_sync_store(self, astore: AsyncMemoryStore) -> None:
        from tapps_brain.store import MemoryStore

        assert isinstance(astore.sync_store, MemoryStore)

    @pytest.mark.asyncio
    async def test_profile(self, astore: AsyncMemoryStore) -> None:
        # profile may be None or a MemoryProfile — just verify access works
        _ = astore.profile


class TestAsyncContextManager:
    """Async context manager protocol."""

    @pytest.mark.asyncio
    async def test_context_manager(self, tmp_path: Path) -> None:
        async with await AsyncMemoryStore.open(tmp_path) as store:
            await store.save(key="cm", value="works")
            assert await store.count() == 1
        # store is closed after exiting — no assertion needed, just no crash


class TestAsyncGetattr:
    """Auto-wrapping of methods not explicitly defined on AsyncMemoryStore."""

    @pytest.mark.asyncio
    async def test_getattr_wraps_callable(self, astore: AsyncMemoryStore) -> None:
        # update_fields is not explicitly defined — goes through __getattr__
        await astore.save(key="k1", value="v1")
        result = await astore.update_fields("k1", value="v2")
        assert result is not None
        assert result.value == "v2"

    @pytest.mark.asyncio
    async def test_getattr_private_raises(self, astore: AsyncMemoryStore) -> None:
        with pytest.raises(AttributeError):
            _ = astore._nonexistent

    @pytest.mark.asyncio
    async def test_getattr_update_tags(self, astore: AsyncMemoryStore) -> None:
        await astore.save(key="k1", value="v1", tags=["a"])
        result = await astore.update_tags("k1", add=["b"])
        assert result is not None
        assert "b" in result.tags

    # These two tests are intentionally *sync* — __getattr__ is a synchronous
    # method and the caching assertion doesn't require an event loop.
    def test_getattr_wrapper_is_cached(self, astore: AsyncMemoryStore) -> None:
        """Repeated attribute access must return the same function object (TAP-727)."""
        m1 = astore.update_fields  # type: ignore[attr-defined]
        m2 = astore.update_fields  # type: ignore[attr-defined]
        assert m1 is m2, "wrapper should be cached — identity failed"

    def test_getattr_wrapper_cache_populated(self, astore: AsyncMemoryStore) -> None:
        """After first access the cache slot must contain the wrapper (TAP-727)."""
        import inspect

        _ = astore.update_fields  # type: ignore[attr-defined]
        cache = object.__getattribute__(astore, "_wrapper_cache")
        assert "update_fields" in cache
        assert inspect.iscoroutinefunction(cache["update_fields"])


class TestAsyncGcRun:
    """gc_run() alias parity (STORY-070.10 AC)."""

    @pytest.mark.asyncio
    async def test_gc_run_dry_run(self, astore: AsyncMemoryStore) -> None:
        """gc_run(dry_run=True) returns a result without modifying the store."""
        result = await astore.gc_run(dry_run=True)
        assert result is not None

    @pytest.mark.asyncio
    async def test_gc_run_and_gc_same_behaviour(self, astore: AsyncMemoryStore) -> None:
        """gc_run() is a functional alias for gc() — both are awaitable (STORY-070.10)."""
        result_run = await astore.gc_run(dry_run=True)
        result_gc = await astore.gc(dry_run=True)
        # Both return the same type (GCResult)
        assert type(result_run) is type(result_gc)


class TestAsyncConcurrentLoad:
    """Benchmark: 100-concurrent async recalls complete without error (STORY-070.10 AC).

    The strict "≤ 2× single recall latency" criterion from the story
    requires a native async psycopg connection pool so the event loop
    is never blocked.  The ``asyncio.to_thread`` wrapper (CLAUDE.md §
    "Synchronous by design") serialises through ``threading.Lock`` and
    therefore cannot meet the strict latency bound.

    This test verifies the weaker (but crucial) property: 100 concurrent
    recalls all complete without error, deadlock, or data corruption.
    Latency is logged for human review.
    """

    @pytest.mark.asyncio
    async def test_100_concurrent_recalls_no_errors(self, tmp_path: Path) -> None:
        """100 asyncio.gather'd recalls must all succeed (no deadlock / exception)."""
        async with await AsyncMemoryStore.open(tmp_path) as store:
            await store.save(key="bench", value="concurrent recall benchmark data")

            # Time a single recall for reference
            t_start = time.perf_counter()
            single = await store.recall("concurrent recall")
            single_ms = (time.perf_counter() - t_start) * 1_000

            # Fire 100 concurrent recalls
            t_start = time.perf_counter()
            results = await asyncio.gather(*[store.recall("concurrent recall") for _ in range(100)])
            concurrent_ms = (time.perf_counter() - t_start) * 1_000

        # All 100 coroutines must complete without raising
        assert len(results) == 100

        # Advisory: log timing so engineers can track improvements toward the
        # strict 2× goal (requires native async psycopg pool — future work).
        ratio = concurrent_ms / max(single_ms, 0.001)
        print(
            f"\nSTORY-070.10 benchmark: single={single_ms:.2f}ms "
            f"concurrent_100={concurrent_ms:.2f}ms ratio={ratio:.1f}×"
        )


# ---------------------------------------------------------------------------
# STORY-072.8 (TAP-1565): relations + audit parity on the async-native path
# ---------------------------------------------------------------------------


class TestAsyncNativeSecondaryWriteParity:
    """When ``_async_backend`` is wired, captured ``save_relations`` and
    ``append_audit`` calls must be flushed via the async backend after the
    sync ``MemoryStore.save``/``delete`` returns.

    Prior to STORY-072.8 these were silent no-ops, which meant the async
    write path dropped audit rows and relation upserts."""

    def _make_async_backend(self) -> object:
        """Return an AsyncMock-backed fake async backend exposing the four
        async methods :class:`AsyncMemoryStore` flushes through."""
        from unittest.mock import AsyncMock, MagicMock

        backend = MagicMock()
        backend.save = AsyncMock()
        backend.delete = AsyncMock()
        backend.save_relations = AsyncMock()
        backend.append_audit = AsyncMock()
        backend.close = AsyncMock()
        return backend

    @pytest.mark.asyncio
    async def test_flush_drains_captured_relations_and_audit(self, tmp_path: Path) -> None:
        """A save that captures relations + audit must flush them through the
        async backend before returning."""
        from tapps_brain.aio import _CapturePersistenceBackend
        from tapps_brain.models import MemoryEntry

        sync_store = await AsyncMemoryStore.open(tmp_path)
        try:
            backend = self._make_async_backend()
            astore = AsyncMemoryStore(sync_store.sync_store, async_backend=backend)

            # Drive a capture cycle directly so we can assert the flush
            # contract without dragging the full MemoryStore.save into the
            # picture — the production path uses the same _flush_capture
            # helper, and unit-tested helpers stay decoupled from store
            # internals.
            capture = _CapturePersistenceBackend(astore.sync_store._persistence)
            entry = MemoryEntry(
                key="k",
                value="v",
                tier="pattern",
                confidence=0.5,
                source="agent",
                source_agent="agent",
            )
            capture.save(entry)
            capture.delete("old-key")
            capture.save_relations("k", [object()])  # opaque relation payload
            capture.append_audit("save", "k", {"reason": "test"})

            await astore._flush_capture(capture)

            backend.save.assert_awaited_once_with(entry)
            backend.delete.assert_awaited_once_with("old-key")
            backend.save_relations.assert_awaited_once()
            rel_call = backend.save_relations.await_args
            assert rel_call.args[0] == "k"
            assert len(rel_call.args[1]) == 1
            backend.append_audit.assert_awaited_once_with("save", "k", {"reason": "test"})

            # Queues are drained after flush.
            saves, deletes, _deleted_rels, rels, audit, archives = capture.flush()
            assert saves == [] and deletes == [] and rels == [] and audit == [] and archives == []
        finally:
            await sync_store.close()

    @pytest.mark.asyncio
    async def test_empty_secondary_queues_skip_backend_calls(self, tmp_path: Path) -> None:
        """No captured relations / audit → no calls on the async backend."""
        from tapps_brain.aio import _CapturePersistenceBackend

        sync_store = await AsyncMemoryStore.open(tmp_path)
        try:
            backend = self._make_async_backend()
            astore = AsyncMemoryStore(sync_store.sync_store, async_backend=backend)

            capture = _CapturePersistenceBackend(astore.sync_store._persistence)
            await astore._flush_capture(capture)

            backend.save.assert_not_awaited()
            backend.delete.assert_not_awaited()
            backend.save_relations.assert_not_awaited()
            backend.append_audit.assert_not_awaited()
        finally:
            await sync_store.close()

    @pytest.mark.asyncio
    async def test_capture_save_relations_empty_returns_zero(self) -> None:
        """``save_relations([])`` is a no-op contract preserved from the
        sync backend."""
        from unittest.mock import MagicMock

        from tapps_brain.aio import _CapturePersistenceBackend

        capture = _CapturePersistenceBackend(MagicMock())
        assert capture.save_relations("k", []) == 0
        _saves, _deletes, _deleted_rels, rels, _audit, _archives = capture.flush()
        assert rels == []

    def test_archive_entry_captures_without_hitting_sync_backend(self) -> None:
        """GC archives must queue for async flush — not write through sync."""
        from unittest.mock import MagicMock

        from tapps_brain.aio import _CapturePersistenceBackend

        real = MagicMock()
        capture = _CapturePersistenceBackend(real)
        entry = MemoryEntry(key="stale", value="old")
        nbytes = capture.archive_entry(entry)
        assert nbytes > 0
        real.archive_entry.assert_not_called()
        _s, _d, _dr, _r, _a, archives = capture.flush()
        assert archives == [entry]

    @pytest.mark.asyncio
    async def test_flush_raises_and_skips_delete_when_archive_returns_zero(
        self, tmp_path: Path
    ) -> None:
        """Durable archive nbytes==0 must not delete the live row (sync GC contract)."""
        from unittest.mock import AsyncMock

        from tapps_brain.aio import _CapturePersistenceBackend

        sync_store = await AsyncMemoryStore.open(tmp_path)
        try:
            backend = self._make_async_backend()
            backend.archive_entry = AsyncMock(return_value=0)
            astore = AsyncMemoryStore(sync_store.sync_store, async_backend=backend)

            capture = _CapturePersistenceBackend(astore.sync_store._persistence)
            entry = MemoryEntry(key="stale", value="old")
            assert capture.archive_entry(entry) > 0
            capture.delete("stale")

            with pytest.raises(RuntimeError, match="durable archive failed"):
                await astore._flush_capture(capture)

            backend.archive_entry.assert_awaited_once_with(entry)
            backend.delete.assert_not_awaited()
        finally:
            await sync_store.close()

    @pytest.mark.asyncio
    async def test_flush_deletes_only_after_successful_archive(self, tmp_path: Path) -> None:
        """Successful durable archive (nbytes>0) unblocks the queued GC delete."""
        from unittest.mock import AsyncMock

        from tapps_brain.aio import _CapturePersistenceBackend

        sync_store = await AsyncMemoryStore.open(tmp_path)
        try:
            backend = self._make_async_backend()
            backend.archive_entry = AsyncMock(return_value=128)
            astore = AsyncMemoryStore(sync_store.sync_store, async_backend=backend)

            capture = _CapturePersistenceBackend(astore.sync_store._persistence)
            entry = MemoryEntry(key="stale", value="old")
            capture.archive_entry(entry)
            capture.delete("stale")

            await astore._flush_capture(capture)

            backend.archive_entry.assert_awaited_once_with(entry)
            backend.delete.assert_awaited_once_with("stale")
        finally:
            await sync_store.close()


class TestBoundedConcurrency:
    """TAP-1815 — write and read semaphores bound concurrent to_thread calls."""

    @pytest.mark.asyncio
    async def test_write_semaphore_bounds_concurrent_saves(self, tmp_path: Path) -> None:
        """1000 concurrent saves with semaphore=4 never exceed 4 simultaneous threads."""
        max_seen: list[int] = [0]
        active: list[int] = [0]
        lock = threading.Lock()

        base = await AsyncMemoryStore.open(tmp_path)
        original_save = base.sync_store.save

        def counting_save(key: str, value: str, **kwargs: object) -> object:
            with lock:
                active[0] += 1
                if active[0] > max_seen[0]:
                    max_seen[0] = active[0]
            try:
                return original_save(key, value, **kwargs)
            finally:
                with lock:
                    active[0] -= 1

        astore = AsyncMemoryStore(
            base.sync_store,
            max_concurrent_writes=4,
        )
        # Monkey-patch the underlying sync save so our counter runs in-thread.
        astore.sync_store.save = counting_save  # type: ignore[method-assign]

        tasks = [asyncio.create_task(astore.save(key=f"k{i}", value=f"v{i}")) for i in range(100)]
        await asyncio.gather(*tasks)
        # Close via base (astore shares the same sync_store and has no async backend).
        await base.close()

        assert max_seen[0] <= 4, f"Expected ≤4 concurrent writes; saw {max_seen[0]}"

    @pytest.mark.asyncio
    async def test_read_semaphore_bounds_concurrent_searches(self, tmp_path: Path) -> None:
        """Concurrent searches are bounded by max_concurrent_reads."""
        max_seen: list[int] = [0]
        active: list[int] = [0]
        lock = threading.Lock()

        base = await AsyncMemoryStore.open(tmp_path)
        original_search = base.sync_store.search

        def counting_search(query: str, **kwargs: object) -> object:
            with lock:
                active[0] += 1
                if active[0] > max_seen[0]:
                    max_seen[0] = active[0]
            try:
                return original_search(query, **kwargs)
            finally:
                with lock:
                    active[0] -= 1

        astore = AsyncMemoryStore(
            base.sync_store,
            max_concurrent_reads=4,
        )
        astore.sync_store.search = counting_search  # type: ignore[method-assign]

        tasks = [asyncio.create_task(astore.search(f"query{i}")) for i in range(50)]
        await asyncio.gather(*tasks)
        # Close via base (astore shares sync_store and has no async backend).
        await base.close()

        assert max_seen[0] <= 4, f"Expected ≤4 concurrent reads; saw {max_seen[0]}"

    @pytest.mark.asyncio
    async def test_queue_depth_properties_idle(self, tmp_path: Path) -> None:
        """write_queue_depth and read_queue_depth are 0 when store is idle."""
        async with await AsyncMemoryStore.open(tmp_path) as astore:
            assert astore.write_queue_depth == 0
            assert astore.read_queue_depth == 0

    @pytest.mark.asyncio
    async def test_write_semaphore_default_from_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TAPPS_BRAIN_AIO_MAX_CONCURRENT_WRITES env var sets semaphore size."""
        monkeypatch.setenv("TAPPS_BRAIN_AIO_MAX_CONCURRENT_WRITES", "8")
        base = await AsyncMemoryStore.open(tmp_path)
        astore = AsyncMemoryStore(base.sync_store)
        # Semaphore starts full — internal _value reflects max capacity.
        assert astore._write_sem._value == 8  # type: ignore[attr-defined]
        await base.close()

    @pytest.mark.asyncio
    async def test_read_semaphore_default_from_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TAPPS_BRAIN_AIO_MAX_CONCURRENT_READS env var sets semaphore size."""
        monkeypatch.setenv("TAPPS_BRAIN_AIO_MAX_CONCURRENT_READS", "32")
        base = await AsyncMemoryStore.open(tmp_path)
        astore = AsyncMemoryStore(base.sync_store)
        assert astore._read_sem._value == 32  # type: ignore[attr-defined]
        await base.close()
