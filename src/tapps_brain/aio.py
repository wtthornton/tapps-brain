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
import inspect
from pathlib import Path
from typing import Any

from tapps_brain.store import ConsolidationConfig, MemoryStore


class AsyncMemoryStore:
    """Async facade over :class:`MemoryStore`.

    Every public method delegates to the underlying sync store via
    :func:`asyncio.to_thread`.
    """

    __slots__ = ("_store",)

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

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
    def profile(self) -> Any:  # noqa: ANN401
        return self._store.profile

    # ------------------------------------------------------------------
    # Primary methods (explicit signatures for IDE discoverability)
    # ------------------------------------------------------------------

    async def save(self, key: str, value: str, **kwargs: Any) -> Any:  # noqa: ANN401
        """Async version of :meth:`MemoryStore.save`."""
        return await asyncio.to_thread(self._store.save, key, value, **kwargs)

    async def get(self, key: str, **kwargs: Any) -> Any:  # noqa: ANN401
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

    async def recall(self, message: str, **kwargs: Any) -> Any:  # noqa: ANN401
        """Async version of :meth:`MemoryStore.recall`."""
        return await asyncio.to_thread(self._store.recall, message, **kwargs)

    async def reinforce(self, key: str, confidence_boost: float, **kwargs: Any) -> Any:  # noqa: ANN401
        """Async version of :meth:`MemoryStore.reinforce`."""
        return await asyncio.to_thread(self._store.reinforce, key, confidence_boost, **kwargs)

    async def ingest_context(self, context: str, **kwargs: Any) -> list[str]:
        """Async version of :meth:`MemoryStore.ingest_context`."""
        return await asyncio.to_thread(self._store.ingest_context, context, **kwargs)

    async def record_access(self, key: str, was_useful: bool) -> None:
        """Async version of :meth:`MemoryStore.record_access`."""
        await asyncio.to_thread(self._store.record_access, key, was_useful)

    async def history(self, key: str) -> list[Any]:
        """Async version of :meth:`MemoryStore.history`."""
        return await asyncio.to_thread(self._store.history, key)

    async def health(self) -> Any:  # noqa: ANN401
        """Async version of :meth:`MemoryStore.health`."""
        return await asyncio.to_thread(self._store.health)

    async def audit(self, key: str, **kwargs: Any) -> list[dict[str, Any]]:
        """Async version of :meth:`MemoryStore.audit`."""
        return await asyncio.to_thread(self._store.audit, key, **kwargs)

    async def diagnostics(self, **kwargs: Any) -> Any:  # noqa: ANN401
        """Async version of :meth:`MemoryStore.diagnostics`."""
        return await asyncio.to_thread(self._store.diagnostics, **kwargs)

    async def count(self) -> int:
        """Async version of :meth:`MemoryStore.count`."""
        return await asyncio.to_thread(self._store.count)

    async def snapshot(self) -> Any:  # noqa: ANN401
        """Async version of :meth:`MemoryStore.snapshot`."""
        return await asyncio.to_thread(self._store.snapshot)

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

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        """Auto-wrap any remaining sync MemoryStore public method as async.

        Properties and private attributes are not wrapped — only callable
        public methods produce an async wrapper.
        """
        if name.startswith("_"):
            raise AttributeError(name)

        attr = getattr(self._store, name)
        if not callable(attr):
            return attr

        async def _async_proxy(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            return await asyncio.to_thread(attr, *args, **kwargs)

        _async_proxy.__name__ = name
        _async_proxy.__qualname__ = f"AsyncMemoryStore.{name}"
        _async_proxy.__doc__ = f"Async version of :meth:`MemoryStore.{name}`."
        return _async_proxy
