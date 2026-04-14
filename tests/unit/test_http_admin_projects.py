"""HTTP admin-route tests for EPIC-069 project registration.

Focus: auth gating, routing, and request-shape validation.  Live DB
behavior is covered by the integration suite (Story 69.8).
"""

from __future__ import annotations

import http.client
import json
import socket
import time
from typing import Any

import pytest

from tapps_brain.http_adapter import HttpAdapter


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_server(port: int, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"HTTP adapter did not start on port {port} within {timeout}s")


def _request(
    port: int,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any] | str]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    payload: str | None = None
    hdrs = dict(headers or {})
    if body is not None:
        payload = json.dumps(body)
        hdrs.setdefault("Content-Type", "application/json")
    try:
        conn.request(method, path, body=payload, headers=hdrs)
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8")
        ct = resp.getheader("Content-Type", "")
        if "application/json" in ct:
            return resp.status, json.loads(raw)
        return resp.status, raw
    finally:
        conn.close()


@pytest.fixture()
def adapter_admin(monkeypatch: pytest.MonkeyPatch):
    """Adapter with admin_token set, DSN *unset* — exercises auth + 503 paths."""
    monkeypatch.delenv("TAPPS_BRAIN_DATABASE_URL", raising=False)
    monkeypatch.delenv("TAPPS_BRAIN_HIVE_DSN", raising=False)
    port = _free_port()
    a = HttpAdapter(host="127.0.0.1", port=port, dsn=None, admin_token="s3cret")
    a.start()
    _wait_for_server(port)
    yield a, port
    a.stop()


@pytest.fixture()
def adapter_no_admin(monkeypatch: pytest.MonkeyPatch):
    """Adapter with NO admin token — /admin/* must 503."""
    monkeypatch.delenv("TAPPS_BRAIN_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("TAPPS_BRAIN_DATABASE_URL", raising=False)
    port = _free_port()
    a = HttpAdapter(host="127.0.0.1", port=port, dsn=None)
    a.start()
    _wait_for_server(port)
    yield a, port
    a.stop()


class TestAdminAuthGate:
    def test_admin_disabled_returns_503_when_no_token(self, adapter_no_admin) -> None:
        _, port = adapter_no_admin
        status, body = _request(port, "GET", "/admin/projects")
        assert status == 503
        assert body["error"] == "admin_disabled"

    def test_missing_bearer_returns_401(self, adapter_admin) -> None:
        _, port = adapter_admin
        status, body = _request(port, "GET", "/admin/projects")
        assert status == 401
        assert body["error"] == "unauthorized"

    def test_wrong_token_returns_403(self, adapter_admin) -> None:
        _, port = adapter_admin
        status, body = _request(
            port, "GET", "/admin/projects", headers={"Authorization": "Bearer wrong"}
        )
        assert status == 403
        assert body["error"] == "forbidden"


class TestAdminRouting:
    def test_list_no_dsn_returns_503(self, adapter_admin) -> None:
        _, port = adapter_admin
        status, body = _request(
            port, "GET", "/admin/projects", headers={"Authorization": "Bearer s3cret"}
        )
        assert status == 503
        assert body["error"] == "db_unavailable"

    def test_register_rejects_empty_body(self, adapter_admin) -> None:
        _, port = adapter_admin
        status, body = _request(
            port, "POST", "/admin/projects", headers={"Authorization": "Bearer s3cret"}
        )
        assert status == 400

    def test_register_rejects_missing_fields(self, adapter_admin) -> None:
        _, port = adapter_admin
        status, body = _request(
            port,
            "POST",
            "/admin/projects",
            headers={"Authorization": "Bearer s3cret"},
            body={"project_id": "alpaca"},  # missing profile
        )
        assert status == 400
        assert "project_id and profile" in body["detail"]

    def test_register_rejects_invalid_slug(self, adapter_admin) -> None:
        from tapps_brain.profile import get_builtin_profile

        _, port = adapter_admin
        profile = get_builtin_profile("repo-brain").model_dump(mode="json")
        status, body = _request(
            port,
            "POST",
            "/admin/projects",
            headers={"Authorization": "Bearer s3cret"},
            body={"project_id": "NOT_VALID", "profile": profile},
        )
        assert status == 400

    def test_unknown_admin_route_404(self, adapter_admin) -> None:
        _, port = adapter_admin
        status, _ = _request(
            port,
            "POST",
            "/admin/bogus",
            headers={"Authorization": "Bearer s3cret"},
            body={"x": 1},
        )
        assert status == 404

    def test_delete_routes_through(self, adapter_admin) -> None:
        _, port = adapter_admin
        # DSN unset → short-circuits to 503 before DB attempt, proving
        # DELETE routing reaches the admin handler.
        status, body = _request(
            port,
            "DELETE",
            "/admin/projects/alpaca",
            headers={"Authorization": "Bearer s3cret"},
        )
        assert status == 503


class TestProjectNotRegisteredMapping:
    """STORY-069.4: ProjectNotRegisteredError → 403 with structured body."""

    def test_get_maps_to_403_structured(
        self, adapter_admin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tapps_brain import http_adapter as mod
        from tapps_brain.project_registry import ProjectNotRegisteredError

        _, port = adapter_admin

        def _boom(self, project_id: str) -> None:
            raise ProjectNotRegisteredError(project_id)

        monkeypatch.setattr(
            mod._Handler, "_handle_admin_project_show", _boom, raising=True
        )
        status, body = _request(
            port,
            "GET",
            "/admin/projects/ghost",
            headers={"Authorization": "Bearer s3cret"},
        )
        assert status == 403
        assert body == {"error": "project_not_registered", "project_id": "ghost"}

    def test_post_maps_to_403_structured(
        self, adapter_admin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tapps_brain import http_adapter as mod
        from tapps_brain.project_registry import ProjectNotRegisteredError

        _, port = adapter_admin

        def _boom(self) -> None:
            raise ProjectNotRegisteredError("ghost")

        monkeypatch.setattr(
            mod._Handler, "_handle_admin_projects_register", _boom, raising=True
        )
        status, body = _request(
            port,
            "POST",
            "/admin/projects",
            headers={"Authorization": "Bearer s3cret"},
            body={"project_id": "ghost"},
        )
        assert status == 403
        assert body == {"error": "project_not_registered", "project_id": "ghost"}


class TestCorsPreflight:
    def test_options_advertises_write_methods(self, adapter_admin) -> None:
        _, port = adapter_admin
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            conn.request("OPTIONS", "/admin/projects")
            resp = conn.getresponse()
            resp.read()
            headers = {k.lower(): v for k, v in resp.getheaders()}
        finally:
            conn.close()
        assert resp.status == 204
        allowed = headers.get("access-control-allow-methods", "")
        assert "POST" in allowed
        assert "DELETE" in allowed
        assert "X-Tapps-Project" in headers.get("access-control-allow-headers", "")
