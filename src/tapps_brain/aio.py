"""Async wrapper for MemoryStore (Issue #66).

Read paths use ``asyncio.to_thread()`` to delegate to the underlying sync
``MemoryStore`` without blocking the event loop.  ``MemoryStore`` already
serializes via ``threading.Lock``, so ``to_thread()`` simply keeps the loop
unblocked — it does NOT add parallelism to store operations.

Write paths (``save``/``delete``) are async-native: the actual Postgres I/O
goes through ``AsyncPostgresPrivateBackend`` (native
``psycopg_pool.AsyncConnectionPool``) instead of a thread-pool thread.  The
``MemoryStore`` in-memory cache and business logic still run in
``to_thread``; only the persistence layer is intercepted.  Captured
secondary writes (``save_relations``, ``append_audit``) are flushed via
the async backend alongside the primary save/delete (STORY-072.8 /
TAP-1565).

When no async backend is wired (e.g. an embedded sync-only test setup),
``save``/``delete`` transparently fall back to ``to_thread`` against the
sync persistence layer.

Usage::

    from tapps_brain.aio import AsyncMemoryStore

    async with await AsyncMemoryStore.open(project_root) as store:
        await store.save(key="greeting", value="hello")
        entry = await store.get("greeting")
"""

from __future__ import annotations

import asyncio
import inspect
import os
import threading
from typing import TYPE_CHECKING, Any, TypeVar

import structlog

from tapps_brain.postgres_connection import is_postgres_dsn
from tapps_brain.store import MemoryStore

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from tapps_brain.async_postgres_private import AsyncPostgresPrivateBackend
    from tapps_brain.models import MemoryEntry

_T = TypeVar("_T")

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


