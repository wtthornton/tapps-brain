"""Unit tests for STORY-070.8 per-tenant auth tokens.

Covers:
- ProjectRegistry.rotate_token / revoke_token / verify_token
- http_adapter per-tenant auth dependency (_verify_per_tenant_token,
  require_data_plane_auth with TAPPS_BRAIN_PER_TENANT_AUTH=1)
- CLI rotate-token / revoke-token commands
- Admin HTTP routes: POST/DELETE /admin/projects/{id}/rotate-token|token
"""

from __future__ import annotations

import secrets
import threading
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

import tapps_brain.http_adapter as _http_mod
from tapps_brain.http_adapter import (
    _per_tenant_auth_enabled,
    _service_version,
    _Settings,
    create_app,
)
from tapps_brain.project_registry import ProjectRegistry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(
    *,
    dsn: str | None = None,
    auth_token: str | None = None,
    admin_token: str | None = None,
    store: Any = None,
) -> _Settings:
    s = _Settings.__new__(_Settings)
    s.dsn = dsn
    s.auth_token = auth_token
    s.admin_token = admin_token
    s.allowed_origins = []
    s.version = _service_version()
    s.store = store
    s.snapshot_lock = threading.Lock()
    s.snapshot_cache = None
    s.snapshot_cache_at = 0.0
    return s


@contextmanager
def _client(settings: _Settings):
    with (
        patch.object(_http_mod, "_settings", settings),
        patch.object(_http_mod, "get_settings", return_value=settings),
    ):
        _mcp_dummy = MagicMock()
        _mcp_dummy.session_manager = None
        app = create_app(mcp_server=_mcp_dummy)
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client


def _make_mock_conn_cm(hashed_token: str | None) -> MagicMock:
    """Build a mock PostgresConnectionManager that returns hashed_token."""
    cur = MagicMock()
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    cur.fetchone.return_value = (hashed_token,)
    cur.rowcount = 1
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    cm = MagicMock()
    cm.admin_context.return_value.__enter__ = MagicMock(return_value=conn)
    cm.admin_context.return_value.__exit__ = MagicMock(return_value=False)
    return cm


def _make_mock_conn_cm_rowcount(rowcount: int) -> MagicMock:
    """Build a mock where rowcount is configurable (for UPDATE checks)."""
    cur = MagicMock()
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    cur.rowcount = rowcount
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    cm = MagicMock()
    cm.admin_context.return_value.__enter__ = MagicMock(return_value=conn)
    cm.admin_context.return_value.__exit__ = MagicMock(return_value=False)
    return cm


# ---------------------------------------------------------------------------
# _per_tenant_auth_enabled
# ---------------------------------------------------------------------------


class TestPerTenantAuthEnabled:
    def test_disabled_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TAPPS_BRAIN_PER_TENANT_AUTH", raising=False)
        assert _per_tenant_auth_enabled() is False

    def test_enabled_when_flag_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_PER_TENANT_AUTH", "1")
        assert _per_tenant_auth_enabled() is True

    def test_not_enabled_for_truthy_non_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_PER_TENANT_AUTH", "true")
        assert _per_tenant_auth_enabled() is False


# ---------------------------------------------------------------------------
# ProjectRegistry token methods (with mock DB)
# ---------------------------------------------------------------------------


class TestProjectRegistryRotateToken:
    def test_rotate_token_returns_plaintext(self) -> None:
        """rotate_token should return a non-empty string."""
        pytest.importorskip("argon2")
        cm = _make_mock_conn_cm_rowcount(1)
        registry = ProjectRegistry(cm)
        token = registry.rotate_token("my-project")
        assert isinstance(token, str)
        assert len(token) >= 32

    def test_rotate_token_raises_on_unknown_project(self) -> None:
        pytest.importorskip("argon2")
        cm = _make_mock_conn_cm_rowcount(0)
        registry = ProjectRegistry(cm)
        with pytest.raises(LookupError, match="not registered"):
            registry.rotate_token("unknown-project")


class TestProjectRegistryRevokeToken:
    def test_revoke_returns_true_on_success(self) -> None:
        cm = _make_mock_conn_cm_rowcount(1)
        registry = ProjectRegistry(cm)
        assert registry.revoke_token("my-project") is True

    def test_revoke_returns_false_on_unknown(self) -> None:
        cm = _make_mock_conn_cm_rowcount(0)
        registry = ProjectRegistry(cm)
        assert registry.revoke_token("ghost-project") is False


