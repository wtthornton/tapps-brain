"""Security regression tests for TAP-780 through TAP-784.

TAP-780 — admin rate limiting
TAP-781 — per-tenant auth fails closed on registry error
TAP-782 — bare except in verify_token
TAP-783 — assert replaced with explicit guard; audit trail for ALLOW_PRIVILEGED_ROLE
TAP-784 — TAPPS_BRAIN_INTEGRITY_KEY env var support
"""

from __future__ import annotations

import base64
import secrets
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# TAP-780: admin rate limiting
# ---------------------------------------------------------------------------


class TestAdminRateLimit:
    """_check_admin_rate_limit enforces sliding-window rate limits."""

    def _fresh_limiter(self):
        """Return a clean module-level state by clearing the bucket dict."""
        from tapps_brain.http import auth as _auth

        with _auth._admin_rate_lock:
            _auth._admin_rate_buckets.clear()
        return _auth

    def test_allows_requests_within_limit(self) -> None:
        auth = self._fresh_limiter()
        for _ in range(auth._ADMIN_RATE_LIMIT):
            assert auth._check_admin_rate_limit("10.0.0.1") is True

    def test_blocks_when_limit_exceeded(self) -> None:
        auth = self._fresh_limiter()
        for _ in range(auth._ADMIN_RATE_LIMIT):
            auth._check_admin_rate_limit("10.0.0.2")
        assert auth._check_admin_rate_limit("10.0.0.2") is False

    def test_different_ips_are_independent(self) -> None:
        auth = self._fresh_limiter()
        for _ in range(auth._ADMIN_RATE_LIMIT):
            auth._check_admin_rate_limit("10.0.0.3")
        # Exhausted for .3, but .4 should still be allowed.
        assert auth._check_admin_rate_limit("10.0.0.4") is True

    def test_old_entries_expire(self) -> None:
        auth = self._fresh_limiter()
        ip = "10.0.0.5"
        # Fill bucket with timestamps in the past (beyond the window).
        past = time.monotonic() - auth._ADMIN_RATE_WINDOW - 1
        with auth._admin_rate_lock:
            import collections

            auth._admin_rate_buckets[ip] = collections.deque(
                [past] * auth._ADMIN_RATE_LIMIT
            )
        # All entries are stale — next request should be allowed.
        assert auth._check_admin_rate_limit(ip) is True

    def test_require_admin_auth_returns_429_when_rate_limited(self) -> None:
        from fastapi import HTTPException

        from tapps_brain.http import auth as _auth

        with _auth._admin_rate_lock:
            _auth._admin_rate_buckets.clear()

        # Fill bucket for this IP.
        ip = "192.168.1.1"
        for _ in range(_auth._ADMIN_RATE_LIMIT):
            _auth._check_admin_rate_limit(ip)

        mock_request = MagicMock()
        mock_request.client.host = ip

        fake_settings = MagicMock()
        fake_settings.admin_token = "secret"

        import tapps_brain.http_adapter as _http_mod

        with patch.object(_http_mod, "get_settings", return_value=fake_settings):
            with pytest.raises(HTTPException) as exc_info:
                _auth.require_admin_auth(mock_request)

        assert exc_info.value.status_code == 429


# ---------------------------------------------------------------------------
# TAP-781: per-tenant auth fails closed on backend error
# ---------------------------------------------------------------------------


