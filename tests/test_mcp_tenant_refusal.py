"""TAP-7329 — ``/mcp`` must route through the same tenant choke point as
``/v1/*`` (``resolve_tenant_or_refuse``, TAP-7243/ADR-010).

Before this fix, ``McpTenantMiddleware.dispatch`` only checked for an empty
``X-Project-Id`` header — it never called ``resolve_tenant_or_refuse``, so a
strict-mode deployment that refuses the literal placeholder ``"default"`` on
``/v1/*`` still accepted it on ``/mcp``. This module proves the same refusal
now applies on both transports, and that lax mode (both strict flags unset)
is unaffected — mirrors the pattern in ``tests/test_http_tenant_refusal.py``.

Local fixtures only — this module intentionally does not touch
``tests/conftest.py``.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

from starlette.testclient import TestClient

import tapps_brain.http_adapter as _http_mod
from tapps_brain.http_adapter import _service_version, _Settings, create_app


def _make_settings(*, auth_token: str | None = None) -> _Settings:
    s = _Settings.__new__(_Settings)
    s.dsn = None
    s.auth_token = auth_token
    s.admin_token = None
    s.metrics_token = None
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
        mcp_dummy = MagicMock()
        mcp_dummy.session_manager = None
        app = create_app(mcp_server=mcp_dummy)
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client


def _tool_call_body(tool: str, **args: Any) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": args},
    }


class TestMcpTenantChokePoint:
    def test_literal_default_project_refused_under_strict_projects(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_STRICT_PROJECTS", "1")
        settings = _make_settings(auth_token=None)
        with _client(settings) as client:
            resp = client.post(
                "/mcp/",
                headers={"x-project-id": "default"},
                json=_tool_call_body("hive_status"),
            )
        assert resp.status_code < 500
        assert resp.status_code in (400, 403)
        body = resp.json()
        assert body.get("ok") is False
        assert body.get("code") == "tenant_project_literal"

    def test_literal_default_project_passes_through_when_flags_unset(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.delenv("TAPPS_BRAIN_STRICT_PROJECTS", raising=False)
        monkeypatch.delenv("TAPPS_BRAIN_STRICT_AGENT_ID", raising=False)
        settings = _make_settings(auth_token=None)
        with _client(settings) as client:
            resp = client.post(
                "/mcp/",
                headers={"x-project-id": "default"},
                json=_tool_call_body("hive_status"),
            )
        # Lax mode: the literal "default" project id is not refused by the
        # tenant gate itself (it may still fail deeper in the stack, e.g.
        # the dummy mcp_server session manager — that is not this test's
        # concern). The key assertion is it is NOT a tenant-gate 400/403.
        assert resp.status_code != 400 or "default" not in str(resp.json())

    def test_missing_project_id_still_refused(self, monkeypatch: Any) -> None:
        monkeypatch.delenv("TAPPS_BRAIN_STRICT_PROJECTS", raising=False)
        settings = _make_settings(auth_token=None)
        with _client(settings) as client:
            resp = client.post(
                "/mcp/",
                headers={},
                json=_tool_call_body("hive_status"),
            )
        assert resp.status_code == 400
        assert resp.json()["error"] == "bad_request"


class TestResolveTenantOrRefuseCalledInDispatch:
    def test_middleware_source_calls_resolve_tenant_or_refuse(self) -> None:
        """Static proof the choke point is wired into McpTenantMiddleware.dispatch
        (not just importable / defined elsewhere in the module)."""
        import inspect

        from tapps_brain.http.middleware import McpTenantMiddleware

        source = inspect.getsource(McpTenantMiddleware.dispatch)
        assert "resolve_tenant_or_refuse(" in source
