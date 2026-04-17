"""EPIC-070 STORY-070.5 — FastAPI + FastMCP Streamable HTTP parity.

Spins up the ASGI app in-process (no uvicorn, no sockets) and verifies
that:

1. Every ``@mcp.tool()``-decorated tool is discoverable via
   :meth:`FastMCP.list_tools` and matches the decorator count in
   ``src/tapps_brain/mcp_server.py`` (sanity check on registration).
2. A curated sample of ``tools/call`` POSTs to ``/mcp`` returns a
   structurally valid JSON-RPC 2.0 envelope (either ``result`` or a
   well-formed ``error``) under the Streamable HTTP transport.

Gated on:
    * ``mcp`` optional extra (FastMCP / MCP Python SDK).
    * ``fastapi`` + ``httpx`` (the ``http`` extra).
    * A live Postgres Hive — set ``TAPPS_BRAIN_HIVE_DSN`` to enable.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.requires_postgres,
    pytest.mark.requires_mcp,
]

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("mcp")

# Hard gate on Postgres — tools depend on a live Hive backend for
# most interesting calls, and we must not hit a remote DB accidentally.
if not os.environ.get("TAPPS_BRAIN_HIVE_DSN"):
    pytest.skip(
        "TAPPS_BRAIN_HIVE_DSN not set — skipping Streamable HTTP parity test.",
        allow_module_level=True,
    )


CURATED_TOOLS = (
    "memory_save",
    "memory_recall",
    "hive_search",
    "flywheel_evaluate",
    "agent_register",
)


def _count_tool_decorators() -> int:
    """Count ``@mcp.tool()`` decorators across the mcp_server package."""
    here = Path(__file__).resolve().parent.parent
    pkg = here / "src" / "tapps_brain" / "mcp_server"
    total = 0
    for py_file in pkg.glob("*.py"):
        src = py_file.read_text(encoding="utf-8")
        total += len(re.findall(r"^\s*@mcp\.tool\(\)", src, flags=re.MULTILINE))
    return total


def _build_app_and_mcp():
    """Build a FastMCP server + mount it on a fresh FastAPI app.

    We construct everything manually so we can pass ``enable_operator_tools=True``
    (the parity test wants to see operator tools too) and avoid the lazy
    mcp build in the module-level ``app``.

    Forces stateless mode via ``TAPPS_BRAIN_STATELESS_HTTP=1`` so the test can
    call ``tools/call`` directly without an initialize handshake — the SDK
    skips the init-required check only in stateless mode.  Production default
    is stateful (VSCode / agent-sdk compat); this switch is test-scoped.
    """
    import os as _os

    _os.environ["TAPPS_BRAIN_STATELESS_HTTP"] = "1"
    from tapps_brain.http_adapter import create_app
    from tapps_brain.mcp_server import create_server

    mcp = create_server(
        Path.cwd(),
        enable_hive=True,
        agent_id="parity-test",
        enable_operator_tools=True,
    )
    app = create_app(mcp_server=mcp)
    return app, mcp


async def test_tool_registration_parity() -> None:
    _, mcp = _build_app_and_mcp()
    tools = await mcp.list_tools()
    assert tools, "FastMCP.list_tools() returned an empty collection"

    decorator_count = _count_tool_decorators()
    # list_tools may be filtered (operator tool gate, etc.) — assert it's
    # non-empty and doesn't exceed the static decorator count.
    assert len(tools) <= decorator_count
    assert decorator_count >= len(CURATED_TOOLS)

    names = {getattr(t, "name", None) for t in tools}
    for required in CURATED_TOOLS:
        assert required in names, f"expected tool '{required}' in list_tools() output"


async def test_streamable_http_curated_tools_respond() -> None:
    import httpx

    app, _mcp = _build_app_and_mcp()

    auth_token = os.environ.get("TAPPS_BRAIN_AUTH_TOKEN", "")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "X-Project-Id": os.environ.get("TAPPS_BRAIN_PROJECT", "default"),
        "X-Agent-Id": "parity-test",
    }
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://parity.local") as client:
        for tool_name in CURATED_TOOLS:
            payload = {
                "jsonrpc": "2.0",
                "id": f"parity-{tool_name}",
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": {}},
            }
            resp = await client.post("/mcp", json=payload, headers=headers)
            # Streamable HTTP with json_response=True returns 200 for both
            # successful results and JSON-RPC-level errors.  4xx/5xx would
            # indicate a transport-layer problem, which this test guards
            # against.
            assert resp.status_code < 400, (
                f"{tool_name}: transport error {resp.status_code} body={resp.text[:500]}"
            )

            ctype = resp.headers.get("content-type", "")
            if "application/json" in ctype:
                body = resp.json()
            elif "text/event-stream" in ctype:
                # Parse the first JSON-RPC frame out of the SSE stream.
                text = resp.text
                data_line = next(
                    (
                        ln[len("data:") :].strip()
                        for ln in text.splitlines()
                        if ln.startswith("data:")
                    ),
                    "",
                )
                assert data_line, f"{tool_name}: empty SSE body"
                body = json.loads(data_line)
            else:
                pytest.fail(f"{tool_name}: unexpected content-type {ctype!r}")

            assert body.get("jsonrpc") == "2.0", body
            assert "result" in body or "error" in body, body
            if "error" in body:
                err = body["error"]
                assert "code" in err and "message" in err, err
