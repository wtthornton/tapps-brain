"""Async wrapper for MemoryStore (Issue #66).

Thin adapter using ``asyncio.to_thread()`` around every public
``MemoryStore`` method.  Thread-safe: ``MemoryStore`` already serializes
via ``threading.Lock``, so ``to_thread()`` simply keeps the event loop
unblocked — it does NOT add parallelism to store operations.

Usage::

    from tapps_brain.aio import AsyncMemoryStore

    async with await AsyncMemoryStore.open(project_root) as store:
        await store.save(key="greeting", value="hello")
        entry = await store.get("greeting")
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from tapps_brain.store import MemoryStore

if TYPE_CHECKING:
    from pathlib import Path


class AsyncMemoryStore:
    """Async facade over :class:`MemoryStore`.

    Every public method delegates to the underlying sync store via
    :func:`asyncio.to_thread`.
    """

    __slots__ = ("_store", "_wrapper_cache")

    def __init__(self, store: MemoryStore) -> None:
        self._store = store
        self._wrapper_cache: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    async def open(cls, project_root: Path, **kwargs: Any) -> AsyncMemoryStore:
        """Create a ``MemoryStore`` in a worker thread and return the wrapper."""
        store = await asyncio.to_thread(MemoryStore, project_root, **kwargs)
        return cls(store)

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
        """Async version of :meth:`MemoryStore.save`."""
        return await asyncio.to_thread(self._store.save, key, value, **kwargs)

    async def get(self, key: str, **kwargs: Any) -> Any:
        """Async version of :meth:`MemoryStore.get`."""
        return await asyncio.to_thread(self._store.get, key, **kwargs)

    async def delete(self, key: str) -> bool:
        """Async version of :meth:`MemoryStore.delete`."""
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
