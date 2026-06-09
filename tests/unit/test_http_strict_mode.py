"""EPIC-078 — fail-closed auth and CORS when TAPPS_BRAIN_STRICT=1."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

import tapps_brain.http_adapter as _http_mod
from tapps_brain.http.middleware import McpTenantMiddleware, OriginAllowlistMiddleware
from tapps_brain.http_adapter import _Settings, _service_version, create_app


def _make_settings(
    *,
    auth_token: str | None = None,
    allowed_origins: list[str] | None = None,
) -> _Settings:
    s = _Settings.__new__(_Settings)
    s.dsn = None
    s.auth_token = auth_token
    s.admin_token = None
    s.allowed_origins = allowed_origins or []
    s.version = _service_version()
    s.store = None
    s.snapshot_lock = threading.Lock()
    s.snapshot_cache = None
    s.snapshot_cache_at = 0.0
    return s


class TestCreateAppStrictMode:
    def test_strict_without_auth_token_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_STRICT", "1")
        settings = _make_settings(auth_token=None, allowed_origins=["https://app.example.com"])
        with (
            patch.object(_http_mod, "_settings", settings),
            patch.object(_http_mod, "get_settings", return_value=settings),
            pytest.raises(RuntimeError, match="TAPPS_BRAIN_AUTH_TOKEN"),
        ):
            create_app(mcp_server=MagicMock())

    def test_strict_without_allowed_origins_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_STRICT", "1")
        settings = _make_settings(auth_token="secret", allowed_origins=[])
        with (
            patch.object(_http_mod, "_settings", settings),
            patch.object(_http_mod, "get_settings", return_value=settings),
            pytest.raises(RuntimeError, match="TAPPS_BRAIN_ALLOWED_ORIGINS"),
        ):
            create_app(mcp_server=MagicMock())

    def test_strict_with_auth_and_origins_starts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_STRICT", "1")
        settings = _make_settings(
            auth_token="secret",
            allowed_origins=["https://app.example.com"],
        )
        mcp_dummy = MagicMock()
        mcp_dummy.session_manager = None
        with (
            patch.object(_http_mod, "_settings", settings),
            patch.object(_http_mod, "get_settings", return_value=settings),
        ):
            app = create_app(mcp_server=mcp_dummy)
        assert app is not None


class TestStrictModeRuntimeAuth:
    def test_mcp_returns_503_when_strict_and_token_cleared_at_runtime(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_STRICT", "1")
        boot_settings = _make_settings(
            auth_token="secret",
            allowed_origins=["https://app.example.com"],
        )
        runtime_settings = _make_settings(
            auth_token=None,
            allowed_origins=["https://app.example.com"],
        )
        mcp_dummy = MagicMock()
        mcp_dummy.session_manager = None
        with (
            patch.object(_http_mod, "_settings", boot_settings),
            patch.object(_http_mod, "get_settings", return_value=boot_settings),
        ):
            app = create_app(mcp_server=mcp_dummy)
        with (
            patch.object(_http_mod, "get_settings", return_value=runtime_settings),
            TestClient(app, raise_server_exceptions=False) as client,
        ):
            resp = client.post(
                "/mcp",
                headers={"X-Project-Id": "proj"},
                json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
            )
        assert resp.status_code == 503

    def test_rest_returns_503_when_strict_and_token_cleared_at_runtime(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_STRICT", "1")
        boot_settings = _make_settings(
            auth_token="secret",
            allowed_origins=["https://app.example.com"],
        )
        runtime_settings = _make_settings(
            auth_token=None,
            allowed_origins=["https://app.example.com"],
        )
        mcp_dummy = MagicMock()
        mcp_dummy.session_manager = None
        with (
            patch.object(_http_mod, "_settings", boot_settings),
            patch.object(_http_mod, "get_settings", return_value=boot_settings),
        ):
            app = create_app(mcp_server=mcp_dummy)
        with (
            patch.object(_http_mod, "get_settings", return_value=runtime_settings),
            TestClient(app, raise_server_exceptions=False) as client,
        ):
            resp = client.get("/info")
        assert resp.status_code == 503


class TestMiddlewareWiring:
    def test_create_app_uses_canonical_middleware_classes(self) -> None:
        settings = _make_settings(auth_token="tok")
        mcp_dummy = MagicMock()
        mcp_dummy.session_manager = None
        with (
            patch.object(_http_mod, "_settings", settings),
            patch.object(_http_mod, "get_settings", return_value=settings),
        ):
            app = create_app(mcp_server=mcp_dummy)
        middleware_classes = [m.cls for m in app.user_middleware]
        assert McpTenantMiddleware in middleware_classes
        assert OriginAllowlistMiddleware in middleware_classes
        assert McpTenantMiddleware.__module__ == "tapps_brain.http.middleware"
        assert OriginAllowlistMiddleware.__module__ == "tapps_brain.http.middleware"
