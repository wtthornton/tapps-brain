"""Integration tests for the http_adapter async-native write path.

Verifies that write-path handlers use AsyncMemoryStore directly (no thread
pool blocking) when ``cfg.async_store`` is wired, and that they fall back
to ``asyncio.to_thread`` against the sync store when no async backend is
available. Async-native became the default in EPIC-072 STORY-072.7
(TAP-1117) — the original wire-up was TAP-826.

These tests do NOT require a real Postgres — they inject a mock async store
so the async dispatch logic can be verified in-process.
"""

from __future__ import annotations

import asyncio
import threading
import time
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
    """Return a mock AsyncMemoryStore with async save/get/delete/reinforce."""
    entry = _make_entry()
    store = MagicMock()
    store.profile = None
    store.save = AsyncMock(return_value=entry)
    store.get = AsyncMock(return_value=entry)
    store.delete = AsyncMock(return_value=True)
    store.reinforce = AsyncMock(return_value=entry)
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


# ---------------------------------------------------------------------------
# STORY-072.9 (TAP-1566): /v1/reinforce + /v1/reinforce:batch async dispatch
# ---------------------------------------------------------------------------


class TestAsyncNativeReinforce:
    @pytest.mark.asyncio
    async def test_reinforce_uses_async_store(self) -> None:
        """When async_store is set, /v1/reinforce calls async_store.reinforce."""
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
                    "/v1/reinforce",
                    json={"key": "k", "confidence_boost": 0.05},
                    headers=_HEADERS,
                )

        assert resp.status_code == 200, resp.text
        async_store.reinforce.assert_awaited_once_with("k", confidence_boost=0.05)
        sync_store.reinforce.assert_not_called()

    @pytest.mark.asyncio
    async def test_reinforce_falls_back_to_to_thread_without_async_store(self) -> None:
        """Without async_store, /v1/reinforce uses asyncio.to_thread on the sync store."""
        entry = _make_entry("k")
        sync_store = _make_sync_store()
        sync_store.reinforce = MagicMock(return_value=entry)
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
                    "/v1/reinforce",
                    json={"key": "k"},
                    headers=_HEADERS,
                )

        assert resp.status_code == 200, resp.text
        sync_store.reinforce.assert_called_once()

    @pytest.mark.asyncio
    async def test_reinforce_batch_uses_async_store(self) -> None:
        """When async_store is set, /v1/reinforce:batch routes per-item through async_store.reinforce."""
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
                    "/v1/reinforce:batch",
                    json={
                        "entries": [
                            {"key": "k1"},
                            {"key": "k2", "confidence_boost": 0.1},
                            {"key": "k3"},
                        ]
                    },
                    headers=_HEADERS,
                )

        assert resp.status_code == 200, resp.text
        assert async_store.reinforce.await_count == 3
        sync_store.reinforce.assert_not_called()
        body = resp.json()
        assert body["reinforced_count"] == 3
        assert body["error_count"] == 0


# ---------------------------------------------------------------------------
# TAP-1833: tools/list warm-path cache latency
# ---------------------------------------------------------------------------