class TestPerTenantAuthFailClosed:
    """require_data_plane_auth raises 503 (not falls through) on registry errors."""

    def _make_request(self, *, project_id: str = "proj-x", token: str = "tok") -> MagicMock:
        req = MagicMock()
        req.headers.get = lambda k, d=None: (
            {"authorization": f"Bearer {token}", "x-project-id": project_id}.get(k, d)
        )
        req.client.host = "127.0.0.1"
        return req

    def test_503_on_db_error_not_fallthrough(self) -> None:
        from fastapi import HTTPException

        import tapps_brain.http_adapter as _http_mod
        from tapps_brain.http import auth as _auth

        fake_cfg = MagicMock()
        fake_cfg.dsn = "postgresql://localhost/test"
        fake_cfg.auth_token = "global-token"

        with (
            patch.object(_http_mod, "get_settings", return_value=fake_cfg),
            patch.object(_auth, "_per_tenant_auth_enabled", return_value=True),
            patch.object(
                _auth,
                "_verify_per_tenant_token",
                side_effect=RuntimeError("DB is down"),
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                _auth.require_data_plane_auth(self._make_request())

        # Must be 503, not 403 (wrong token) and not fall through to global check
        assert exc_info.value.status_code == 503
        assert exc_info.value.detail["error"] == "service_unavailable"

    def test_global_token_not_accepted_after_registry_error(self) -> None:
        """Even with valid global token, DB error → 503, not success."""
        from fastapi import HTTPException

        import tapps_brain.http_adapter as _http_mod
        from tapps_brain.http import auth as _auth

        fake_cfg = MagicMock()
        fake_cfg.dsn = "postgresql://localhost/test"
        fake_cfg.auth_token = "tok"  # matches the token in the request

        with (
            patch.object(_http_mod, "get_settings", return_value=fake_cfg),
            patch.object(_auth, "_per_tenant_auth_enabled", return_value=True),
            patch.object(
                _auth,
                "_verify_per_tenant_token",
                side_effect=ConnectionError("pool exhausted"),
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                _auth.require_data_plane_auth(self._make_request(token="tok"))

        assert exc_info.value.status_code == 503


# ---------------------------------------------------------------------------
# TAP-782: verify_token bare except
# ---------------------------------------------------------------------------


class TestVerifyTokenExceptions:
    """ProjectRegistry.verify_token handles argon2 exceptions correctly."""

    def _make_registry(self):
        from tapps_brain.project_registry import ProjectRegistry

        mock_cm = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cm.admin_context.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_cm.admin_context.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        return ProjectRegistry(mock_cm), mock_cursor

    def test_verify_invalid_error_returns_false(self) -> None:
        pytest.importorskip("argon2")
        from argon2 import PasswordHasher
        from argon2.exceptions import VerifyInvalidError

        registry, cursor = self._make_registry()
        cursor.fetchone.return_value = ("not-a-real-hash",)

        with patch.object(PasswordHasher, "verify", side_effect=VerifyInvalidError):
            result = registry.verify_token("proj", "tok")

        assert result is False

    def test_unexpected_exception_reraises(self) -> None:
        pytest.importorskip("argon2")
        from argon2 import PasswordHasher

        registry, cursor = self._make_registry()
        cursor.fetchone.return_value = ("$argon2id$v=19$m=65536,t=3,p=4$fake",)

        with patch.object(PasswordHasher, "verify", side_effect=MemoryError("oom")):
            with pytest.raises(MemoryError):
                registry.verify_token("proj", "tok")

    def test_verify_mismatch_still_returns_false(self) -> None:
        pytest.importorskip("argon2")
        from argon2 import PasswordHasher
        from argon2.exceptions import VerifyMismatchError

        registry, cursor = self._make_registry()
        cursor.fetchone.return_value = ("$argon2id$v=19$m=65536,t=3,p=4$fake",)

        with patch.object(PasswordHasher, "verify", side_effect=VerifyMismatchError):
            result = registry.verify_token("proj", "tok")

        assert result is False


# ---------------------------------------------------------------------------
# TAP-783: assert replaced with proper guard
# ---------------------------------------------------------------------------


class TestNonPrivilegedRoleGuard:
    """_assert_non_privileged_role uses RuntimeError, not assert."""

    def test_no_assert_in_bytecode(self) -> None:
        """Verify the guard is not an assert (stripped by -O)."""
        import dis
        import io

        from tapps_brain.postgres_connection import PostgresConnectionManager

        output = io.StringIO()
        dis.dis(PostgresConnectionManager._assert_non_privileged_role, file=output)
        bytecode = output.getvalue()
        # dis output for a bare assert includes RAISE_VARARGS after loading
        # AssertionError.  A proper `if ... raise RuntimeError` shows
        # RuntimeError in the LOAD_GLOBAL, not AssertionError.
        assert "AssertionError" not in bytecode

    def test_allow_privileged_role_logs_at_error(self) -> None:
        import tapps_brain.postgres_connection as _pgmod
        from tapps_brain.postgres_connection import PostgresConnectionManager

        cm = PostgresConnectionManager.__new__(PostgresConnectionManager)
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_pool.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_pool.connection.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchone.return_value = ("tapps_super", True, False)
        mock_cursor.fetchall.return_value = []
        cm._pool = mock_pool

        mock_logger = MagicMock()
        with (
            patch.dict("os.environ", {"TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE": "1"}),
            patch.object(_pgmod, "logger", mock_logger),
        ):
            cm._assert_non_privileged_role()

        # structlog .error() is called with the audit event name as positional arg.
        mock_logger.error.assert_called_once()
        call_args = mock_logger.error.call_args
        assert call_args.args[0] == "postgres.privileged_role_audit_override"


# ---------------------------------------------------------------------------
# TAP-784: TAPPS_BRAIN_INTEGRITY_KEY env var support
# ---------------------------------------------------------------------------


class TestIntegrityKeyEnvVar:
    """TAPPS_BRAIN_INTEGRITY_KEY allows key injection without touching disk."""

    @pytest.fixture(autouse=True)
    def _reset(self) -> None:
        from tapps_brain.integrity import reset_key_cache

        reset_key_cache()
        yield
        reset_key_cache()

    def test_base64_key_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tapps_brain.integrity import _KEY_LENGTH, get_signing_key

        key = secrets.token_bytes(_KEY_LENGTH)
        encoded = base64.b64encode(key).decode()
        monkeypatch.setenv("TAPPS_BRAIN_INTEGRITY_KEY", encoded)

        result = get_signing_key()
        assert result == key

    def test_urlsafe_base64_key_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tapps_brain.integrity import _KEY_LENGTH, get_signing_key

        key = secrets.token_bytes(_KEY_LENGTH)
        encoded = base64.urlsafe_b64encode(key).decode()
        monkeypatch.setenv("TAPPS_BRAIN_INTEGRITY_KEY", encoded)

        result = get_signing_key()
        assert result == key

    def test_hex_key_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tapps_brain.integrity import _KEY_LENGTH, get_signing_key

        key = secrets.token_bytes(_KEY_LENGTH)
        encoded = key.hex()
        monkeypatch.setenv("TAPPS_BRAIN_INTEGRITY_KEY", encoded)

        result = get_signing_key()
        assert result == key

    def test_invalid_env_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tapps_brain.integrity import IntegrityKeyEnvError, get_signing_key

        monkeypatch.setenv("TAPPS_BRAIN_INTEGRITY_KEY", "not-valid-!!!")
        with pytest.raises(IntegrityKeyEnvError):
            get_signing_key()

    def test_short_env_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tapps_brain.integrity import IntegrityKeyEnvError, get_signing_key

        short = base64.b64encode(b"tooshort").decode()
        monkeypatch.setenv("TAPPS_BRAIN_INTEGRITY_KEY", short)
        with pytest.raises(IntegrityKeyEnvError):
            get_signing_key()

    def test_env_key_skips_disk(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        """When env var is set, no file I/O occurs — key_path arg is ignored."""
        from tapps_brain.integrity import _KEY_LENGTH, get_signing_key

        key = secrets.token_bytes(_KEY_LENGTH)
        encoded = base64.b64encode(key).decode()
        monkeypatch.setenv("TAPPS_BRAIN_INTEGRITY_KEY", encoded)

        nonexistent_path = tmp_path / "should_not_exist.key"
        result = get_signing_key(key_path=nonexistent_path)

        assert result == key
        assert not nonexistent_path.exists()
