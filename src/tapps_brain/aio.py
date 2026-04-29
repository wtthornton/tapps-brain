"""Async wrapper for MemoryStore (Issue #66).

Default path: thin adapter using ``asyncio.to_thread()`` around every public
``MemoryStore`` method.  Thread-safe: ``MemoryStore`` already serializes
via ``threading.Lock``, so ``to_thread()`` simply keeps the event loop
unblocked — it does NOT add parallelism to store operations.

Async-native path (``TAPPS_BRAIN_ASYNC_NATIVE=1``): hot-path writes are
intercepted so the actual Postgres I/O goes through
``AsyncPostgresPrivateBackend`` (native ``psycopg_pool.AsyncConnectionPool``)
instead of a thread-pool thread.  The MemoryStore's in-memory cache and
business logic still run in ``to_thread``; only the persistence layer is
replaced.  Relations, audit, and other secondary writes are deferred in this
mode (known limitation — tracked in EPIC-072 roadmap).

Usage::

    from tapps_brain.aio import AsyncMemoryStore

    async with await AsyncMemoryStore.open(project_root) as store:
        await store.save(key="greeting", value="hello")
        entry = await store.get("greeting")
"""

from __future__ import annotations

import asyncio
import os
import threading
from typing import TYPE_CHECKING, Any

from tapps_brain.store import MemoryStore

if TYPE_CHECKING:
    from pathlib import Path

    from tapps_brain.models import MemoryEntry
    from tapps_brain.postgres_private import AsyncPostgresPrivateBackend


class _CapturePersistenceBackend:
    """Intercepts save/delete calls during a MemoryStore operation.

    Used in async-native mode to prevent MemoryStore from blocking a thread
    pool thread on the Postgres write.  The captured entries are flushed via
    ``AsyncPostgresPrivateBackend`` after the ``to_thread`` call returns.

    All read operations and secondary writes (audit, relations) delegate to
    the real backend so MemoryStore's read paths continue to work.  Audit
    ``append_audit`` and ``save_relations`` are no-ops — these are
    best-effort writes that can be accepted as missing in the MVP of native
    mode (EPIC-072 roadmap item).
    """

    def __init__(self, real: Any) -> None:
        self._real = real
        self._saved: list[MemoryEntry] = []
        self._deleted: list[str] = []
        self._lock = threading.Lock()

    # --- Captured writes ---------------------------------------------------

    def save(self, entry: MemoryEntry) -> None:
        with self._lock:
            self._saved.append(entry)

    def delete(self, key: str) -> bool:
        with self._lock:
            self._saved = [e for e in self._saved if e.key != key]
            self._deleted.append(key)
        return True

    # --- Secondary writes (no-op in native mode) ----------------------------

    def append_audit(self, *args: Any, **kwargs: Any) -> None:
        pass

    def save_relations(self, *args: Any, **kwargs: Any) -> int:
        return 0

    # --- Read operations (delegate to real backend) -------------------------

    def load_all(self, **kwargs: Any) -> list[Any]:
        return list(self._real.load_all(**kwargs))

    def search(self, *args: Any, **kwargs: Any) -> list[Any]:
        return list(self._real.search(*args, **kwargs))

    def knn_search(self, *args: Any, **kwargs: Any) -> list[Any]:
        return list(self._real.knn_search(*args, **kwargs))

    def vector_row_count(self) -> int:
        return int(self._real.vector_row_count())

    def list_relations(self) -> list[Any]:
        return list(self._real.list_relations())

    def count_relations(self) -> int:
        return int(self._real.count_relations())

    def load_relations(self, key: str) -> list[Any]:
        return list(self._real.load_relations(key))

    def get_schema_version(self) -> int:
        return int(self._real.get_schema_version())

    def verify_expected_indexes(self) -> list[str]:
        vr = getattr(self._real, "verify_expected_indexes", None)
        return list(vr()) if callable(vr) else []

    def query_audit(self, **kwargs: Any) -> list[Any]:
        qa = getattr(self._real, "query_audit", None)
        return list(qa(**kwargs)) if callable(qa) else []

    def flywheel_meta_get(self, key: str) -> str | None:
        fn = getattr(self._real, "flywheel_meta_get", None)
        result = fn(key) if callable(fn) else None
        return str(result) if result is not None else None

    def flywheel_meta_set(self, key: str, value: str) -> None:
        fn = getattr(self._real, "flywheel_meta_set", None)
        if callable(fn):
            fn(key, value)

    def archive_entry(self, entry: Any) -> int:
        fn = getattr(self._real, "archive_entry", None)
        return fn(entry) if callable(fn) else 0

    def list_archive(self, **kwargs: Any) -> list[Any]:
        fn = getattr(self._real, "list_archive", None)
        return fn(**kwargs) if callable(fn) else []

    def total_archive_bytes(self) -> int:
        fn = getattr(self._real, "total_archive_bytes", None)
        return fn() if callable(fn) else 0

    def close(self) -> None:
        pass

    @property
    def store_dir(self) -> Any:
        return self._real.store_dir

    @property
    def db_path(self) -> Any:
        return self._real.db_path

    @property
    def audit_path(self) -> Any:
        return self._real.audit_path

    @property
    def encryption_key(self) -> str | None:
        key = self._real.encryption_key
        return str(key) if key is not None else None

    def flush(self) -> tuple[list[MemoryEntry], list[str]]:
        """Return captured saves/deletes and clear the queues."""
        with self._lock:
            saves = list(self._saved)
            deletes = list(self._deleted)
            self._saved.clear()
            self._deleted.clear()
        return saves, deletes