class TestToolsListCache:
    """Verify the in-process tools/list cache (TAP-1833).

    Uses the tool_filter layer directly — no HTTP round-trip needed to verify
    the cache hit path.  The sub-50 ms threshold is generous relative to the
    actual cost of a cache hit (< 0.1 ms) so CI timing variance cannot flap.
    """

    def setup_method(self) -> None:
        """Clear cache state before each test."""
        from tapps_brain.mcp_server.tool_filter import (
            clear_tools_list_cache,
            reset_profile_filter_counters,
        )

        clear_tools_list_cache()
        reset_profile_filter_counters()

    def test_tools_list_warm_under_50ms(self) -> None:
        """Second tools/list call returns cached result well under 50 ms.

        Acceptance criterion for TAP-1833: the warm-path cost collapses to
        a list copy after the first call populates the in-process cache.
        """
        mcp = pytest.importorskip("mcp.server.fastmcp", reason="mcp extra not installed")
        FastMCP = mcp.FastMCP

        from tapps_brain.mcp_server.profile_registry import ProfileRegistry
        from tapps_brain.mcp_server.tool_filter import (
            _TOOLS_LIST_CACHE,
            clear_tools_list_cache,
            install_tool_filter,
        )

        server = FastMCP("tap-1833-test")

        @server.tool(description="ping")  # type: ignore[misc]
        def ping(x: int) -> str:
            return str(x)

        @server.tool(description="echo")  # type: ignore[misc]
        def echo(msg: str) -> str:
            return msg

        registry = ProfileRegistry()
        clear_tools_list_cache()
        install_tool_filter(server, profile_registry=registry)

        # Cold call: populates cache.
        cold_result = server._tool_manager.list_tools()
        assert len(cold_result) == 2

        # Cache must be populated after the first call.
        assert "full" in _TOOLS_LIST_CACHE, "cache not populated after first call"

        # Warm call: must be served from cache in < 50 ms.
        start = time.monotonic()
        warm_result = server._tool_manager.list_tools()
        elapsed_ms = (time.monotonic() - start) * 1000

        assert len(warm_result) == 2
        assert elapsed_ms < 50.0, (
            f"Warm tools/list took {elapsed_ms:.2f} ms — expected < 50 ms (TAP-1833)"
        )

    def test_tools_list_cache_expires_after_ttl(self) -> None:
        """Cache entry is rebuilt after the TTL expires."""
        mcp = pytest.importorskip("mcp.server.fastmcp", reason="mcp extra not installed")
        FastMCP = mcp.FastMCP

        from tapps_brain.mcp_server import tool_filter as _tf
        from tapps_brain.mcp_server.profile_registry import ProfileRegistry

        server = FastMCP("tap-1833-expiry-test")

        @server.tool(description="a")  # type: ignore[misc]
        def tool_a() -> str:
            return "a"

        registry = ProfileRegistry()
        _tf.clear_tools_list_cache()
        _tf.install_tool_filter(server, profile_registry=registry)

        # Populate cache.
        server._tool_manager.list_tools()
        assert "full" in _tf._TOOLS_LIST_CACHE

        # Backdate the cache entry so it appears expired.
        _old_expires, old_tools = _tf._TOOLS_LIST_CACHE["full"]
        _tf._TOOLS_LIST_CACHE["full"] = (time.monotonic() - 1.0, old_tools)

        # Next call should rebuild (expired TTL → cache miss path).
        result = server._tool_manager.list_tools()
        assert len(result) == 1

        # Cache is refreshed with a new expiry in the future.
        new_expires, _ = _tf._TOOLS_LIST_CACHE["full"]
        assert new_expires > time.monotonic()

    def test_tools_list_cache_returns_copy(self) -> None:
        """Cache returns a list copy so callers cannot corrupt the cached state."""
        mcp = pytest.importorskip("mcp.server.fastmcp", reason="mcp extra not installed")
        FastMCP = mcp.FastMCP

        from tapps_brain.mcp_server import tool_filter as _tf
        from tapps_brain.mcp_server.profile_registry import ProfileRegistry

        server = FastMCP("tap-1833-copy-test")

        @server.tool(description="b")  # type: ignore[misc]
        def tool_b() -> str:
            return "b"

        registry = ProfileRegistry()
        _tf.clear_tools_list_cache()
        _tf.install_tool_filter(server, profile_registry=registry)

        # Populate cache.
        first = server._tool_manager.list_tools()

        # Mutate the returned list — should NOT affect cached state.
        first.clear()

        # Subsequent call should still return the original tool list.
        second = server._tool_manager.list_tools()
        assert len(second) == 1, "Cache was corrupted by mutation of the returned list"


# ---------------------------------------------------------------------------
# TAP-1843: /v1/tools/list static snapshot route
# ---------------------------------------------------------------------------


class TestV1ToolsList:
    def test_v1_tools_list_static_snapshot(self) -> None:
        """GET /v1/tools/list returns the static tool snapshot built at startup.

        TAP-1843: the route must be unauthenticated, return Cache-Control
        public/max-age=300, and expose a ``{tools:[...]}`` payload with the
        same per-tool fields (name, description, inputSchema) as MCP
        ``tools/list`` minus the JSON-RPC envelope.

        Uses Starlette TestClient so the ASGI lifespan runs (httpx
        ASGITransport skips the lifespan scope; TestClient does not).
        """
        # Use SimpleNamespace for tool stubs — MagicMock.name is a reserved
        # mock attribute and cannot be overridden via attribute assignment.
        import types

        from starlette.testclient import TestClient

        def _make_tool(name: str, description: str) -> Any:
            return types.SimpleNamespace(
                name=name,
                description=description,
                parameters={"type": "object", "properties": {}},
            )

        mock_tool_manager = MagicMock()
        mock_tool_manager.list_tools.return_value = [
            _make_tool("brain_remember", "Store a memory entry"),
            _make_tool("brain_recall", "Retrieve memory entries"),
        ]

        mcp_server = MagicMock()
        mcp_server.session_manager = None
        mcp_server._tool_manager = mock_tool_manager

        settings = _make_settings()

        with (
            patch.object(_http_mod, "_settings", settings),
            patch.object(_http_mod, "get_settings", return_value=settings),
        ):
            app = create_app(mcp_server=mcp_server)
            with TestClient(app, raise_server_exceptions=True) as client:
                resp = client.get("/v1/tools/list")

        assert resp.status_code == 200
        assert "public" in resp.headers.get("cache-control", "")
        assert "max-age=300" in resp.headers.get("cache-control", "")
        data = resp.json()
        assert "tools" in data
        assert isinstance(data["tools"], list)
        assert len(data["tools"]) == 2
        names = {t["name"] for t in data["tools"]}
        assert "brain_remember" in names
        assert "brain_recall" in names
        for tool_entry in data["tools"]:
            assert "name" in tool_entry
            assert "description" in tool_entry
            assert "inputSchema" in tool_entry

    def test_v1_tools_list_no_auth_required(self) -> None:
        """GET /v1/tools/list succeeds without an Authorization header."""
        from starlette.testclient import TestClient

        mcp_server = MagicMock()
        mcp_server.session_manager = None
        settings = _make_settings()

        with (
            patch.object(_http_mod, "_settings", settings),
            patch.object(_http_mod, "get_settings", return_value=settings),
        ):
            app = create_app(mcp_server=mcp_server)
            with TestClient(app) as client:
                resp = client.get("/v1/tools/list")

        assert resp.status_code == 200
