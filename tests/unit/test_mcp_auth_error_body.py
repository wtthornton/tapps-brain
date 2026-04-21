"""Enriched /mcp auth-failure response body.

When the ``McpTenantMiddleware`` rejects a request with 401 or 403, the
response body must include ``auth_model`` and ``expected_env`` (so clients
like tapps-mcp's ``auth_probe`` / ``tapps_doctor`` can surface the real
remediation) plus best-effort ``tool`` and ``project_id`` diagnostics
pulled from the JSON-RPC body and ``X-Project-Id`` header.

The underlying 401/403 status codes and the ``error`` field shape
(``unauthorized`` / ``forbidden``) are preserved — this is purely
additive metadata, so existing clients that only inspect status codes
keep working.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

from starlette.testclient import TestClient

import tapps_brain.http_adapter as _http_mod
from tapps_brain.http.middleware import (
    MCP_AUTH_EXPECTED_ENV,
    MCP_AUTH_MODEL,
    _mcp_auth_error_body,
    _peek_mcp_tool_name,
)
from tapps_brain.http_adapter import (
    _service_version,
    _Settings,
    create_app,
)


def _make_settings(auth_token: str | None) -> _Settings:
    s = _Settings.__new__(_Settings)
    s.dsn = None
    s.auth_token = auth_token
    s.admin_token = None
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


def _tool_call_body(tool: str, **args: Any) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": args},
    }


class TestPeekMcpToolName:
    def test_extracts_tool_name_from_tools_call(self) -> None:
        body = b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"hive_status"}}'
        assert _peek_mcp_tool_name(body) == "hive_status"

    def test_returns_none_for_non_tool_call_method(self) -> None:
        body = b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
        assert _peek_mcp_tool_name(body) is None

    def test_returns_none_for_malformed_json(self) -> None:
        assert _peek_mcp_tool_name(b"not-json{{") is None

    def test_returns_none_for_empty_body(self) -> None:
        assert _peek_mcp_tool_name(b"") is None

    def test_returns_none_when_params_missing_name(self) -> None:
        body = b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{}}'
        assert _peek_mcp_tool_name(body) is None

    def test_returns_none_for_batch_request(self) -> None:
        body = b'[{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"x"}}]'
        assert _peek_mcp_tool_name(body) is None


class TestMcpAuthErrorBody:
    def test_always_includes_auth_model_and_expected_env(self) -> None:
        body = _mcp_auth_error_body("Invalid token.", error="forbidden", project_id=None, tool=None)
        assert body["auth_model"] == MCP_AUTH_MODEL == "global_bearer"
        assert body["expected_env"] == MCP_AUTH_EXPECTED_ENV == "TAPPS_BRAIN_AUTH_TOKEN"
        assert body["error"] == "forbidden"
        assert body["detail"] == "Invalid token."

    def test_includes_tool_and_project_id_when_provided(self) -> None:
        body = _mcp_auth_error_body(
            "Invalid token.",
            error="forbidden",
            project_id="tapps-brain",
            tool="hive_status",
        )
        assert body["tool"] == "hive_status"
        assert body["project_id"] == "tapps-brain"

    def test_omits_tool_and_project_id_when_none(self) -> None:
        body = _mcp_auth_error_body(
            "Bearer token required for /mcp.",
            error="unauthorized",
            project_id=None,
            tool=None,
        )
        assert "tool" not in body
        assert "project_id" not in body


class TestMcpAuthRejectionBody:
    """End-to-end: hitting /mcp with a bad token returns the enriched body."""

    def test_wrong_token_returns_enriched_403_body(self) -> None:
        settings = _make_settings(auth_token="mcp-secret")
        with _client(settings) as client:
            resp = client.post(
                "/mcp/",
                headers={
                    "Authorization": "Bearer wrong-token",
                    "x-project-id": "tapps-brain",
                },
                json=_tool_call_body("hive_status"),
            )
        assert resp.status_code == 403
        body = resp.json()
        assert body["error"] == "forbidden"
        assert body["detail"] == "Invalid token."
        assert body["auth_model"] == "global_bearer"
        assert body["expected_env"] == "TAPPS_BRAIN_AUTH_TOKEN"
        assert body["tool"] == "hive_status"
        assert body["project_id"] == "tapps-brain"

    def test_missing_authorization_returns_enriched_401_body(self) -> None:
        settings = _make_settings(auth_token="mcp-secret")
        with _client(settings) as client:
            resp = client.post(
                "/mcp/",
                headers={"x-project-id": "tapps-brain"},
                json=_tool_call_body("memory_search", query="foo"),
            )
        assert resp.status_code == 401
        body = resp.json()
        assert body["error"] == "unauthorized"
        assert body["auth_model"] == "global_bearer"
        assert body["expected_env"] == "TAPPS_BRAIN_AUTH_TOKEN"
        assert body["tool"] == "memory_search"
        assert body["project_id"] == "tapps-brain"

    def test_rejection_body_omits_project_id_when_header_missing(self) -> None:
        settings = _make_settings(auth_token="mcp-secret")
        with _client(settings) as client:
            resp = client.post(
                "/mcp/",
                headers={"Authorization": "Bearer wrong"},
                json=_tool_call_body("hive_status"),
            )
        assert resp.status_code == 403
        body = resp.json()
        assert "project_id" not in body
        assert body["tool"] == "hive_status"

    def test_rejection_body_omits_tool_when_body_not_tool_call(self) -> None:
        settings = _make_settings(auth_token="mcp-secret")
        with _client(settings) as client:
            resp = client.post(
                "/mcp/",
                headers={
                    "Authorization": "Bearer wrong",
                    "x-project-id": "tapps-brain",
                },
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            )
        assert resp.status_code == 403
        body = resp.json()
        assert "tool" not in body
        assert body["project_id"] == "tapps-brain"
