"""TestClient integration tests for the RestProfileGateMiddleware (TAP-1929).

These exercise the middleware end-to-end on a FastAPI app — confirming the
``/v1/*`` denial wire shape, the public-path passthrough, the
``/v1/tools/list`` per-profile filter, and the unknown-profile 400 response.

These tests do NOT spin up Postgres — they stand up a minimal app with the
real middleware plus a stub route handler that always returns 200.  The
goal is to verify the middleware's gating decisions, not the downstream
service layer.
"""

from __future__ import annotations

from typing import Any

import pytest

try:
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    pytest.skip("fastapi not installed", allow_module_level=True)

from tapps_brain.http.middleware import RestProfileGateMiddleware


def _build_app() -> FastAPI:
    """Build a minimal app with just the gate middleware and stub handlers.

    Every gated handler returns ``{"ok": True}`` so a 200 means the middleware
    let the call through and a 403 means the middleware denied it.  The
    ``/v1/tools/list`` route is wired separately so the test can verify the
    public-path passthrough independently.
    """
    app = FastAPI()
    app.add_middleware(RestProfileGateMiddleware)

    async def _stub(request: Request) -> JSONResponse:
        return JSONResponse(content={"ok": True})

    # Gated routes (a sample — covers each profile bucket).
    app.add_api_route("/v1/remember", _stub, methods=["POST"])
    app.add_api_route("/v1/forget", _stub, methods=["POST"])
    app.add_api_route("/v1/reinforce", _stub, methods=["POST"])
    app.add_api_route("/v1/recall", _stub, methods=["POST"])
    app.add_api_route("/v1/experience", _stub, methods=["POST"])
    app.add_api_route("/v1/kg/feedback", _stub, methods=["POST"])
    # Public path — middleware must pass it through.
    app.add_api_route("/v1/tools/list", _stub, methods=["GET"])
    # Unmapped /v1/* — middleware must pass through (admin / future routes).
    app.add_api_route("/v1/admin/something", _stub, methods=["POST"])
    # Non-/v1 path — middleware ignores entirely.
    app.add_api_route("/health", _stub, methods=["GET"])
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_build_app())


class TestRestProfileGateMiddlewareIntegration:
    def test_default_profile_permits_full_surface(self, client: TestClient) -> None:
        """No header → server default profile (``full``) → all gated calls pass."""
        for path in ["/v1/remember", "/v1/forget", "/v1/reinforce", "/v1/recall"]:
            r = client.post(path, json={})
            assert r.status_code == 200, f"{path} should pass under default profile"

    def test_agent_brain_profile_blocks_memory_layer_endpoints(self, client: TestClient) -> None:
        """X-Brain-Profile: agent_brain blocks /v1/reinforce (memory_reinforce)."""
        r = client.post("/v1/reinforce", json={}, headers={"X-Brain-Profile": "agent_brain"})
        assert r.status_code == 403
        body = r.json()
        assert body["error"] == "out_of_profile"
        assert body["data"]["reason"] == "out_of_profile"
        assert body["data"]["tool"] == "memory_reinforce"
        assert body["data"]["profile"] == "agent_brain"

    def test_agent_brain_profile_permits_brain_facade(self, client: TestClient) -> None:
        """X-Brain-Profile: agent_brain admits /v1/recall (brain_recall) and friends."""
        for path in ["/v1/recall", "/v1/remember", "/v1/forget", "/v1/experience"]:
            r = client.post(path, json={}, headers={"X-Brain-Profile": "agent_brain"})
            assert r.status_code == 200, f"{path} should pass for agent_brain"

    def test_reviewer_profile_is_read_only(self, client: TestClient) -> None:
        """reviewer profile (read-only) blocks the write endpoints.

        ``reviewer`` exposes brain_recall + a handful of read tools; it does
        **not** include brain_remember / brain_forget / brain_record_event.
        """
        for path in ["/v1/remember", "/v1/forget", "/v1/experience"]:
            r = client.post(path, json={}, headers={"X-Brain-Profile": "reviewer"})
            assert r.status_code == 403, f"{path} should be 403 for reviewer"
            body = r.json()
            assert body["error"] == "out_of_profile"

    def test_reviewer_profile_permits_recall(self, client: TestClient) -> None:
        r = client.post("/v1/recall", json={}, headers={"X-Brain-Profile": "reviewer"})
        assert r.status_code == 200

    def test_unknown_profile_returns_400(self, client: TestClient) -> None:
        r = client.post("/v1/recall", json={}, headers={"X-Brain-Profile": "nonexistent-profile"})
        assert r.status_code == 400
        body = r.json()
        assert body["error"] == "bad_request"
        assert "available" in body
        assert "agent_brain" in body["available"]

    def test_public_path_passes_through(self, client: TestClient) -> None:
        """/v1/tools/list is in PUBLIC_PATHS — middleware never gates it."""
        r = client.get("/v1/tools/list", headers={"X-Brain-Profile": "reviewer"})
        assert r.status_code == 200

    def test_unmapped_v1_path_passes_through(self, client: TestClient) -> None:
        """Unmapped /v1/* routes (admin/future) pass through this middleware.

        Bearer-token auth handles their access control via their own
        ``Depends(require_admin_auth)`` — the profile gate is intentionally
        narrow to the mapped tool surface.
        """
        r = client.post("/v1/admin/something", json={}, headers={"X-Brain-Profile": "reviewer"})
        assert r.status_code == 200

    def test_non_v1_path_ignored(self, client: TestClient) -> None:
        """Non-/v1 paths bypass this middleware entirely.

        ``/health`` is the canonical example — DNS-rebinding probes hit it
        before any auth layer; the profile gate must not interfere.
        """
        r = client.get("/health", headers={"X-Brain-Profile": "reviewer"})
        assert r.status_code == 200

    def test_denial_response_includes_full_data_shape(self, client: TestClient) -> None:
        """Validates the documented JSON-RPC -32602 mirror shape (TAP-1619)."""
        r = client.post("/v1/forget", json={}, headers={"X-Brain-Profile": "reviewer"})
        assert r.status_code == 403
        body = r.json()
        # Must match the wire shape used by the MCP path.
        assert set(body) >= {"error", "detail", "data"}
        assert set(body["data"]) == {"reason", "tool", "profile"}
        assert body["error"] == "out_of_profile"
        assert body["data"]["tool"] == "brain_forget"
        assert body["data"]["profile"] == "reviewer"


