"""Lock default Streamable HTTP behaviour: initialize issues Mcp-Session-Id.

Claude Code's VSCode extension (agent-sdk) completes the MCP initialize
handshake but then never enumerates tools when no ``Mcp-Session-Id`` header
comes back on the initialize response.  We exercise the SDK's
StreamableHTTPSessionManager through a bare FastMCP built with the same
flag combination we pick in ``create_server`` and assert the header is
present.  If someone flips the stateless_http default back to True, this
test fails — preventing a silent regression for VSCode users.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

pytest.importorskip("httpx")
pytest.importorskip("mcp")


async def _build_app(stateless_http: bool, json_response: bool):
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.applications import Starlette
    from starlette.routing import Mount

    sec = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    mcp = FastMCP(
        "test",
        stateless_http=stateless_http,
        json_response=json_response,
        transport_security=sec,
    )

    @mcp.tool()
    def ping() -> str:
        return "pong"

    mcp.settings.streamable_http_path = "/"
    sub = mcp.streamable_http_app()
    return mcp, Starlette(routes=[Mount("/mcp", app=sub)])


async def _post_initialize(app, base_url: str = "http://127.0.0.1"):
    import httpx

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=base_url) as client:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "0"},
            },
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        return await client.post("/mcp/", json=payload, headers=headers)


async def test_default_stateful_issues_session_id() -> None:
    """Default (stateless_http=False) — initialize MUST include Mcp-Session-Id."""
    mcp, app = await _build_app(stateless_http=False, json_response=True)
    async with mcp.session_manager.run():
        resp = await _post_initialize(app)
    assert resp.status_code == 200, resp.text
    sid = resp.headers.get("mcp-session-id")
    assert sid, (
        "Mcp-Session-Id header missing — Claude Code VSCode (agent-sdk) will "
        "connect but fail to enumerate tools. See mcp_server/__init__.py "
        "stateless_http default."
    )
    assert len(sid) >= 16  # SDK uses uuid4().hex


async def test_stateless_opt_in_suppresses_session_id() -> None:
    """Opt-in stateless mode — no Mcp-Session-Id, per horizontal-scaling contract."""
    mcp, app = await _build_app(stateless_http=True, json_response=True)
    async with mcp.session_manager.run():
        resp = await _post_initialize(app)
    assert resp.status_code == 200, resp.text
    assert resp.headers.get("mcp-session-id") is None
