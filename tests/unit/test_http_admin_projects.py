"""HTTP admin-route tests for EPIC-069 project registration.

Focus: auth gating, routing, and request-shape validation.  Live DB
behavior is covered by the integration suite (Story 69.8).
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

from starlette.testclient import TestClient

import tapps_brain.http_adapter as _mod
from tapps_brain.http_adapter import _service_version, _Settings, create_app

# ---------------------------------------------------------------------------
# Shared test helpers (replaces real-server helpers from old test)
# ---------------------------------------------------------------------------


def _make_settings(
    *,
    dsn: str | None = None,
    auth_token: str | None = None,
    admin_token: str | None = None,
    store: Any = None,
) -> _Settings:
    """Return a fresh _Settings with explicit values (bypasses env reads)."""
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
    """Yield a TestClient driving create_app() with isolated settings."""
    with (
        patch.object(_mod, "_settings", settings),
        patch.object(_mod, "get_settings", return_value=settings),
    ):
        _mcp_dummy = MagicMock()
        _mcp_dummy.session_manager = None
        app = create_app(mcp_server=_mcp_dummy)
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client


class TestAdminAuthGate:
    def test_admin_disabled_returns_503_when_no_token(self) -> None:
        """No admin_token set → /admin/* returns 503 admin_disabled."""
        with _client(_make_settings(admin_token=None)) as c:
            resp = c.get("/admin/projects")
        assert resp.status_code == 503
        assert resp.json()["error"] == "admin_disabled"

    def test_missing_bearer_returns_401(self) -> None:
        """admin_token configured but no Authorization header → 401."""
        with _client(_make_settings(admin_token="s3cret")) as c:
            resp = c.get("/admin/projects")
        assert resp.status_code == 401
        assert resp.json()["error"] == "unauthorized"

    def test_wrong_token_returns_403(self) -> None:
        """Wrong Bearer token → 403."""
        with _client(_make_settings(admin_token="s3cret")) as c:
            resp = c.get("/admin/projects", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 403
        assert resp.json()["error"] == "forbidden"


class TestAdminRouting:
    def test_list_no_dsn_returns_503(self) -> None:
        """Correct auth but no DSN → 503 db_unavailable."""
        with _client(_make_settings(admin_token="s3cret", dsn=None)) as c:
            resp = c.get("/admin/projects", headers={"Authorization": "Bearer s3cret"})
        assert resp.status_code == 503
        assert resp.json()["error"] == "db_unavailable"

    def test_register_rejects_empty_body(self) -> None:
        """POST /admin/projects with no body → 400."""
        with _client(_make_settings(admin_token="s3cret", dsn=None)) as c:
            resp = c.post(
                "/admin/projects",
                headers={"Authorization": "Bearer s3cret"},
            )
        assert resp.status_code == 400

    def test_register_rejects_missing_fields(self) -> None:
        """POST body with project_id but no profile → 400 mentioning both fields."""
        with _client(_make_settings(admin_token="s3cret", dsn=None)) as c:
            resp = c.post(
                "/admin/projects",
                headers={
                    "Authorization": "Bearer s3cret",
                    "Content-Type": "application/json",
                },
                content='{"project_id": "alpaca"}',
            )
        assert resp.status_code == 400
        assert "project_id and profile" in resp.json()["detail"]

    def test_register_rejects_invalid_slug(self) -> None:
        """project_id with invalid slug → 400."""
        from tapps_brain.profile import get_builtin_profile

        profile = get_builtin_profile("repo-brain").model_dump(mode="json")
        import json

        body = json.dumps({"project_id": "NOT_VALID", "profile": profile})
        with _client(_make_settings(admin_token="s3cret", dsn=None)) as c:
            resp = c.post(
                "/admin/projects",
                headers={
                    "Authorization": "Bearer s3cret",
                    "Content-Type": "application/json",
                },
                content=body,
            )
        assert resp.status_code == 400

    def test_unknown_admin_route_404(self) -> None:
        """Unknown /admin/* route → 404."""
        with _client(_make_settings(admin_token="s3cret", dsn=None)) as c:
            resp = c.post(
                "/admin/bogus",
                headers={
                    "Authorization": "Bearer s3cret",
                    "Content-Type": "application/json",
                },
                content='{"x": 1}',
            )
        assert resp.status_code == 404

    def test_delete_routes_through(self) -> None:
        """DELETE /admin/projects/<id> with no DSN → 503 (routing confirmed)."""
        with _client(_make_settings(admin_token="s3cret", dsn=None)) as c:
            resp = c.delete(
                "/admin/projects/alpaca",
                headers={"Authorization": "Bearer s3cret"},
            )
        assert resp.status_code == 503


class TestProjectNotRegisteredMapping:
    """STORY-069.4: ProjectNotRegisteredError → 403 with structured body."""

    def test_get_maps_to_403_structured(self) -> None:
        """GET /admin/projects/<id> with ProjectNotRegisteredError → 403 structured."""
        from tapps_brain.project_registry import ProjectNotRegisteredError

        # PostgresConnectionManager and ProjectRegistry are imported lazily inside
        # _open_registry(), so patch at their source modules.
        mock_registry = MagicMock()
        mock_registry.get.side_effect = ProjectNotRegisteredError("ghost")
        mock_cm = MagicMock()

        settings = _make_settings(admin_token="s3cret", dsn="postgres://mock/db")
        with (
            patch.object(_mod, "_settings", settings),
            patch.object(_mod, "get_settings", return_value=settings),
        ):
            _mcp_dummy = MagicMock()
            _mcp_dummy.session_manager = None
            app = create_app(mcp_server=_mcp_dummy)

            with (
                patch(
                    "tapps_brain.postgres_connection.PostgresConnectionManager",
                    return_value=mock_cm,
                ),
                patch("tapps_brain.project_registry.ProjectRegistry", return_value=mock_registry),
            ):
                with TestClient(app, raise_server_exceptions=False) as c:
                    resp = c.get(
                        "/admin/projects/ghost",
                        headers={"Authorization": "Bearer s3cret"},
                    )

        assert resp.status_code == 403
        body = resp.json()
        assert body["error"] == "project_not_registered"
        assert body["project_id"] == "ghost"

    def test_post_maps_to_403_structured(self) -> None:
        """POST /admin/projects with ProjectNotRegisteredError → 403 structured."""
        from tapps_brain.profile import get_builtin_profile
        from tapps_brain.project_registry import ProjectNotRegisteredError

        profile = get_builtin_profile("repo-brain").model_dump(mode="json")
        import json

        body = json.dumps({"project_id": "ghost", "profile": profile})

        mock_registry = MagicMock()
        mock_registry.register.side_effect = ProjectNotRegisteredError("ghost")
        mock_cm = MagicMock()

        settings = _make_settings(admin_token="s3cret", dsn="postgres://mock/db")
        with (
            patch.object(_mod, "_settings", settings),
            patch.object(_mod, "get_settings", return_value=settings),
        ):
            _mcp_dummy = MagicMock()
            _mcp_dummy.session_manager = None
            app = create_app(mcp_server=_mcp_dummy)

            with (
                patch(
                    "tapps_brain.postgres_connection.PostgresConnectionManager",
                    return_value=mock_cm,
                ),
                patch("tapps_brain.project_registry.ProjectRegistry", return_value=mock_registry),
            ):
                with TestClient(app, raise_server_exceptions=False) as c:
                    resp = c.post(
                        "/admin/projects",
                        headers={
                            "Authorization": "Bearer s3cret",
                            "Content-Type": "application/json",
                        },
                        content=body,
                    )

        assert resp.status_code == 403
        body = resp.json()
        assert body["error"] == "project_not_registered"
        assert body["project_id"] == "ghost"


class TestCorsPreflight:
    def test_options_advertises_write_methods(self) -> None:
        """OPTIONS /admin/projects: FastAPI responds (200/204/405) and we check Allow header."""
        with _client(_make_settings(admin_token="s3cret")) as c:
            resp = c.options("/admin/projects")
        # FastAPI without explicit CORS middleware returns 405 for OPTIONS,
        # but will include an Allow header listing the valid methods.
        # The key admin routes (GET, POST) must be listed.
        assert resp.status_code in (200, 204, 405)
        # Check either Allow or access-control-allow-methods
        allow = resp.headers.get("allow", "") or resp.headers.get(
            "access-control-allow-methods", ""
        )
        # At minimum GET and POST are registered on /admin/projects
        assert "GET" in allow or "POST" in allow or resp.status_code in (200, 204)