class _CapturePersistenceBackend:
    """Intercepts save/delete calls during a MemoryStore operation.

    Used in async-native mode to prevent MemoryStore from blocking a thread
    pool thread on the Postgres write.  Captured entries — primary saves
    and deletes as well as secondary writes (``save_relations``,
    ``append_audit``) — are flushed via ``AsyncPostgresPrivateBackend``
    after the ``to_thread`` call returns.

    All read operations delegate to the real backend so MemoryStore's read
    paths continue to work.
    """

    def __init__(self, real: Any) -> None:
        self._real = real
        self._saved: list[MemoryEntry] = []
        self._deleted: list[str] = []
        self._deleted_relations: list[str] = []
        self._relations: list[tuple[str, list[Any]]] = []
        self._audit: list[tuple[str, str, dict[str, Any] | None]] = []
        self._lock = threading.Lock()

    # --- Captured writes ---------------------------------------------------

    def save(self, entry: MemoryEntry) -> None:
        with self._lock:
            self._saved.append(entry)

    def save_many(self, entries: list[MemoryEntry]) -> None:
        # TAP-2800: capture a batched persist the same way as single saves so
        # MemoryStore.save_many works under async-native capture; the entries are
        # flushed via the async backend after the to_thread call returns.
        with self._lock:
            self._saved.extend(entries)

    def delete(self, key: str) -> bool:
        with self._lock:
            self._saved = [e for e in self._saved if e.key != key]
            self._deleted.append(key)
            self._deleted_relations.append(key)
            self._relations = [(k, r) for k, r in self._relations if k != key]
        return True

    def delete_relations(self, key: str) -> int:
        """Queue relation deletion so async flush mirrors sync QueryMixin.delete."""
        with self._lock:
            self._relations = [(k, r) for k, r in self._relations if k != key]
            if key not in self._deleted_relations:
                self._deleted_relations.append(key)
        return 0

    # --- Captured secondary writes ------------------------------------------

    def append_audit(
        self,
        action: str,
        key: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            self._audit.append((action, key, extra))

    def save_relations(self, key: str, relations: list[Any]) -> int:
        if not relations:
            return 0
        with self._lock:
            self._relations.append((key, list(relations)))
            # Drop any pending delete for this key — a later save wins.
            self._deleted_relations = [k for k in self._deleted_relations if k != key]
        return len(relations)

    # --- Read operations (delegate to real backend) -------------------------

    def load_all(self, **kwargs: Any) -> list[Any]:
        return list(self._real.load_all(**kwargs))

    def load_one(self, key: str) -> Any:
        """Delegate lazy hydration to the real backend, honouring captures.

        ``_ensure_entry_cached`` treats a missing ``load_one`` as "entry does
        not exist", so omitting this delegate silently broke reinforce /
        delete / record_access on durable-but-uncached entries while the
        capture was swapped in.
        """
        with self._lock:
            for entry in reversed(self._saved):
                if entry.key == key:
                    return entry
            if key in self._deleted:
                return None
        fn = getattr(self._real, "load_one", None)
        return fn(key) if callable(fn) else None

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
        with self._lock:
            for rel_key, rels in reversed(self._relations):
                if rel_key == key:
                    out: list[Any] = []
                    for rel in rels:
                        if isinstance(rel, dict):
                            out.append(rel)
                        else:
                            out.append(
                                {
                                    "subject": getattr(rel, "subject", ""),
                                    "predicate": getattr(rel, "predicate", ""),
                                    "object_entity": getattr(rel, "object_entity", ""),
                                    "source_entry_keys": list(
                                        getattr(rel, "source_entry_keys", []) or []
                                    ),
                                    "confidence": float(getattr(rel, "confidence", 0.8)),
                                }
                            )
                    return out
            if key in self._deleted_relations:
                return []
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

    def flush(
        self,
    ) -> tuple[
        list[MemoryEntry],
        list[str],
        list[str],
        list[tuple[str, list[Any]]],
        list[tuple[str, str, dict[str, Any] | None]],
    ]:
        """Return captured saves/deletes/relations/audit and clear the queues."""
        with self._lock:
            saves = list(self._saved)
            deletes = list(self._deleted)
            deleted_relations = list(self._deleted_relations)
            relations = list(self._relations)
            audit = list(self._audit)
            self._saved.clear()
            self._deleted.clear()
            self._deleted_relations.clear()
            self._relations.clear()
            self._audit.clear()
        return saves, deletes, deleted_relations, relations, audit


class AsyncMemoryStore:
    """Async facade over :class:`MemoryStore`.

    All concurrency is *thread-based* (``asyncio.to_thread``) — this wrapper
    does not add true async I/O parallelism to the underlying sync store.

    Write methods (``save`` / ``delete`` / ``reinforce`` and other mutating
    calls) are bounded by a write semaphore (default 16 concurrent writes,
    configurable via ``TAPPS_BRAIN_AIO_MAX_CONCURRENT_WRITES`` env var or the
    ``max_concurrent_writes`` constructor argument).  Read methods are bounded
    by a separate read semaphore (default 64, ``TAPPS_BRAIN_AIO_MAX_CONCURRENT_READS``
    / ``max_concurrent_reads``).  Both bounds make back-pressure explicit and
    observable via :attr:`write_queue_depth` / :attr:`read_queue_depth`.

    When an :class:`~tapps_brain.async_postgres_private.AsyncPostgresPrivateBackend`
    is wired, write-path Postgres I/O also goes through the async pool instead
    of a thread-pool thread.  When no async backend is wired (sync-only embedded
    setups), writes fall back to ``to_thread`` under the same semaphore.
    """

    __slots__ = (
        "_async_backend",
        "_lock",
        "_read_inflight",
        "_read_sem",
        "_store",
        "_wrapper_cache",
        "_write_inflight",
        "_write_sem",
    )

    def __init__(
        self,
        store: MemoryStore,
        *,
        async_backend: AsyncPostgresPrivateBackend | None = None,
        max_concurrent_writes: int | None = None,
        max_concurrent_reads: int | None = None,
    ) -> None:
        self._store = store
        self._wrapper_cache: dict[str, Any] = {}
        self._async_backend = async_backend
        # Serialises the persistence-swap in async-native save/delete so
        # concurrent coroutines never observe each other's capture backend.
        self._lock: asyncio.Lock = asyncio.Lock()
        _w = (
            max_concurrent_writes
            if max_concurrent_writes is not None
            else int(os.environ.get("TAPPS_BRAIN_AIO_MAX_CONCURRENT_WRITES", "16"))
        )
        _r = (
            max_concurrent_reads
            if max_concurrent_reads is not None
            else int(os.environ.get("TAPPS_BRAIN_AIO_MAX_CONCURRENT_READS", "64"))
        )
        # Guard against misconfigured zero/negative values so asyncio.Semaphore
        # never receives a value < 1.
        _w = max(_w, 1)
        _r = max(_r, 1)
        self._write_sem: asyncio.Semaphore = asyncio.Semaphore(_w)
        self._read_sem: asyncio.Semaphore = asyncio.Semaphore(_r)
        # Count of operations *currently holding* the respective semaphore.
        # Incremented after acquire, decremented in finally — safe because
        # asyncio coroutines are cooperative; there is no await between the
        # semaphore acquire and the increment, so the update is atomic from
        # the event-loop's perspective.
        # NOTE: for the async-backend code path (save/delete/reinforce),
        # _lock serialises the persistence-swap, so the effective concurrency
        # for those operations is 1 even though the write semaphore allows
        # up to max_concurrent_writes holders.  write_queue_depth reflects
        # semaphore holders (including those blocked on _lock), not active I/O.
        self._write_inflight: int = 0
        self._read_inflight: int = 0

    # ------------------------------------------------------------------
    # Concurrency gauges
    # ------------------------------------------------------------------

    @property
    def write_queue_depth(self) -> int:
        """Number of write operations currently holding the write semaphore."""
        return self._write_inflight

    @property
    def read_queue_depth(self) -> int:
        """Number of read operations currently holding the read semaphore."""
        return self._read_inflight

    # ------------------------------------------------------------------
    # Bounded thread-pool helpers
    # ------------------------------------------------------------------

    async def _write_thread(self, fn: Callable[..., _T], *args: Any, **kwargs: Any) -> _T:
        """Run *fn* in a worker thread, bounded by the write semaphore."""
        async with self._write_sem:
            self._write_inflight += 1
            try:
                return await asyncio.to_thread(fn, *args, **kwargs)
            finally:
                self._write_inflight -= 1

    async def _read_thread(self, fn: Callable[..., _T], *args: Any, **kwargs: Any) -> _T:
        """Run *fn* in a worker thread, bounded by the read semaphore."""
        async with self._read_sem:
            self._read_inflight += 1
            try:
                return await asyncio.to_thread(fn, *args, **kwargs)
            finally:
                self._read_inflight -= 1

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    async def open(cls, project_root: Path, **kwargs: Any) -> AsyncMemoryStore:
        """Create a ``MemoryStore`` in a worker thread and return the wrapper.

        When a PostgreSQL DSN is configured, also builds an
        :class:`AsyncPostgresPrivateBackend` so writes go through the native
        async pool instead of a thread-pool thread.
        """
        store = await asyncio.to_thread(MemoryStore, project_root, **kwargs)

        async_backend = None
        dsn = (
            os.environ.get("TAPPS_BRAIN_DATABASE_URL")
            or os.environ.get("TAPPS_BRAIN_HIVE_DSN")
            or ""
        ).strip()
        if is_postgres_dsn(dsn):
            from tapps_brain.backends import create_async_private_backend

            project_id = getattr(store, "_project_id", None) or ""
            agent_id = getattr(store, "_agent_id", None) or ""
            if (
                isinstance(project_id, str)
                and isinstance(agent_id, str)
                and project_id
                and agent_id
            ):
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
    # Primary methods (explicit signatures for IDE discoverability)
    # ------------------------------------------------------------------

    async def save(self, key: str, value: str, **kwargs: Any) -> Any:
        """Async version of :meth:`MemoryStore.save`.

        When an :class:`AsyncPostgresPrivateBackend` is wired, intercepts the
        sync persistence layer so the Postgres write is flushed via the async
        pool after the in-memory cache update returns.  Otherwise delegates
        to the sync store via :func:`asyncio.to_thread`.

        Concurrent saves are bounded by ``_write_sem`` (default 16).
        """
        if self._async_backend is None:
            return await self._write_thread(self._store.save, key, value, **kwargs)

        async with self._write_sem:
            self._write_inflight += 1
            try:
                with self._store._serialized():
                    prior = self._store._entries.get(key)
                capture = _CapturePersistenceBackend(self._store._persistence)
                async with self._lock:
                    old = self._store._persistence
                    self._store._persistence = capture
                    try:
                        result = await asyncio.to_thread(self._store.save, key, value, **kwargs)
                    finally:
                        self._store._persistence = old
                try:
                    await self._flush_capture(capture)
                except Exception:
                    with self._store._serialized():
                        if prior is None:
                            self._store._entries.pop(key, None)
                        else:
                            self._store._entries[key] = prior
                    raise
                return result
            finally:
                self._write_inflight -= 1

    async def get(self, key: str, **kwargs: Any) -> Any:
        """Async version of :meth:`MemoryStore.get`."""
        return await self._read_thread(self._store.get, key, **kwargs)

    async def delete(self, key: str) -> bool:
        """Async version of :meth:`MemoryStore.delete`.

        When an :class:`AsyncPostgresPrivateBackend` is wired, intercepts the
        sync persistence layer so the Postgres delete is flushed via the
        async pool.  Otherwise delegates to the sync store via
        :func:`asyncio.to_thread`.

        Bounded by ``_write_sem`` (default 16).
        """
        if self._async_backend is None:
            return await self._write_thread(self._store.delete, key)

        async with self._write_sem:
            self._write_inflight += 1
            try:
                with self._store._serialized():
                    prior = self._store._entries.get(key)
                    prior_rels = list(self._store._relations.get(key, []))
                capture = _CapturePersistenceBackend(self._store._persistence)
                async with self._lock:
                    old = self._store._persistence
                    self._store._persistence = capture
                    try:
                        result = await asyncio.to_thread(self._store.delete, key)
                    finally:
                        self._store._persistence = old
                try:
                    await self._flush_capture(capture)
                except Exception:
                    if prior is not None:
                        with self._store._serialized():
                            self._store._entries[key] = prior
                            if prior_rels:
                                self._store._relations[key] = prior_rels
                    raise
                return result
            finally:
                self._write_inflight -= 1

    async def _flush_capture(self, capture: _CapturePersistenceBackend) -> None:
        """Drain captured writes (saves, deletes, relations, audit) via the async backend.

        ``save_relations`` and ``append_audit`` are best-effort on the sync
        path — failures are logged but never raised, so we preserve that
        contract here too.
        """
        # unreachable: callers guard on _async_backend is not None before calling
        if self._async_backend is None:  # pragma: no cover
            raise RuntimeError("aio: _async_backend not initialised before write")
        saves, deletes, deleted_relations, relations, audit = capture.flush()
        # Prefer atomic batch when available so a mid-list failure cannot leave
        # a partial durable write against an already-updated in-memory cache.
        try:
            if saves:
                save_many = getattr(self._async_backend, "save_many", None)
                batch_done = False
                if callable(save_many):
                    maybe_batch = save_many(saves)
                    if inspect.isawaitable(maybe_batch):
                        await maybe_batch
                        batch_done = True
                if not batch_done:
                    for entry in saves:
                        await self._async_backend.save(entry)
            for k in deletes:
                await self._async_backend.delete(k)
            for rel_key in deleted_relations:
                del_rels = getattr(self._async_backend, "delete_relations", None)
                if callable(del_rels):
                    maybe = del_rels(rel_key)
                    if inspect.isawaitable(maybe):
                        await maybe
        except Exception:
            logger.warning(
                "aio.flush_capture_failed",
                save_count=len(saves),
                delete_count=len(deletes),
                exc_info=True,
            )
            raise
        # Relations/audit are best-effort (matching the sync path): by this
        # point the primary saves/deletes are already durable via the async
        # pool, so raising here would make the caller roll the entry back out
        # of the cache while the Postgres row stands — a cache/durable split
        # worse than a missing secondary write.
        for rel_key, rels in relations:
            save_rels = getattr(self._async_backend, "save_relations", None)
            if save_rels is not None:
                try:
                    await save_rels(rel_key, rels)
                except Exception:
                    logger.warning("aio.flush_relations_failed", key=rel_key, exc_info=True)
        for action, audit_key, extra in audit:
            append = getattr(self._async_backend, "append_audit", None)
            if append is not None:
                try:
                    await append(action, audit_key, extra)
                except Exception:
                    logger.warning("aio.flush_audit_failed", key=audit_key, exc_info=True)

    async def search(self, query: str, **kwargs: Any) -> list[Any]:
        """Async version of :meth:`MemoryStore.search`."""
        return await self._read_thread(self._store.search, query, **kwargs)

    async def list_all(self, **kwargs: Any) -> list[Any]:
        """Async version of :meth:`MemoryStore.list_all`."""
        return await self._read_thread(self._store.list_all, **kwargs)

    async def list_memory_groups(self) -> list[str]:
        """Async version of :meth:`MemoryStore.list_memory_groups`."""
        return await self._read_thread(self._store.list_memory_groups)

    async def recall(self, message: str, **kwargs: Any) -> Any:
        """Async version of :meth:`MemoryStore.recall`."""
        return await self._read_thread(self._store.recall, message, **kwargs)

    async def reinforce(self, key: str, **kwargs: Any) -> Any:
        """Async version of :meth:`MemoryStore.reinforce`.

        When an :class:`AsyncPostgresPrivateBackend` is wired, intercepts the
        sync persistence layer so the reinforced entry's Postgres write is
        flushed via the async pool after the in-memory cache update returns.
        Otherwise delegates to the sync store via :func:`asyncio.to_thread`
        (STORY-072.9, TAP-1566).

        Bounded by ``_write_sem`` (default 16).
        """
        if self._async_backend is None:
            return await self._write_thread(self._store.reinforce, key, **kwargs)

        async with self._write_sem:
            self._write_inflight += 1
            try:
                with self._store._serialized():
                    prior = self._store._entries.get(key)
                capture = _CapturePersistenceBackend(self._store._persistence)
                async with self._lock:
                    old = self._store._persistence
                    self._store._persistence = capture
                    try:
                        result = await asyncio.to_thread(self._store.reinforce, key, **kwargs)
                    finally:
                        self._store._persistence = old
                try:
                    await self._flush_capture(capture)
                except Exception:
                    with self._store._serialized():
                        if prior is None:
                            self._store._entries.pop(key, None)
                        else:
                            self._store._entries[key] = prior
                    raise
                return result
            finally:
                self._write_inflight -= 1

    async def ingest_context(self, context: str, **kwargs: Any) -> list[str]:
        """Async version of :meth:`MemoryStore.ingest_context`."""
        if self._async_backend is None:
            return await self._write_thread(self._store.ingest_context, context, **kwargs)
        async with self._write_sem:
            self._write_inflight += 1
            try:
                with self._store._serialized():
                    prior_keys = set(self._store._entries)
                capture = _CapturePersistenceBackend(self._store._persistence)
                async with self._lock:
                    old = self._store._persistence
                    self._store._persistence = capture
                    try:
                        result = await asyncio.to_thread(
                            self._store.ingest_context, context, **kwargs
                        )
                    finally:
                        self._store._persistence = old
                try:
                    await self._flush_capture(capture)
                except Exception:
                    with self._store._serialized():
                        for k in list(self._store._entries):
                            if k not in prior_keys:
                                self._store._entries.pop(k, None)
                    raise
                return result
            finally:
                self._write_inflight -= 1

    async def record_access(self, key: str, was_useful: bool) -> None:
        """Async version of :meth:`MemoryStore.record_access`."""
        if self._async_backend is None:
            await self._write_thread(self._store.record_access, key, was_useful)
            return
        async with self._write_sem:
            self._write_inflight += 1
            try:
                with self._store._serialized():
                    prior = self._store._entries.get(key)
                capture = _CapturePersistenceBackend(self._store._persistence)
                async with self._lock:
                    old = self._store._persistence
                    self._store._persistence = capture
                    try:
                        await asyncio.to_thread(self._store.record_access, key, was_useful)
                    finally:
                        self._store._persistence = old
                try:
                    await self._flush_capture(capture)
                except Exception:
                    if prior is not None:
                        with self._store._serialized():
                            self._store._entries[key] = prior
                    raise
            finally:
                self._write_inflight -= 1

    async def history(self, key: str) -> list[Any]:
        """Async version of :meth:`MemoryStore.history`."""
        return await self._read_thread(self._store.history, key)

    async def health(self) -> Any:
        """Async version of :meth:`MemoryStore.health`."""
        return await self._read_thread(self._store.health)

    async def audit(self, **kwargs: Any) -> list[Any]:
        """Async version of :meth:`MemoryStore.audit` (TAP-2134).

        When an :class:`AsyncPostgresPrivateBackend` is wired, queries the
        audit log via the native async pool (``query_audit``) and wraps the
        dict rows in :class:`~tapps_brain.audit.AuditEntry` to preserve the
        sync return type.  Otherwise delegates to the sync store via
        :func:`asyncio.to_thread`.

        Bounded by ``_read_sem`` (default 64).
        """
        if self._async_backend is None:
            return await self._read_thread(self._store.audit, **kwargs)

        from tapps_brain.audit import AuditEntry

        async with self._read_sem:
            self._read_inflight += 1
            try:
                rows = await self._async_backend.query_audit(**kwargs)
            finally:
                self._read_inflight -= 1
        return [
            AuditEntry(
                timestamp=str(r.get("timestamp", "")),
                event_type=str(r.get("event_type", "")),
                key=str(r.get("key", "")),
                details=dict(r.get("details") or {}),
            )
            for r in rows
        ]

    async def diagnostics(self, **kwargs: Any) -> Any:
        """Async version of :meth:`MemoryStore.diagnostics`."""
        return await self._read_thread(self._store.diagnostics, **kwargs)

    async def count(self) -> int:
        """Async version of :meth:`MemoryStore.count`."""
        return await self._read_thread(self._store.count)

    async def snapshot(self) -> Any:
        """Async version of :meth:`MemoryStore.snapshot`."""
        return await self._read_thread(self._store.snapshot)

    async def gc(self, *, dry_run: bool = False) -> Any:
        """Async version of :meth:`MemoryStore.gc` (STORY-070.10)."""
        if self._async_backend is None:
            return await self._write_thread(self._store.gc, dry_run=dry_run)
        async with self._write_sem:
            self._write_inflight += 1
            try:
                with self._store._serialized():
                    prior = dict(self._store._entries)
                capture = _CapturePersistenceBackend(self._store._persistence)
                async with self._lock:
                    old = self._store._persistence
                    self._store._persistence = capture
                    try:
                        result = await asyncio.to_thread(self._store.gc, dry_run=dry_run)
                    finally:
                        self._store._persistence = old
                try:
                    await self._flush_capture(capture)
                except Exception:
                    with self._store._serialized():
                        self._store._entries.clear()
                        self._store._entries.update(prior)
                    raise
                return result
            finally:
                self._write_inflight -= 1

    async def supersede(self, old_key: str, new_value: str, **kwargs: Any) -> Any:
        """Async version of :meth:`MemoryStore.supersede` (STORY-070.10)."""
        if self._async_backend is None:
            return await self._write_thread(self._store.supersede, old_key, new_value, **kwargs)
        async with self._write_sem:
            self._write_inflight += 1
            try:
                with self._store._serialized():
                    prior_old = self._store._entries.get(old_key)
                capture = _CapturePersistenceBackend(self._store._persistence)
                async with self._lock:
                    old = self._store._persistence
                    self._store._persistence = capture
                    try:
                        result = await asyncio.to_thread(
                            self._store.supersede, old_key, new_value, **kwargs
                        )
                    finally:
                        self._store._persistence = old
                try:
                    await self._flush_capture(capture)
                except Exception:
                    with self._store._serialized():
                        if prior_old is not None:
                            self._store._entries[old_key] = prior_old
                        # supersede() also inserted a brand-new entry
                        # (<old_key>.v2 or similar) into the cache; its durable
                        # write never happened, so drop it or get() would
                        # return a phantom entry that doesn't exist in Postgres.
                        new_key = getattr(result, "key", None)
                        if isinstance(new_key, str) and new_key != old_key:
                            self._store._entries.pop(new_key, None)
                    raise
                return result
            finally:
                self._write_inflight -= 1

    async def get_gc_config(self) -> Any:
        """Async version of :meth:`MemoryStore.get_gc_config` (STORY-070.10)."""
        return await self._read_thread(self._store.get_gc_config)

    async def set_gc_config(self, config: Any) -> None:
        """Async version of :meth:`MemoryStore.set_gc_config` (STORY-070.10)."""
        await self._write_thread(self._store.set_gc_config, config)

    async def get_consolidation_config(self) -> Any:
        """Async version of :meth:`MemoryStore.get_consolidation_config` (STORY-070.10)."""
        return await self._read_thread(self._store.get_consolidation_config)

    async def set_consolidation_config(self, config: Any) -> None:
        """Async version of :meth:`MemoryStore.set_consolidation_config` (STORY-070.10)."""
        await self._write_thread(self._store.set_consolidation_config, config)

    async def get_relations(self, key: str) -> Any:
        """Async version of :meth:`MemoryStore.get_relations` (STORY-070.10)."""
        return await self._read_thread(self._store.get_relations, key)

    async def get_relations_batch(self, keys: list[str]) -> Any:
        """Async version of :meth:`MemoryStore.get_relations_batch` (STORY-070.10)."""
        return await self._read_thread(self._store.get_relations_batch, keys)

    async def find_related(self, key: str, *, max_hops: int = 2) -> Any:
        """Async version of :meth:`MemoryStore.find_related` (STORY-070.10)."""
        return await self._read_thread(self._store.find_related, key, max_hops=max_hops)

    async def query_relations(self, **kwargs: Any) -> Any:
        """Async version of :meth:`MemoryStore.query_relations` (STORY-070.10)."""
        return await self._read_thread(self._store.query_relations, **kwargs)

    async def list_tags(self) -> Any:
        """Async version of :meth:`MemoryStore.list_tags` (STORY-070.10)."""
        return await self._read_thread(self._store.list_tags)

    async def update_tags(self, key: str, **kwargs: Any) -> Any:
        """Async version of :meth:`MemoryStore.update_tags` (STORY-070.10)."""
        if self._async_backend is None:
            return await self._write_thread(self._store.update_tags, key, **kwargs)
        async with self._write_sem:
            self._write_inflight += 1
            try:
                with self._store._serialized():
                    prior = self._store._entries.get(key)
                capture = _CapturePersistenceBackend(self._store._persistence)
                async with self._lock:
                    old = self._store._persistence
                    self._store._persistence = capture
                    try:
                        result = await asyncio.to_thread(self._store.update_tags, key, **kwargs)
                    finally:
                        self._store._persistence = old
                try:
                    await self._flush_capture(capture)
                except Exception:
                    if prior is not None:
                        with self._store._serialized():
                            self._store._entries[key] = prior
                    raise
                return result
            finally:
                self._write_inflight -= 1

    async def entries_by_tag(self, tag: str, **kwargs: Any) -> Any:
        """Async version of :meth:`MemoryStore.entries_by_tag` (STORY-070.10)."""
        return await self._read_thread(self._store.entries_by_tag, tag, **kwargs)

    async def index_session(self, session_id: str, chunks: list[str]) -> Any:
        """Async version of :meth:`MemoryStore.index_session` (STORY-070.10)."""
        return await self._write_thread(self._store.index_session, session_id, chunks)

    async def search_sessions(self, query: str, **kwargs: Any) -> Any:
        """Async version of :meth:`MemoryStore.search_sessions` (STORY-070.10)."""
        return await self._read_thread(self._store.search_sessions, query, **kwargs)

    async def list_gc_stale_details(self) -> Any:
        """Async version of :meth:`MemoryStore.list_gc_stale_details` (STORY-070.10)."""
        return await self._read_thread(self._store.list_gc_stale_details)

    async def generate_report(self, *, period_days: int = 7) -> Any:
        """Async version of :meth:`MemoryStore.generate_report` (STORY-070.10)."""
        return await self._read_thread(self._store.generate_report, period_days=period_days)

    async def latest_quality_report(self) -> Any:
        """Async version of :meth:`MemoryStore.latest_quality_report` (STORY-070.10)."""
        return await self._read_thread(self._store.latest_quality_report)

    async def gc_run(self, *, dry_run: bool = False) -> Any:
        """Async version of :meth:`MemoryStore.gc` (alias for STORY-070.10 parity).

        ``gc_run()`` is an explicit alias matching the method name used by
        AgentForge callers.  Internally delegates to ``gc(dry_run=dry_run)``
        so the async-native capture/flush/rollback path is used when an
        async backend is wired (previously it bypassed ``gc()`` and did
        blocking sync Postgres I/O in a thread).
        """
        return await self.gc(dry_run=dry_run)

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

    #: Sync MemoryStore methods that mutate state but have no explicit async
    #: wrapper above.  Auto-wrapped via the *write* semaphore so they are
    #: counted in write_queue_depth and bounded like other writes.  Note they
    #: still run against the real sync backend (blocking I/O in a thread) —
    #: only the explicit wrappers use the async-native capture/flush path.
    _AUTO_WRAP_WRITE_METHODS: frozenset[str] = frozenset(
        {
            "save_many",
            "update_fields",
            "record_access",
            "ingest_context",
            "backfill_embeddings",
            "undo_consolidation_merge",
            "save_relations",
            "cleanup_sessions",
            "refresh_group_membership",
            "validate_entries",
        }
    )

    def __getattr__(self, name: str) -> Any:
        """Auto-wrap any remaining sync MemoryStore public method as async.

        Properties and private attributes are not wrapped — only callable
        public methods produce an async wrapper.  Known mutating methods
        (see ``_AUTO_WRAP_WRITE_METHODS``) are bounded by the write
        semaphore; everything else uses the read semaphore.

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

        runner = self._write_thread if name in self._AUTO_WRAP_WRITE_METHODS else self._read_thread

        async def _async_proxy(*args: Any, **kwargs: Any) -> Any:
            return await runner(attr, *args, **kwargs)

        _async_proxy.__name__ = name
        _async_proxy.__qualname__ = f"AsyncMemoryStore.{name}"
        _async_proxy.__doc__ = f"Async version of :meth:`MemoryStore.{name}`."
        cache[name] = _async_proxy
        return _async_proxy
