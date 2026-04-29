"""Integration tests for TAP-826: http_adapter async-native write path.

Verifies that write-path handlers use AsyncMemoryStore directly (no thread
pool blocking) when TAPPS_BRAIN_ASYNC_NATIVE=1.

These tests do NOT require a real Postgres — they inject a mock async store
so the async dispatch logic can be verified in-process.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("httpx")
pytest.importorskip("fastapi")

import httpx

import tapps_brain.http_adapter as _http_mod
from tapps_brain.http_adapter import (
    _service_version,
    _Settings,
    create_app,
)
from tapps_brain.models import MemoryEntry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_AUTH_TOKEN = "test-bearer-token"
_HEADERS = {
    "X-Project-Id": "proj",
    "X-Agent-Id": "agent",
    "Authorization": f"Bearer {_AUTH_TOKEN}",
}


def _make_entry(key: str = "k") -> MemoryEntry:
    return MemoryEntry(
        key=key,
        value="v",
        tier="pattern",
        confidence=0.8,
        source="agent",
        source_agent="agent",
        created_at=datetime.now(tz=UTC).isoformat(),
        updated_at=datetime.now(tz=UTC).isoformat(),
        last_accessed=datetime.now(tz=UTC).isoformat(),
    )


def _make_async_store() -> MagicMock:
    """Return a mock AsyncMemoryStore with async save/get/delete."""
    entry = _make_entry()
    store = MagicMock()
    store.profile = None
    store.save = AsyncMock(return_value=entry)
    store.get = AsyncMock(return_value=entry)
    store.delete = AsyncMock(return_value=True)
    store.close = AsyncMock()
    return store


def _make_sync_store() -> MagicMock:
    """Return a mock MemoryStore used as cfg.store."""
    store = MagicMock()
    store.profile = None
    store._project_id = "proj"
    store._agent_id = "agent"
    return store


def _make_settings(
    *,
    auth_token: str | None = _AUTH_TOKEN,
    async_store: Any = None,
    sync_store: Any = None,
) -> _Settings:
    s = _Settings.__new__(_Settings)
    s.dsn = None
    s.auth_token = auth_token
    s.admin_token = None
    s.metrics_token = None
    s.allowed_origins = []
    s.version = _service_version()
    s.store = sync_store
    s.snapshot_lock = threading.Lock()
    s.snapshot_cache = None
    s.snapshot_cache_at = 0.0
    s.idempotency_store = None
    s.async_store = async_store
    return s


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAsyncNativeRemember:
    @pytest.mark.asyncio
    async def test_remember_uses_async_store(self) -> None:
        """When async_store is set, /v1/remember calls async_store.save."""
        async_store = _make_async_store()
        sync_store = _make_sync_store()
        settings = _make_settings(async_store=async_store, sync_store=sync_store)

        _mcp_dummy = MagicMock()
        _mcp_dummy.session_manager = None

        with (
            patch.object(_http_mod, "_settings", settings),
            patch.object(_http_mod, "get_settings", return_value=settings),
        ):
            app = create_app(mcp_server=_mcp_dummy)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/v1/remember",
                    json={"key": "foo", "value": "bar"},
                    headers=_HEADERS,
                )

        assert resp.status_code == 200, resp.text
        async_store.save.assert_awaited_once()
        sync_store.save.assert_not_called()

    @pytest.mark.asyncio
    async def test_concurrent_remember_no_thread_pool_saturation(self) -> None:
        """50 concurrent /v1/remember requests complete when async_store is wired."""
        async_store = _make_async_store()
        sync_store = _make_sync_store()
        settings = _make_settings(async_store=async_store, sync_store=sync_store)

        _mcp_dummy = MagicMock()
        _mcp_dummy.session_manager = None

        with (
            patch.object(_http_mod, "_settings", settings),
            patch.object(_http_mod, "get_settings", return_value=settings),
        ):
            app = create_app(mcp_server=_mcp_dummy)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                tasks = [
                    client.post(
                        "/v1/remember",
                        json={"key": f"key-{i}", "value": f"val-{i}"},
                        headers=_HEADERS,
                    )
                    for i in range(50)
                ]
                responses = await asyncio.gather(*tasks)

        assert all(r.status_code == 200 for r in responses), [
            r.text for r in responses if r.status_code != 200
        ]
        assert async_store.save.await_count == 50

    @pytest.mark.asyncio
    async def test_remember_falls_back_to_to_thread_without_async_store(self) -> None:
        """Without async_store, /v1/remember uses asyncio.to_thread (sync path)."""
        entry = _make_entry("foo")
        sync_store = _make_sync_store()
        sync_store.save = MagicMock(return_value=entry)
        settings = _make_settings(async_store=None, sync_store=sync_store)

        _mcp_dummy = MagicMock()
        _mcp_dummy.session_manager = None

        with (
            patch.object(_http_mod, "_settings", settings),
            patch.object(_http_mod, "get_settings", return_value=settings),
        ):
            app = create_app(mcp_server=_mcp_dummy)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/v1/remember",
                    json={"key": "foo", "value": "bar"},
                    headers=_HEADERS,
                )

        assert resp.status_code == 200, resp.text
        sync_store.save.assert_called_once()


class TestAsyncNativeBrainForget:
    @pytest.mark.asyncio
    async def test_brain_forget_uses_async_store(self) -> None:
        """When async_store is set, /v1/forget calls async store get+delete."""
        async_store = _make_async_store()
        sync_store = _make_sync_store()
        settings = _make_settings(async_store=async_store, sync_store=sync_store)

        _mcp_dummy = MagicMock()
        _mcp_dummy.session_manager = None

        with (
            patch.object(_http_mod, "_settings", settings),
            patch.object(_http_mod, "get_settings", return_value=settings),
        ):
            app = create_app(mcp_server=_mcp_dummy)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/v1/forget",
                    json={"key": "k"},
                    headers=_HEADERS,
                )

        assert resp.status_code == 200, resp.text
        async_store.get.assert_awaited_once_with("k")
        async_store.delete.assert_awaited_once_with("k")


class TestAsyncNativeLearnSuccess:
    @pytest.mark.asyncio
    async def test_learn_success_uses_async_store(self) -> None:
        """When async_store is set, /v1/learn_success calls async_store.save."""
        async_store = _make_async_store()
        sync_store = _make_sync_store()
        settings = _make_settings(async_store=async_store, sync_store=sync_store)

        _mcp_dummy = MagicMock()
        _mcp_dummy.session_manager = None

        with (
            patch.object(_http_mod, "_settings", settings),
            patch.object(_http_mod, "get_settings", return_value=settings),
        ):
            app = create_app(mcp_server=_mcp_dummy)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/v1/learn_success",
                    json={"task_description": "Did a thing"},
                    headers=_HEADERS,
                )

        assert resp.status_code == 200, resp.text
        async_store.save.assert_awaited_once()


class TestAsyncNativeLearnFailure:
    @pytest.mark.asyncio
    async def test_learn_failure_uses_async_store(self) -> None:
        """When async_store is set, /v1/learn_failure calls async_store.save."""
        async_store = _make_async_store()
        sync_store = _make_sync_store()
        settings = _make_settings(async_store=async_store, sync_store=sync_store)

        _mcp_dummy = MagicMock()
        _mcp_dummy.session_manager = None

        with (
            patch.object(_http_mod, "_settings", settings),
            patch.object(_http_mod, "get_settings", return_value=settings),
        ):
            app = create_app(mcp_server=_mcp_dummy)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/v1/learn_failure",
                    json={"description": "Something went wrong", "error": "TimeoutError"},
                    headers=_HEADERS,
                )

        assert resp.status_code == 200, resp.text
        async_store.save.assert_awaited_once()