# ---------------------------------------------------------------------------
# TAP-1934 — record_many atomicity (unit test against a fake CM)
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, fail_on_call: int | None = None) -> None:
        self.fail_on_call = fail_on_call
        self.call_count = 0
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        # Pretend every INSERT RETURNING returns ("uuid-N",).
        self._next_id = 0

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...]) -> None:
        self.call_count += 1
        if self.fail_on_call is not None and self.call_count == self.fail_on_call:
            raise RuntimeError(f"simulated db failure on call #{self.fail_on_call}")
        self.executed.append((sql, params))

    def fetchone(self) -> tuple[str]:
        self._next_id += 1
        return (f"uuid-{self._next_id}",)


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor
        self.committed = False
        self.rolled_back = False

    def cursor(self) -> _FakeCursor:
        return self._cursor


class _FakeProjectContext:
    """Mimics the ``cm.project_context(project_id)`` context manager.

    Tracks whether an exception left the block — that's the proxy for
    "Postgres rollback would occur" since our fake doesn't run real SQL.
    """

    def __init__(self, conn: _FakeConnection) -> None:
        self.conn = conn
        self.exit_with_exception = False

    def __enter__(self) -> _FakeConnection:
        return self.conn

    def __exit__(self, exc_type: Any, *_: Any) -> bool:
        if exc_type is not None:
            self.exit_with_exception = True
        return False  # don't suppress


class _FakeCm:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor
        self.last_ctx: _FakeProjectContext | None = None

    def project_context(self, project_id: str) -> _FakeProjectContext:
        self.last_ctx = _FakeProjectContext(_FakeConnection(self._cursor))
        return self.last_ctx


class TestRecordManyAtomicity:
    def test_record_many_uses_single_cursor_block(self) -> None:
        """Every event in record_many shares one cm.project_context cursor."""
        from tapps_brain.experience import ExperienceEvent, ExperienceEventRecorder

        cursor = _FakeCursor()
        cm = _FakeCm(cursor)
        recorder = ExperienceEventRecorder(cm, project_id="p", brain_id="b", agent_id="a")
        events = [ExperienceEvent(event_type=f"e{i}", utility_score=0.5) for i in range(3)]
        results = recorder.record_many(events)
        assert len(results) == 3
        # All three events ran inside one project_context block.
        assert cm.last_ctx is not None
        assert cursor.call_count == 3, "3 INSERT EVENT statements expected"

    def test_record_many_failure_propagates_and_exits_context_with_exception(
        self,
    ) -> None:
        """A failure on event N propagates out of the shared context.

        The recorder relies on ``cm.project_context`` to wrap the cursor in a
        transaction that rolls back on exception.  Verifying that the
        exception leaves the context block (rather than being swallowed by
        the recorder) is enough — the real Postgres ``cm.project_context``
        commits on clean exit and rolls back on exception.
        """
        from tapps_brain.experience import ExperienceEvent, ExperienceEventRecorder

        # Fail on the 2nd cursor.execute() call → the 2nd event's INSERT
        # EVENT row.  Events 1's writes are queued in the same transaction.
        cursor = _FakeCursor(fail_on_call=2)
        cm = _FakeCm(cursor)
        recorder = ExperienceEventRecorder(cm, project_id="p", brain_id="b", agent_id="a")
        events = [
            ExperienceEvent(event_type="e1", utility_score=0.0),
            ExperienceEvent(event_type="e2", utility_score=0.0),
            ExperienceEvent(event_type="e3", utility_score=0.0),
        ]
        with pytest.raises(RuntimeError, match="simulated db failure"):
            recorder.record_many(events)
        assert cm.last_ctx is not None
        # Exception must leave the project_context block — real Postgres
        # rollback happens here.  Our fake just records the fact.
        assert cm.last_ctx.exit_with_exception is True

    def test_record_many_empty_returns_empty(self) -> None:
        from tapps_brain.experience import ExperienceEventRecorder

        cursor = _FakeCursor()
        cm = _FakeCm(cursor)
        recorder = ExperienceEventRecorder(cm, project_id="p", brain_id="b", agent_id="a")
        assert recorder.record_many([]) == []
        assert cursor.call_count == 0
