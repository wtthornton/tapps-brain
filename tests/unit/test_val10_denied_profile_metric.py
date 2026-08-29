"""VAL-10 (TAP-6696): a REST profile-gate denial records the same
``denied_profile`` outcome the MCP call-tool interceptor already emits.

Before this change, ``RestProfileGateMiddleware``'s denial branch only logged
(``rest_profile_gate.denied``) — nothing reached ``/metrics``. The wire-level
JSON error shape (``error: "out_of_profile"``, ``data.reason``) is untouched;
this only adds the metrics side-channel (SC-10 additive-only).
"""

from __future__ import annotations

import pytest

try:
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    pytest.skip("fastapi not installed", allow_module_level=True)

from tapps_brain.http.middleware import RestProfileGateMiddleware
from tapps_brain.mcp_server.tool_filter import (
    get_profile_filter_metrics_snapshot,
    reset_profile_filter_counters,
)


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RestProfileGateMiddleware)

    async def _stub(request: Request) -> JSONResponse:
        return JSONResponse(content={"ok": True})

    app.add_api_route("/v1/reinforce", _stub, methods=["POST"])
    app.add_api_route("/v1/recall", _stub, methods=["POST"])
    return app


@pytest.fixture
def client() -> TestClient:
    reset_profile_filter_counters()
    yield TestClient(_build_app())
    reset_profile_filter_counters()


class TestDeniedProfileMetric:
    def test_denied_rest_call_increments_denied_profile_counter(self, client: TestClient) -> None:
        r = client.post("/v1/reinforce", json={}, headers={"X-Brain-Profile": "agent_brain"})
        assert r.status_code == 403
        # Wire-level shape is unchanged (SC-10) — still "out_of_profile".
        assert r.json()["error"] == "out_of_profile"

        snap = get_profile_filter_metrics_snapshot()
        call_total = snap["call_total"]
        assert call_total.get(("agent_brain", "memory_reinforce", "denied_profile")) == 1

    def test_allowed_rest_call_does_not_increment_denied_counter(self, client: TestClient) -> None:
        r = client.post("/v1/recall", json={}, headers={"X-Brain-Profile": "agent_brain"})
        assert r.status_code == 200

        snap = get_profile_filter_metrics_snapshot()
        assert not snap["call_total"]

    def test_repeated_denials_accumulate(self, client: TestClient) -> None:
        for _ in range(3):
            client.post("/v1/reinforce", json={}, headers={"X-Brain-Profile": "agent_brain"})
        snap = get_profile_filter_metrics_snapshot()
        assert snap["call_total"][("agent_brain", "memory_reinforce", "denied_profile")] == 3