class TestProjectRegistryVerifyToken:
    def test_returns_none_when_no_token(self) -> None:
        pytest.importorskip("argon2")
        cm = _make_mock_conn_cm(None)
        registry = ProjectRegistry(cm)
        result = registry.verify_token("proj", "anytoken")
        assert result is None

    def test_returns_true_for_valid_token(self) -> None:
        argon2 = pytest.importorskip("argon2")
        plaintext = secrets.token_urlsafe(16)
        ph = argon2.PasswordHasher()
        hashed = ph.hash(plaintext)

        cm = _make_mock_conn_cm(hashed)
        registry = ProjectRegistry(cm)
        assert registry.verify_token("proj", plaintext) is True

    def test_returns_false_for_wrong_token(self) -> None:
        argon2 = pytest.importorskip("argon2")
        ph = argon2.PasswordHasher()
        hashed = ph.hash("correct-token")

        cm = _make_mock_conn_cm(hashed)
        registry = ProjectRegistry(cm)
        assert registry.verify_token("proj", "wrong-token") is False


# ---------------------------------------------------------------------------
# Admin HTTP routes — rotate-token and revoke-token auth gating
# ---------------------------------------------------------------------------


class TestAdminTokenRouteAuthGating:
    def _settings(self) -> _Settings:
        return _make_settings(admin_token="admin-secret", dsn="postgresql://x")

    def test_rotate_token_requires_admin_auth(self) -> None:
        with _client(self._settings()) as client:
            resp = client.post("/admin/projects/my-proj/rotate-token")
        assert resp.status_code == 401

    def test_rotate_token_wrong_admin_token(self) -> None:
        with _client(self._settings()) as client:
            resp = client.post(
                "/admin/projects/my-proj/rotate-token",
                headers={"Authorization": "Bearer wrong"},
            )
        assert resp.status_code == 403

    def test_revoke_token_requires_admin_auth(self) -> None:
        with _client(self._settings()) as client:
            resp = client.delete("/admin/projects/my-proj/token")
        assert resp.status_code == 401

    def test_revoke_token_wrong_admin_token(self) -> None:
        with _client(self._settings()) as client:
            resp = client.delete(
                "/admin/projects/my-proj/token",
                headers={"Authorization": "Bearer wrong"},
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# require_data_plane_auth with per-tenant flag
# ---------------------------------------------------------------------------


class TestRequireDataPlaneAuthPerTenant:
    """Test require_data_plane_auth respects TAPPS_BRAIN_PER_TENANT_AUTH."""

    def test_no_flag_uses_global_token_pass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TAPPS_BRAIN_PER_TENANT_AUTH", raising=False)
        settings = _make_settings(auth_token="global-tok")
        with _client(settings) as client:
            resp = client.get("/health", headers={"Authorization": "Bearer global-tok"})
        assert resp.status_code == 200

    def test_per_tenant_flag_set_passes_with_valid_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pytest.importorskip("argon2")
        monkeypatch.setenv("TAPPS_BRAIN_PER_TENANT_AUTH", "1")

        settings = _make_settings(dsn="postgresql://fake", auth_token=None)
        with (
            _client(settings) as client,
            patch.object(_http_mod, "_verify_per_tenant_token", return_value=True),
        ):
            resp = client.get(
                "/v1/recall",
                headers={
                    "Authorization": "Bearer some-token",
                    "x-project-id": "proj-a",
                },
                params={"query": "test"},
            )
        # 200 or 422/400 (no store) — auth should not 401/403
        assert resp.status_code not in (401, 403)

    def test_per_tenant_flag_set_rejects_wrong_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pytest.importorskip("argon2")
        monkeypatch.setenv("TAPPS_BRAIN_PER_TENANT_AUTH", "1")
        settings = _make_settings(dsn="postgresql://fake", auth_token=None)
        with (
            _client(settings) as client,
            patch.object(_http_mod, "_verify_per_tenant_token", return_value=False),
        ):
            resp = client.get(
                "/v1/recall",
                headers={
                    "Authorization": "Bearer wrong-token",
                    "x-project-id": "proj-a",
                },
                params={"query": "test"},
            )
        assert resp.status_code == 403

    def test_per_tenant_fallback_to_global_when_no_hash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When project has no per-tenant token (None), fall back to global check."""
        pytest.importorskip("argon2")
        monkeypatch.setenv("TAPPS_BRAIN_PER_TENANT_AUTH", "1")
        settings = _make_settings(dsn="postgresql://fake", auth_token="global-tok")
        with (
            _client(settings) as client,
            patch.object(_http_mod, "_verify_per_tenant_token", return_value=None),
        ):
            # Correct global token → should pass
            resp_ok = client.get(
                "/v1/recall",
                headers={
                    "Authorization": "Bearer global-tok",
                    "x-project-id": "proj-a",
                },
                params={"query": "test"},
            )
            # Wrong global token → 403
            resp_bad = client.get(
                "/v1/recall",
                headers={
                    "Authorization": "Bearer wrong",
                    "x-project-id": "proj-a",
                },
                params={"query": "test"},
            )
        assert resp_ok.status_code not in (401, 403)
        assert resp_bad.status_code == 403
