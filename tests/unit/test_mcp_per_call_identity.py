"""Unit tests for STORY-070.7 — per-call identity (agent_id / scope / group).

These tests exercise the transport-agnostic contextvars and helper functions
added to :mod:`tapps_brain.mcp_server`, plus the HTTP middleware bridging
``X-Tapps-Agent``, ``X-Tapps-Scope``, and ``X-Tapps-Group`` headers into the
contextvars.  No Postgres or Hive backend is required — the tests only
exercise the wiring layer.
"""

from __future__ import annotations

import pytest

from tapps_brain import mcp_server as mcp_mod
from tapps_brain.mcp_server import (
    REQUEST_AGENT_ID,
    REQUEST_GROUP,
    REQUEST_SCOPE,
    _current_request_group,
    _current_request_scope,
    _resolve_per_call_agent_id,
)


# ---------------------------------------------------------------------------
# _resolve_per_call_agent_id — precedence rules
# ---------------------------------------------------------------------------


def test_resolve_per_call_agent_id_call_param_wins() -> None:
    """Explicit call param beats contextvar and default."""
    token = REQUEST_AGENT_ID.set("ctx-agent")
    try:
        assert _resolve_per_call_agent_id("call-agent", default="srv") == "call-agent"
    finally:
        REQUEST_AGENT_ID.reset(token)


def test_resolve_per_call_agent_id_contextvar_wins_over_default() -> None:
    """When call param is empty, contextvar is used over the server default."""
    token = REQUEST_AGENT_ID.set("ctx-agent")
    try:
        assert _resolve_per_call_agent_id("", default="srv") == "ctx-agent"
    finally:
        REQUEST_AGENT_ID.reset(token)


def test_resolve_per_call_agent_id_default_fallback() -> None:
    """Falls back to server-level default when both call param and context are empty."""
    # Context is clean (no token set) — REQUEST_AGENT_ID default is None.
    assert _resolve_per_call_agent_id("", default="srv-default") == "srv-default"


def test_resolve_per_call_agent_id_whitespace_treated_as_empty() -> None:
    """Whitespace-only call params do not override the context / default."""
    token = REQUEST_AGENT_ID.set("ctx-agent")
    try:
        assert _resolve_per_call_agent_id("   ", default="srv") == "ctx-agent"
    finally:
        REQUEST_AGENT_ID.reset(token)


# ---------------------------------------------------------------------------
# _current_request_scope / _current_request_group — contextvar readers
# ---------------------------------------------------------------------------


def test_current_request_scope_returns_none_when_unset() -> None:
    assert _current_request_scope() is None


def test_current_request_group_returns_none_when_unset() -> None:
    assert _current_request_group() is None


def test_current_request_scope_from_contextvar() -> None:
    token = REQUEST_SCOPE.set("domain")
    try:
        assert _current_request_scope() == "domain"
    finally:
        REQUEST_SCOPE.reset(token)


def test_current_request_group_from_contextvar() -> None:
    token = REQUEST_GROUP.set("frontend-guild")
    try:
        assert _current_request_group() == "frontend-guild"
    finally:
        REQUEST_GROUP.reset(token)


def test_current_request_scope_whitespace_returns_none() -> None:
    token = REQUEST_SCOPE.set("   ")
    try:
        assert _current_request_scope() is None
    finally:
        REQUEST_SCOPE.reset(token)


# ---------------------------------------------------------------------------
# HTTP middleware — header bridging into contextvars
# ---------------------------------------------------------------------------


def _build_app_no_auth():
    """Construct an ASGI app with McpTenantMiddleware but no auth token.

    Returns ``(app, probe_holder)`` where ``probe_holder`` is a dict that the
    downstream probe endpoint fills with the contextvar values observed
    *inside* the middleware-managed request.
    """
    pytest.importorskip("fastapi")
    pytest.importorskip("starlette")
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    from tapps_brain.http_adapter import McpTenantMiddleware

    app = FastAPI()
    app.add_middleware(McpTenantMiddleware)

    @app.post("/mcp/probe")
    async def probe() -> JSONResponse:  # noqa: ANN202 — FastAPI handler
        return JSONResponse(
            {
                "agent_id": REQUEST_AGENT_ID.get(),
                "scope": REQUEST_SCOPE.get(),
                "group": REQUEST_GROUP.get(),
            }
        )

    return app


