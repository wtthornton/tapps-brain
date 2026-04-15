"""Integration test — STORY-070.8: per-tenant token isolation.

Verifies that token-A cannot authenticate requests scoped to project-B.

Requires a live Postgres instance pointed to by ``TAPPS_TEST_POSTGRES_DSN``.
Skipped when that env var is unset (matches the pattern used by
``tests/integration/test_tenant_isolation.py``).

Feature flag ``TAPPS_BRAIN_PER_TENANT_AUTH=1`` is set for the duration of
each test via monkeypatch.
"""

from __future__ import annotations

import contextlib
import os

import pytest

_PG_DSN = os.environ.get("TAPPS_TEST_POSTGRES_DSN", "")
_SKIP = not _PG_DSN

pytestmark = pytest.mark.skipif(_SKIP, reason="TAPPS_TEST_POSTGRES_DSN not set")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _apply_migrations(dsn: str) -> None:
    from tapps_brain.postgres_migrations import apply_private_migrations

    apply_private_migrations(dsn)


def _make_registry(dsn: str):  # type: ignore[return]
    from tapps_brain.postgres_connection import PostgresConnectionManager
    from tapps_brain.project_registry import ProjectRegistry

    cm = PostgresConnectionManager(dsn)
    return ProjectRegistry(cm), cm


def _register_project(registry: object, project_id: str) -> None:
    from tapps_brain.profile import get_builtin_profile

    profile = get_builtin_profile("repo-brain")
    registry.register(project_id, profile, source="admin", approved=True)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPerTenantTokenIsolation:
    """Token A must not authenticate requests to project B."""

    def test_token_a_cannot_auth_project_b(self) -> None:
        """rotate_token on project_a returns token-A; verifying against project_b
        returns False (project_b has its own token, not matching token-A).
        """
        pytest.importorskip("argon2")
        _apply_migrations(_PG_DSN)

        registry_a, cm_a = _make_registry(_PG_DSN)
        registry_b, cm_b = _make_registry(_PG_DSN)
        try:
            _register_project(registry_a, "test-tenant-a")
            _register_project(registry_b, "test-tenant-b")

            token_a = registry_a.rotate_token("test-tenant-a")
            registry_b.rotate_token("test-tenant-b")

            # token_a should verify for project a
            assert registry_a.verify_token("test-tenant-a", token_a) is True

            # token_a must NOT verify for project b
            result = registry_b.verify_token("test-tenant-b", token_a)
            assert result is False, (
                "token_a verified against project_b — cross-tenant auth leak!"
            )
        finally:
            with contextlib.suppress(Exception):
                registry_a.revoke_token("test-tenant-a")
                registry_a.delete("test-tenant-a")
            with contextlib.suppress(Exception):
                registry_b.revoke_token("test-tenant-b")
                registry_b.delete("test-tenant-b")
            cm_a.close()
            cm_b.close()

    def test_token_a_cannot_auth_project_b_via_http(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """HTTP data-plane auth with per-tenant flag: token-A → project-B → 403."""
        pytest.importorskip("argon2")
        monkeypatch.setenv("TAPPS_BRAIN_PER_TENANT_AUTH", "1")
        _apply_migrations(_PG_DSN)

        registry, cm = _make_registry(_PG_DSN)
        try:
            _register_project(registry, "http-tenant-a")
            _register_project(registry, "http-tenant-b")
            token_a = registry.rotate_token("http-tenant-a")

            registry_b, cm_b = _make_registry(_PG_DSN)
            try:
                registry_b.rotate_token("http-tenant-b")
            finally:
                cm_b.close()

            # Directly call _verify_per_tenant_token as used by the HTTP adapter.
            from tapps_brain.http_adapter import _verify_per_tenant_token

            # token_a → project-a must pass
            assert _verify_per_tenant_token("http-tenant-a", token_a, _PG_DSN) is True

            # token_a → project-b must fail (False, not None — project_b HAS a token)
            result = _verify_per_tenant_token("http-tenant-b", token_a, _PG_DSN)
            assert result is False, (
                "token_a was accepted for http-tenant-b — cross-tenant isolation FAILED"
            )
        finally:
            with contextlib.suppress(Exception):
                registry.revoke_token("http-tenant-a")
                registry.delete("http-tenant-a")
                registry.revoke_token("http-tenant-b")
                registry.delete("http-tenant-b")
            cm.close()

    def test_project_with_no_token_returns_none(self) -> None:
        """verify_token returns None when no token is set — caller falls back."""
        pytest.importorskip("argon2")
        _apply_migrations(_PG_DSN)

        registry, cm = _make_registry(_PG_DSN)
        try:
            _register_project(registry, "no-token-project")
            # No rotate_token call → hashed_token IS NULL
            result = registry.verify_token("no-token-project", "any-token")
            assert result is None
        finally:
            with contextlib.suppress(Exception):
                registry.delete("no-token-project")
            cm.close()

    def test_revoke_token_clears_auth(self) -> None:
        """After revocation, verify_token returns None (not False)."""
        pytest.importorskip("argon2")
        _apply_migrations(_PG_DSN)

        registry, cm = _make_registry(_PG_DSN)
        try:
            _register_project(registry, "revoke-test-proj")
            token = registry.rotate_token("revoke-test-proj")
            assert registry.verify_token("revoke-test-proj", token) is True

            revoked = registry.revoke_token("revoke-test-proj")
            assert revoked is True

            # After revoke: no hash → None, not False
            result = registry.verify_token("revoke-test-proj", token)
            assert result is None, (
                f"Expected None after revocation, got {result!r}"
            )
        finally:
            with contextlib.suppress(Exception):
                registry.delete("revoke-test-proj")
            cm.close()
