"""Unit tests for TAP-544 constant-time bearer-token comparisons.

The three HTTP adapter bearer-token checks — ``require_data_plane_auth``,
``require_admin_auth``, and ``McpTenantMiddleware`` — must use
``hmac.compare_digest`` rather than ``!=`` so that attackers cannot recover
tokens byte-by-byte via statistical timing analysis.

These tests are deterministic: rather than measuring wall-clock timing
(which is flaky in CI), we patch ``hmac.compare_digest`` inside
:mod:`tapps_brain.http_adapter` and assert that each code path invokes it
with the user-supplied token and the configured secret. This is a stronger
check than a statistical timing test — it verifies the *exact* constant-time
primitive is being used, not just that timing happens to look uniform.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

import tapps_brain.http_adapter as _http_mod
from tapps_brain.http_adapter import (
    _service_version,
    _Settings,
    create_app,
)


def _make_settings(
    *,
    auth_token: str | None = None,
    admin_token: str | None = None,
) -> _Settings:
    s = _Settings.__new__(_Settings)
    s.dsn = None
    s.auth_token = auth_token
    s.admin_token = admin_token
    s.allowed_origins = []
    s.version = _service_version()
    s.store = None
    s.snapshot_lock = threading.Lock()
    s.snapshot_cache = None
    s.snapshot_cache_at = 0.0
    return s


@contextmanager
def _client(settings: _Settings) -> Any:
    with (
        patch.object(_http_mod, "_settings", settings),
        patch.object(_http_mod, "get_settings", return_value=settings),
    ):
        _mcp_dummy = MagicMock()
        _mcp_dummy.session_manager = None
        app = create_app(mcp_server=_mcp_dummy)
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client


class TestDataPlaneAuthConstantTime:
    """``require_data_plane_auth`` must call ``hmac.compare_digest``."""

    def test_compare_digest_invoked_with_both_tokens_encoded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TAPPS_BRAIN_PER_TENANT_AUTH", raising=False)
        settings = _make_settings(auth_token="correct-token")
        compare_spy = MagicMock(return_value=False)
        with (
            patch.object(_http_mod.hmac, "compare_digest", compare_spy),
            _client(settings) as client,
        ):
            resp = client.get(
                "/info",
                headers={"Authorization": "Bearer wrong-token"},
            )
        assert resp.status_code == 403
        assert compare_spy.called, "require_data_plane_auth must use hmac.compare_digest"
        args = compare_spy.call_args.args
        assert args == (b"wrong-token", b"correct-token")


class TestAdminAuthConstantTime:
    """``require_admin_auth`` must call ``hmac.compare_digest``."""

    def test_compare_digest_invoked_on_wrong_admin_token(self) -> None:
        settings = _make_settings(admin_token="admin-secret")
        compare_spy = MagicMock(return_value=False)
        with (
            patch.object(_http_mod.hmac, "compare_digest", compare_spy),
            _client(settings) as client,
        ):
            resp = client.post(
                "/admin/projects/my-proj/rotate-token",
                headers={"Authorization": "Bearer wrong"},
            )
        assert resp.status_code == 403
        assert compare_spy.called, "require_admin_auth must use hmac.compare_digest"
        args = compare_spy.call_args.args
        assert args == (b"wrong", b"admin-secret")

    def test_compare_digest_not_called_when_token_header_missing(self) -> None:
        """Short-circuit to 401 before the constant-time comparison runs."""
        settings = _make_settings(admin_token="admin-secret")
        compare_spy = MagicMock(return_value=False)
        with (
            patch.object(_http_mod.hmac, "compare_digest", compare_spy),
            _client(settings) as client,
        ):
            resp = client.post("/admin/projects/my-proj/rotate-token")
        assert resp.status_code == 401
        compare_spy.assert_not_called()


class TestMcpMiddlewareConstantTime:
    """``McpTenantMiddleware`` must call ``hmac.compare_digest``."""

    def test_compare_digest_invoked_on_wrong_mcp_token(self) -> None:
        settings = _make_settings(auth_token="mcp-secret")
        compare_spy = MagicMock(return_value=False)
        with (
            patch.object(_http_mod.hmac, "compare_digest", compare_spy),
            _client(settings) as client,
        ):
            resp = client.post(
                "/mcp/",
                headers={
                    "Authorization": "Bearer wrong",
                    "x-project-id": "proj-a",
                },
                json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            )
        assert resp.status_code == 403
        assert compare_spy.called, "McpTenantMiddleware must use hmac.compare_digest"
        args = compare_spy.call_args.args
        assert args == (b"wrong", b"mcp-secret")


class TestTokenMismatchStillRejected:
    """Functional: wrong tokens of equal length to the secret still 403."""

    def test_data_plane_wrong_equal_length_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TAPPS_BRAIN_PER_TENANT_AUTH", raising=False)
        settings = _make_settings(auth_token="abcdef")
        with _client(settings) as client:
            resp = client.get(
                "/info",
                headers={"Authorization": "Bearer zzzzzz"},
            )
        assert resp.status_code == 403

    def test_admin_wrong_equal_length_token(self) -> None:
        settings = _make_settings(admin_token="abcdef")
        with _client(settings) as client:
            resp = client.post(
                "/admin/projects/p/rotate-token",
                headers={"Authorization": "Bearer zzzzzz"},
            )
        assert resp.status_code == 403
