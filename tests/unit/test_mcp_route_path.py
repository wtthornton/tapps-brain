"""TAP-509 — lock the public MCP route at a single ``/mcp``.

FastMCP's streamable-HTTP sub-app declares its own internal route
(``streamable_http_path``, default ``/mcp``).  Mounted at ``/mcp`` by
the FastAPI adapter without overriding the inner path, the public
endpoint becomes ``/mcp/mcp`` — what v3.7.2 shipped after the previous
attempt to fix the original `/v1/tools/{name}` 404.

The fix in ``http_adapter._get_mcp_asgi_sub`` is to set
``mcp.settings.streamable_http_path = "/"`` before calling
``streamable_http_app()``, so the inner route is at ``/`` and the
public path collapses back to a single ``/mcp``.

These tests assert that contract end-to-end:

1.  The FastAPI app exposes a Mount at ``/mcp`` (not ``/mcp/mcp``).
2.  The mounted sub-app's inner Starlette route is at ``/``.
3.  POSTing to ``/mcp/mcp`` 404s — the bad path is gone.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

starlette_testclient = pytest.importorskip("starlette.testclient")
from starlette.testclient import TestClient

from tapps_brain.http_adapter import create_app

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover — mcp extras not installed in lint job
    FastMCP = None  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Route-table assertions
# ---------------------------------------------------------------------------


def test_real_fastmcp_mount_path_is_single_mcp() -> None:
    """With a real FastMCP, the FastAPI app mounts at ``/mcp`` and the
    sub-app's inner route is at ``/`` — public path is ``/mcp``."""
    if FastMCP is None:
        pytest.skip("mcp package not installed")
    mcp = FastMCP("tapps-brain-test")
    app = create_app(mcp_server=mcp)

    mcp_mounts = [r for r in app.routes if getattr(r, "path", None) == "/mcp"]
    assert mcp_mounts, "FastAPI app must expose a /mcp mount"

    # The mount target is a Starlette sub-app — its routes should now be at "/".
    inner_app = mcp_mounts[0].app  # type: ignore[attr-defined]
    inner_paths = [getattr(r, "path", None) for r in inner_app.routes]
    assert "/" in inner_paths, (
        f"FastMCP sub-app inner route should be at '/', got {inner_paths!r} — "
        "TAP-509 regression (streamable_http_path not pinned to '/')"
    )
    assert "/mcp" not in inner_paths, (
        f"FastMCP sub-app inner route at '/mcp' would re-create /mcp/mcp public path; "
        f"got {inner_paths!r}"
    )


def test_double_mcp_path_returns_404() -> None:
    """POSTing to /mcp/mcp must 404 — TAP-509 collapsed the public path."""
    if FastMCP is None:
        pytest.skip("mcp package not installed")
    mcp = FastMCP("tapps-brain-test")
    app = create_app(mcp_server=mcp)
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post(
            "/mcp/mcp",
            headers={
                "Authorization": "Bearer x",
                "X-Project-Id": "p1",
                "Content-Type": "application/json",
            },
            content=b"{}",
        )
        assert resp.status_code == 404, (
            f"/mcp/mcp must 404 after TAP-509 (public path is /mcp); got {resp.status_code}"
        )


def test_dummy_mcp_mount_still_at_single_mcp() -> None:
    """Even with a stub MCP that has no .settings, the FastAPI mount stays at
    /mcp.  Guards against the dummy used by other unit tests accidentally
    restoring the /mcp/mcp path."""
    dummy = MagicMock()
    dummy.session_manager = None
    # No streamable_http_path attr on settings — the helper should skip the
    # override gracefully and still mount the sub-app at /mcp.
    app = create_app(mcp_server=dummy)
    mcp_mounts = [r for r in app.routes if getattr(r, "path", None) == "/mcp"]
    assert mcp_mounts, "FastAPI app must expose a /mcp mount even with a stub MCP"
