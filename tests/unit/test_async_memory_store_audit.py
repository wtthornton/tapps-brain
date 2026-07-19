"""Unit tests for the async-native audit() read path (TAP-2134).

Verifies that ``AsyncMemoryStore.audit`` calls
``AsyncPostgresPrivateBackend.query_audit`` directly when the async backend
is wired, returning ``AuditEntry`` instances and *not* dispatching the call
through ``asyncio.to_thread``. Also verifies that the legacy to_thread path
is preserved when no async backend is configured.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tapps_brain.aio import AsyncMemoryStore
from tapps_brain.audit import AuditEntry


def _make_sync_store() -> MagicMock:
    store = MagicMock()
    store.profile = None
    store._project_id = "proj"
    store._agent_id = "agent"
    return store


class TestAsyncAuditWithBackend:
    """`AsyncMemoryStore.audit` should call query_audit directly when wired."""

    @pytest.mark.asyncio
    async def test_audit_calls_async_backend_query_audit(self) -> None:
        sync_store = _make_sync_store()
        async_backend = MagicMock()
        async_backend.query_audit = AsyncMock(
            return_value=[
                {
                    "timestamp": "2026-05-18T22:00:00+00:00",
                    "event_type": "save",
                    "key": "k1",
                    "details": {"x": 1},
                },
            ]
        )
        ams = AsyncMemoryStore(sync_store, async_backend=async_backend)

        result = await ams.audit(key="k1", limit=10)

        async_backend.query_audit.assert_awaited_once_with(newest_first=True, key="k1", limit=10)
        sync_store.audit.assert_not_called()
        assert len(result) == 1
        assert isinstance(result[0], AuditEntry)
        assert result[0].key == "k1"
        assert result[0].event_type == "save"
        assert result[0].details == {"x": 1}

    @pytest.mark.asyncio
    async def test_audit_does_not_invoke_asyncio_to_thread(self) -> None:
        sync_store = _make_sync_store()
        async_backend = MagicMock()
        async_backend.query_audit = AsyncMock(return_value=[])
        ams = AsyncMemoryStore(sync_store, async_backend=async_backend)

        with patch("tapps_brain.aio.asyncio.to_thread") as to_thread:
            result = await ams.audit()

        to_thread.assert_not_called()
        assert result == []

    @pytest.mark.asyncio
    async def test_audit_handles_missing_details(self) -> None:
        sync_store = _make_sync_store()
        async_backend = MagicMock()
        async_backend.query_audit = AsyncMock(
            return_value=[
                {"timestamp": "t", "event_type": "delete", "key": "k", "details": None},
            ]
        )
        ams = AsyncMemoryStore(sync_store, async_backend=async_backend)

        result = await ams.audit()

        assert result[0].details == {}


class TestAsyncAuditFallback:
    """When no async backend is wired, fall back to the sync store path."""

    @pytest.mark.asyncio
    async def test_audit_uses_read_thread_when_no_async_backend(self) -> None:
        sync_store = _make_sync_store()
        sync_store.audit = MagicMock(
            return_value=[
                AuditEntry(timestamp="t", event_type="save", key="k", details={}),
            ]
        )
        ams = AsyncMemoryStore(sync_store, async_backend=None)

        result = await ams.audit(key="k")

        sync_store.audit.assert_called_once_with(key="k")
        assert len(result) == 1
        assert isinstance(result[0], AuditEntry)