def test_mcp_tenant_middleware_sets_scope_group(monkeypatch: pytest.MonkeyPatch) -> None:
    """``X-Tapps-Scope`` / ``X-Tapps-Group`` populate REQUEST_SCOPE / REQUEST_GROUP."""
    pytest.importorskip("starlette")
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient

    # Ensure auth is off for this minimal test app.
    monkeypatch.delenv("TAPPS_BRAIN_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("TAPPS_BRAIN_HTTP_AUTH_TOKEN", raising=False)

    # Reset the module-level settings singleton so ``get_settings()`` sees
    # the env-var changes we just applied.
    import tapps_brain.http_adapter as ha

    ha._settings = ha._Settings()  # type: ignore[attr-defined]

    app = _build_app_no_auth()
    with TestClient(app) as client:
        resp = client.post(
            "/mcp/probe",
            headers={
                "X-Project-Id": "proj-A",
                "X-Agent-Id": "legacy-agent",
                "X-Tapps-Scope": "hive",
                "X-Tapps-Group": "dev-pipeline",
            },
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["agent_id"] == "legacy-agent"
    assert body["scope"] == "hive"
    assert body["group"] == "dev-pipeline"

    # After the request finishes the contextvars must be reset to None.
    assert REQUEST_AGENT_ID.get() is None
    assert REQUEST_SCOPE.get() is None
    assert REQUEST_GROUP.get() is None


def test_mcp_tenant_middleware_x_tapps_agent_overrides_x_agent_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``X-Tapps-Agent`` takes precedence over the legacy ``X-Agent-Id``."""
    pytest.importorskip("starlette")
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient

    monkeypatch.delenv("TAPPS_BRAIN_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("TAPPS_BRAIN_HTTP_AUTH_TOKEN", raising=False)

    import tapps_brain.http_adapter as ha

    # Reset the module-level settings singleton so ``get_settings()`` sees the
    # env-var changes we just applied.
    ha._settings = ha._Settings()  # type: ignore[attr-defined]

    app = _build_app_no_auth()
    with TestClient(app) as client:
        resp = client.post(
            "/mcp/probe",
            headers={
                "X-Project-Id": "proj-A",
                "X-Agent-Id": "legacy-agent",
                "X-Tapps-Agent": "canonical-agent",
            },
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["agent_id"] == "canonical-agent"


def test_mcp_tenant_middleware_missing_project_id_returns_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without ``X-Project-Id`` the middleware returns 400 (pre-existing contract)."""
    pytest.importorskip("starlette")
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient

    monkeypatch.delenv("TAPPS_BRAIN_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("TAPPS_BRAIN_HTTP_AUTH_TOKEN", raising=False)

    import tapps_brain.http_adapter as ha

    # Reset the module-level settings singleton so ``get_settings()`` sees the
    # env-var changes we just applied.
    ha._settings = ha._Settings()  # type: ignore[attr-defined]

    app = _build_app_no_auth()
    with TestClient(app) as client:
        resp = client.post(
            "/mcp/probe",
            headers={"X-Tapps-Agent": "a"},
        )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Backward compatibility — tool signatures accept no agent_id
# ---------------------------------------------------------------------------


def test_backward_compat_brain_remember_accepts_no_agent_id_param() -> None:
    """Tools must keep their pre-STORY-070.7 callable shape (agent_id optional)."""
    import inspect

    # Re-read the module source to check tool signatures without booting
    # a full FastMCP server (which would touch Hive / Postgres).
    src = inspect.getsource(mcp_mod)
    # These tools gained the optional ``agent_id=""`` kwarg in STORY-070.7.
    expected_defaults = (
        "def brain_remember(",
        "def brain_recall(",
        "def memory_save(",
        "def memory_recall(",
    )
    for marker in expected_defaults:
        # Each signature should contain `agent_id: str = ""` somewhere in its
        # parameter block, and the defaults must keep every other param
        # optional.
        idx = src.find(marker)
        assert idx != -1, f"tool definition not found: {marker}"
        # Grab the signature up to the closing paren.
        sig_end = src.find(") -> str:", idx)
        assert sig_end != -1, f"signature end not found for {marker}"
        signature = src[idx:sig_end]
        assert 'agent_id: str = ""' in signature, (
            f"{marker} is missing the per-call ``agent_id`` override parameter"
        )


def test_resolve_per_call_agent_id_ignores_empty_contextvar() -> None:
    """An empty-string contextvar does not clobber the default fallback."""
    token = REQUEST_AGENT_ID.set("")
    try:
        assert _resolve_per_call_agent_id("", default="srv-default") == "srv-default"
    finally:
        REQUEST_AGENT_ID.reset(token)