class AsyncMemoryStore:
    """Async facade over :class:`MemoryStore`.

    Every public method delegates to the underlying sync store via
    :func:`asyncio.to_thread` (default) or, when
    ``TAPPS_BRAIN_ASYNC_NATIVE=1`` is set, intercepts the persistence layer
    so Postgres writes go through
    :class:`~tapps_brain.postgres_private.AsyncPostgresPrivateBackend`
    without blocking a thread pool thread.
    """

    __slots__ = ("_async_backend", "_async_native", "_native_lock", "_store", "_wrapper_cache")

    def __init__(
        self,
        store: MemoryStore,
        *,
        async_backend: AsyncPostgresPrivateBackend | None = None,
    ) -> None:
        self._store = store
        self._wrapper_cache: dict[str, Any] = {}
        self._async_backend = async_backend
        self._async_native = async_backend is not None
        # asyncio.Lock serialises the persistence-swap in native save/delete.
        self._native_lock: asyncio.Lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    async def open(cls, project_root: Path, **kwargs: Any) -> AsyncMemoryStore:
        """Create a ``MemoryStore`` in a worker thread and return the wrapper.

        When ``TAPPS_BRAIN_ASYNC_NATIVE=1`` is set and a PostgreSQL DSN is
        available, also creates an :class:`AsyncPostgresPrivateBackend` that
        takes over persistence so DB writes no longer block thread-pool
        threads.
        """
        store = await asyncio.to_thread(MemoryStore, project_root, **kwargs)

        async_backend = None
        if os.environ.get("TAPPS_BRAIN_ASYNC_NATIVE") == "1":
            dsn = (
                os.environ.get("TAPPS_BRAIN_DATABASE_URL")
                or os.environ.get("TAPPS_BRAIN_HIVE_DSN")
                or ""
            ).strip()
            if dsn.startswith(("postgres://", "postgresql://")):
                from tapps_brain.backends import create_async_private_backend

                # Derive project_id / agent_id from the sync store.
                project_id = getattr(store, "_project_id", None) or ""
                agent_id = getattr(store, "_agent_id", None) or ""
                if project_id and agent_id:
                    async_backend = create_async_private_backend(
                        dsn, project_id=project_id, agent_id=agent_id
                    )

        return cls(store, async_backend=async_backend)

    # ------------------------------------------------------------------
    # Properties (sync — no I/O)
    # ------------------------------------------------------------------

    @property
    def sync_store(self) -> MemoryStore:
        """Access the underlying synchronous store."""
        return self._store

    @property
    def project_root(self) -> Path:
        return self._store.project_root

    @property
    def profile(self) -> Any:
        return self._store.profile

    # ------------------------------------------------------------------
    # Native-mode helpers
    # ------------------------------------------------------------------

    async def _native_save(self, key: str, value: str, **kwargs: Any) -> Any:
        """Run MemoryStore.save via to_thread but capture the DB write.

        Business logic and in-memory cache update run in the thread pool
        (MemoryStore lock applies).  The actual Postgres write is flushed
        via :attr:`_async_backend` after the thread returns.
        """
        capture = _CapturePersistenceBackend(self._store._persistence)
        async with self._native_lock:
            old = self._store._persistence
            self._store._persistence = capture
            try:
                result = await asyncio.to_thread(self._store.save, key, value, **kwargs)
            finally:
                self._store._persistence = old
        # Flush outside the lock so concurrent saves can proceed.
        saves, deletes = capture.flush()
        assert self._async_backend is not None
        for entry in saves:
            await self._async_backend.save(entry)
        for k in deletes:
            await self._async_backend.delete(k)
        return result

    async def _native_delete(self, key: str) -> bool:
        """Run MemoryStore.delete via to_thread, flushing via async backend."""
        capture = _CapturePersistenceBackend(self._store._persistence)
        async with self._native_lock:
            old = self._store._persistence
            self._store._persistence = capture
            try:
                result = await asyncio.to_thread(self._store.delete, key)
            finally:
                self._store._persistence = old
        saves, deletes = capture.flush()
        assert self._async_backend is not None
        for entry in saves:
            await self._async_backend.save(entry)
        for k in deletes:
            await self._async_backend.delete(k)
        return result

    # ------------------------------------------------------------------
    # Primary methods (explicit signatures for IDE discoverability)
    # ------------------------------------------------------------------

    async def save(self, key: str, value: str, **kwargs: Any) -> Any:
        """Async version of :meth:`MemoryStore.save`."""
        if self._async_native:
            return await self._native_save(key, value, **kwargs)
        return await asyncio.to_thread(self._store.save, key, value, **kwargs)

    async def get(self, key: str, **kwargs: Any) -> Any:
        """Async version of :meth:`MemoryStore.get`."""
        return await asyncio.to_thread(self._store.get, key, **kwargs)

    async def delete(self, key: str) -> bool:
        """Async version of :meth:`MemoryStore.delete`."""
        if self._async_native:
            return await self._native_delete(key)
        return await asyncio.to_thread(self._store.delete, key)

    async def search(self, query: str, **kwargs: Any) -> list[Any]:
        """Async version of :meth:`MemoryStore.search`."""
        return await asyncio.to_thread(self._store.search, query, **kwargs)

    async def list_all(self, **kwargs: Any) -> list[Any]:
        """Async version of :meth:`MemoryStore.list_all`."""
        return await asyncio.to_thread(self._store.list_all, **kwargs)

    async def list_memory_groups(self) -> list[str]:
        """Async version of :meth:`MemoryStore.list_memory_groups`."""
        return await asyncio.to_thread(self._store.list_memory_groups)

    async def recall(self, message: str, **kwargs: Any) -> Any:
        """Async version of :meth:`MemoryStore.recall`."""
        return await asyncio.to_thread(self._store.recall, message, **kwargs)

    async def reinforce(self, key: str, **kwargs: Any) -> Any:
        """Async version of :meth:`MemoryStore.reinforce`."""
        return await asyncio.to_thread(self._store.reinforce, key, **kwargs)

    async def ingest_context(self, context: str, **kwargs: Any) -> list[str]:
        """Async version of :meth:`MemoryStore.ingest_context`."""
        return await asyncio.to_thread(self._store.ingest_context, context, **kwargs)

    async def record_access(self, key: str, was_useful: bool) -> None:
        """Async version of :meth:`MemoryStore.record_access`."""
        await asyncio.to_thread(self._store.record_access, key, was_useful)

    async def history(self, key: str) -> list[Any]:
        """Async version of :meth:`MemoryStore.history`."""
        return await asyncio.to_thread(self._store.history, key)

    async def health(self) -> Any:
        """Async version of :meth:`MemoryStore.health`."""
        return await asyncio.to_thread(self._store.health)

    async def audit(self, **kwargs: Any) -> list[dict[str, Any]]:
        """Async version of :meth:`MemoryStore.audit`."""
        return await asyncio.to_thread(self._store.audit, **kwargs)

    async def diagnostics(self, **kwargs: Any) -> Any:
        """Async version of :meth:`MemoryStore.diagnostics`."""
        return await asyncio.to_thread(self._store.diagnostics, **kwargs)

    async def count(self) -> int:
        """Async version of :meth:`MemoryStore.count`."""
        return await asyncio.to_thread(self._store.count)

    async def snapshot(self) -> Any:
        """Async version of :meth:`MemoryStore.snapshot`."""
        return await asyncio.to_thread(self._store.snapshot)

    async def gc(self, *, dry_run: bool = False) -> Any:
        """Async version of :meth:`MemoryStore.gc` (STORY-070.10)."""
        return await asyncio.to_thread(self._store.gc, dry_run=dry_run)

    async def supersede(self, old_key: str, new_value: str, **kwargs: Any) -> Any:
        """Async version of :meth:`MemoryStore.supersede` (STORY-070.10)."""
        return await asyncio.to_thread(self._store.supersede, old_key, new_value, **kwargs)

    async def get_gc_config(self) -> Any:
        """Async version of :meth:`MemoryStore.get_gc_config` (STORY-070.10)."""
        return await asyncio.to_thread(self._store.get_gc_config)

    async def set_gc_config(self, config: Any) -> None:
        """Async version of :meth:`MemoryStore.set_gc_config` (STORY-070.10)."""
        await asyncio.to_thread(self._store.set_gc_config, config)

    async def get_consolidation_config(self) -> Any:
        """Async version of :meth:`MemoryStore.get_consolidation_config` (STORY-070.10)."""
        return await asyncio.to_thread(self._store.get_consolidation_config)

    async def set_consolidation_config(self, config: Any) -> None:
        """Async version of :meth:`MemoryStore.set_consolidation_config` (STORY-070.10)."""
        await asyncio.to_thread(self._store.set_consolidation_config, config)

    async def get_relations(self, key: str) -> Any:
        """Async version of :meth:`MemoryStore.get_relations` (STORY-070.10)."""
        return await asyncio.to_thread(self._store.get_relations, key)

    async def get_relations_batch(self, keys: list[str]) -> Any:
        """Async version of :meth:`MemoryStore.get_relations_batch` (STORY-070.10)."""
        return await asyncio.to_thread(self._store.get_relations_batch, keys)

    async def find_related(self, key: str, *, max_hops: int = 2) -> Any:
        """Async version of :meth:`MemoryStore.find_related` (STORY-070.10)."""
        return await asyncio.to_thread(self._store.find_related, key, max_hops=max_hops)

    async def query_relations(self, **kwargs: Any) -> Any:
        """Async version of :meth:`MemoryStore.query_relations` (STORY-070.10)."""
        return await asyncio.to_thread(self._store.query_relations, **kwargs)

    async def list_tags(self) -> Any:
        """Async version of :meth:`MemoryStore.list_tags` (STORY-070.10)."""
        return await asyncio.to_thread(self._store.list_tags)

    async def update_tags(self, key: str, **kwargs: Any) -> Any:
        """Async version of :meth:`MemoryStore.update_tags` (STORY-070.10)."""
        return await asyncio.to_thread(self._store.update_tags, key, **kwargs)

    async def entries_by_tag(self, tag: str, **kwargs: Any) -> Any:
        """Async version of :meth:`MemoryStore.entries_by_tag` (STORY-070.10)."""
        return await asyncio.to_thread(self._store.entries_by_tag, tag, **kwargs)

    async def index_session(self, session_id: str, chunks: list[str]) -> Any:
        """Async version of :meth:`MemoryStore.index_session` (STORY-070.10)."""
        return await asyncio.to_thread(self._store.index_session, session_id, chunks)

    async def search_sessions(self, query: str, **kwargs: Any) -> Any:
        """Async version of :meth:`MemoryStore.search_sessions` (STORY-070.10)."""
        return await asyncio.to_thread(self._store.search_sessions, query, **kwargs)

    async def list_gc_stale_details(self) -> Any:
        """Async version of :meth:`MemoryStore.list_gc_stale_details` (STORY-070.10)."""
        return await asyncio.to_thread(self._store.list_gc_stale_details)

    async def generate_report(self, *, period_days: int = 7) -> Any:
        """Async version of :meth:`MemoryStore.generate_report` (STORY-070.10)."""
        return await asyncio.to_thread(self._store.generate_report, period_days=period_days)

    async def latest_quality_report(self) -> Any:
        """Async version of :meth:`MemoryStore.latest_quality_report` (STORY-070.10)."""
        return await asyncio.to_thread(self._store.latest_quality_report)

    async def gc_run(self, *, dry_run: bool = False) -> Any:
        """Async version of :meth:`MemoryStore.gc` (alias for STORY-070.10 parity).

        ``gc_run()`` is an explicit alias matching the method name used by
        AgentForge callers.  Internally delegates to ``gc(dry_run=dry_run)``.
        """
        return await asyncio.to_thread(self._store.gc, dry_run=dry_run)

    async def close(self) -> None:
        """Async version of :meth:`MemoryStore.close`."""
        if self._async_backend is not None:
            await self._async_backend.close()
        await asyncio.to_thread(self._store.close)

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> AsyncMemoryStore:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Auto-wrapping for remaining public methods
    # ------------------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        """Auto-wrap any remaining sync MemoryStore public method as async.

        Properties and private attributes are not wrapped — only callable
        public methods produce an async wrapper.

        Generated wrappers are cached on the instance so repeated attribute
        access returns the same function object (referential stability for
        mocking) and avoids per-call allocation on hot paths.
        """
        if name.startswith("_"):
            raise AttributeError(name)

        # Return cached wrapper if already built for this name.
        # Use object.__getattribute__ to avoid recursion via __getattr__
        # in the unlikely event the slot is accessed before __init__ sets it.
        try:
            cache: dict[str, Any] = object.__getattribute__(self, "_wrapper_cache")
        except AttributeError:
            # Pre-__init__ access (e.g. subclass calls __getattr__ before
            # super().__init__).  Seed the cache slot so subsequent accesses
            # benefit from caching too.
            cache = {}
            object.__setattr__(self, "_wrapper_cache", cache)

        if name in cache:
            return cache[name]

        attr = getattr(self._store, name)
        if not callable(attr):
            return attr

        async def _async_proxy(*args: Any, **kwargs: Any) -> Any:
            return await asyncio.to_thread(attr, *args, **kwargs)

        _async_proxy.__name__ = name
        _async_proxy.__qualname__ = f"AsyncMemoryStore.{name}"
        _async_proxy.__doc__ = f"Async version of :meth:`MemoryStore.{name}`."
        cache[name] = _async_proxy
        return _async_proxy
