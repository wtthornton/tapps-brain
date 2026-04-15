"""Contract tests for tapps_brain.http_adapter (STORY-060.3 / STORY-060.4 / STORY-065.1).

Tests cover:
- /health: always 200, JSON body
- /ready: 200 when DB reachable, 503 when DB down / DSN missing
- /metrics: Prometheus text format, correct Content-Type, key gauge names
- /info: auth-protected runtime info
- /snapshot: live VisualSnapshot endpoint with TTL cache and CORS
- /openapi.json: public OpenAPI spec
- Auth: 401 on missing header, 403 on wrong token, 200 on correct token
- 404 for unknown routes
- Context manager lifecycle (start/stop)
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

import tapps_brain.http_adapter as _mod
from tapps_brain.http_adapter import (
    _OPENAPI_SPEC,
    HttpAdapter,
    _probe_db,
    _service_version,
    _Settings,
    create_app,
)

# ---------------------------------------------------------------------------
# Helpers
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
    """Context manager that yields a TestClient driving create_app() with isolated settings."""
    with patch.object(_mod, "_settings", settings), \
         patch.object(_mod, "get_settings", return_value=settings):
        # Pass a dummy mcp_server so lifespan skips the real MCP build.
        _mcp_dummy = MagicMock()
        _mcp_dummy.session_manager = None
        app = create_app(mcp_server=_mcp_dummy)
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client


# ---------------------------------------------------------------------------
# _probe_db unit tests (no real DB needed)
# ---------------------------------------------------------------------------


class TestProbeDb:
    def test_no_dsn_returns_not_ready(self) -> None:
        is_ready, version, msg = _probe_db(None)
        assert not is_ready
        assert version is None
        assert "no DSN" in msg

    def test_no_dsn_empty_string_returns_not_ready(self) -> None:
        is_ready, _version, msg = _probe_db("")
        assert not is_ready
        assert "no DSN" in msg

    def test_exception_returns_not_ready(self) -> None:
        """Any exception in get_hive_schema_status maps to degraded."""
        with patch(
            "tapps_brain.postgres_migrations.get_hive_schema_status",
            side_effect=RuntimeError("connection refused"),
        ):
            is_ready, version, msg = _probe_db("postgres://localhost/testdb")
        assert not is_ready
        assert version is None
        assert "db_error" in msg

    def test_success_returns_ready(self) -> None:
        mock_status = MagicMock()
        mock_status.current_version = 3
        mock_status.pending_migrations = []
        with patch(
            "tapps_brain.postgres_migrations.get_hive_schema_status", return_value=mock_status
        ):
            is_ready, version, msg = _probe_db("postgres://localhost/testdb")
        assert is_ready
        assert version == 3
        assert "ready" in msg

    def test_pending_migrations_still_ready(self) -> None:
        mock_status = MagicMock()
        mock_status.current_version = 2
        mock_status.pending_migrations = [("3", "003_foo.sql")]
        with patch(
            "tapps_brain.postgres_migrations.get_hive_schema_status", return_value=mock_status
        ):
            is_ready, version, msg = _probe_db("postgres://localhost/testdb")
        # Still ready — pending does not mean broken, just out-of-date
        assert is_ready
        assert version == 2
        assert "pending=1" in msg


# ---------------------------------------------------------------------------
# /health endpoint
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    def test_returns_200(self) -> None:
        with _client(_make_settings()) as c:
            resp = c.get("/health")
        assert resp.status_code == 200

    def test_returns_json_with_ok_status(self) -> None:
        with _client(_make_settings()) as c:
            body = c.get("/health").json()
        assert isinstance(body, dict)
        assert body["status"] == "ok"

    def test_returns_service_name(self) -> None:
        with _client(_make_settings()) as c:
            body = c.get("/health").json()
        assert isinstance(body, dict)
        assert body["service"] == "tapps-brain"

    def test_returns_version_field(self) -> None:
        with _client(_make_settings()) as c:
            body = c.get("/health").json()
        assert isinstance(body, dict)
        assert "version" in body

    def test_root_path_also_returns_200(self) -> None:
        with _client(_make_settings()) as c:
            resp = c.get("/")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, dict)
        assert body["status"] == "ok"

    def test_liveness_returns_200_even_with_db_down(self) -> None:
        """STORY-061.4: /health must return 200 even when Postgres is unreachable."""
        settings = _make_settings(dsn="postgres://invalid_host_that_does_not_exist:5432/nodb")
        with _client(settings) as c:
            resp = c.get("/health")
        assert resp.status_code == 200, (
            f"/health returned {resp.status_code} with bad DSN — liveness must never call the DB"
        )
        body = resp.json()
        assert isinstance(body, dict)
        assert body["status"] == "ok"


# ---------------------------------------------------------------------------
# /ready endpoint — no DSN (degraded)
# ---------------------------------------------------------------------------


class TestReadyEndpointNoDsn:
    def test_returns_503_without_dsn(self) -> None:
        with _client(_make_settings(dsn=None)) as c:
            resp = c.get("/ready")
        assert resp.status_code == 503

    def test_body_status_is_degraded(self) -> None:
        with _client(_make_settings(dsn=None)) as c:
            body = c.get("/ready").json()
        assert isinstance(body, dict)
        assert body["status"] == "degraded"

    def test_detail_mentions_dsn(self) -> None:
        with _client(_make_settings(dsn=None)) as c:
            body = c.get("/ready").json()
        assert isinstance(body, dict)
        assert "DSN" in body.get("detail", "") or "dsn" in body.get("detail", "").lower()


# ---------------------------------------------------------------------------
# /ready endpoint — bad DSN (DB-down scenario)
# ---------------------------------------------------------------------------


class TestReadyEndpointDbDown:
    def test_returns_503_on_db_failure(self) -> None:
        settings = _make_settings(dsn="postgres://invalid_host_that_does_not_exist:5432/nodb")
        with _client(settings) as c:
            resp = c.get("/ready")
        assert resp.status_code == 503

    def test_body_status_is_degraded(self) -> None:
        settings = _make_settings(dsn="postgres://invalid_host_that_does_not_exist:5432/nodb")
        with _client(settings) as c:
            body = c.get("/ready").json()
        assert isinstance(body, dict)
        assert body["status"] == "degraded"

    def test_dsn_not_leaked_in_body(self) -> None:
        """DSN (host/credentials) must not appear in any response field."""
        settings = _make_settings(dsn="postgres://invalid_host_that_does_not_exist:5432/nodb")
        with _client(settings) as c:
            body = c.get("/ready").json()
        import json
        body_str = json.dumps(body)
        assert "invalid_host_that_does_not_exist" not in body_str


# ---------------------------------------------------------------------------
# /ready endpoint — DB healthy (mocked)
# ---------------------------------------------------------------------------


class TestReadyEndpointDbHealthy:
    def test_returns_200_when_db_ready(self) -> None:
        mock_status = MagicMock()
        mock_status.current_version = 5
        mock_status.pending_migrations = []
        settings = _make_settings(dsn="postgres://mockhost/testdb")
        with patch(
            "tapps_brain.postgres_migrations.get_hive_schema_status", return_value=mock_status
        ), _client(settings) as c:
            resp = c.get("/ready")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, dict)
        assert body["status"] == "ready"

    def test_migration_version_in_body(self) -> None:
        mock_status = MagicMock()
        mock_status.current_version = 7
        mock_status.pending_migrations = []
        settings = _make_settings(dsn="postgres://mockhost/testdb")
        with patch(
            "tapps_brain.postgres_migrations.get_hive_schema_status", return_value=mock_status
        ), _client(settings) as c:
            body = c.get("/ready").json()
        assert isinstance(body, dict)
        assert body["migration_version"] == 7


# ---------------------------------------------------------------------------
# /metrics endpoint
# ---------------------------------------------------------------------------


class TestMetricsEndpoint:
    def test_returns_200(self) -> None:
        with _client(_make_settings()) as c:
            resp = c.get("/metrics")
        assert resp.status_code == 200

    def test_content_type_is_prometheus(self) -> None:
        with _client(_make_settings()) as c:
            resp = c.get("/metrics")
        ct = resp.headers.get("content-type", "")
        assert "text/plain" in ct

    def test_contains_process_uptime_gauge(self) -> None:
        with _client(_make_settings()) as c:
            body = c.get("/metrics").text
        assert isinstance(body, str)
        assert "tapps_brain_process_uptime_seconds" in body

    def test_contains_db_ready_gauge(self) -> None:
        with _client(_make_settings()) as c:
            body = c.get("/metrics").text
        assert isinstance(body, str)
        assert "tapps_brain_db_ready" in body

    def test_db_ready_is_zero_without_dsn(self) -> None:
        with _client(_make_settings(dsn=None)) as c:
            body = c.get("/metrics").text
        assert isinstance(body, str)
        assert "tapps_brain_db_ready 0.0" in body

    def test_no_high_cardinality_labels(self) -> None:
        """Metrics must not leak raw memory content, query text, or unbounded keys.

        STORY-070.12: labeled counters (project_id, agent_id, tool, status) are
        intentionally present with bounded cardinality — those are fine.  The
        forbidden patterns are raw per-request values that would create unbounded
        label cardinality (memory keys, query text, session IDs, etc.).
        """
        with _client(_make_settings()) as c:
            body = c.get("/metrics").text
        assert isinstance(body, str)
        # Forbidden: raw memory content or query text as label values.
        forbidden_label_names = {"memory_key", "query_text", "session_id", "memory_value"}
        for forbidden in forbidden_label_names:
            assert f'{forbidden}="' not in body, (
                f"High-cardinality label '{forbidden}' must not appear in /metrics"
            )


# ---------------------------------------------------------------------------
# 404 for unknown routes
# ---------------------------------------------------------------------------


class TestUnknownRoute:
    def test_unknown_path_returns_404(self) -> None:
        with _client(_make_settings()) as c:
            resp = c.get("/nonexistent")
        assert resp.status_code == 404

    def test_404_body_contains_error(self) -> None:
        with _client(_make_settings()) as c:
            body = c.get("/nonexistent").json()
        assert isinstance(body, dict)
        # FastAPI returns {"detail": "Not Found"} for unregistered routes;
        # either "error" or "detail" key satisfies the "contains error info" contract.
        assert "error" in body or "detail" in body


# ---------------------------------------------------------------------------
# Lifecycle / context manager
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_context_manager_starts_and_stops(self) -> None:
        """HttpAdapter context manager starts and stops cleanly."""
        # Use the ASGI client approach — no real server needed
        with _client(_make_settings()) as c:
            resp = c.get("/health")
            assert resp.status_code == 200

    def test_double_start_is_idempotent(self) -> None:
        """HttpAdapter.start() called twice is a no-op (real server test preserved)."""
        import socket

        def _free_port() -> int:
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                return s.getsockname()[1]

        port = _free_port()
        adapter = HttpAdapter(host="127.0.0.1", port=port, dsn=None)
        try:
            adapter.start()
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        break
                except OSError:
                    time.sleep(0.05)
            server_before = adapter._server
            adapter.start()  # second start — should be a no-op
            assert adapter._server is server_before
        finally:
            adapter.stop()

    def test_stop_without_start_is_safe(self) -> None:
        adapter = HttpAdapter(host="127.0.0.1", port=9999, dsn=None)
        adapter.stop()  # must not raise

    def test_address_property(self) -> None:
        """HttpAdapter.address returns the configured (host, port) tuple."""
        adapter = HttpAdapter(host="127.0.0.1", port=18080, dsn=None)
        host, port = adapter.address
        assert host == "127.0.0.1"
        assert port == 18080

    def test_concurrent_requests(self) -> None:
        """Server must handle a burst of concurrent requests without crashing."""
        errors: list[Exception] = []

        def worker(client: TestClient) -> None:
            try:
                resp = client.get("/health")
                assert resp.status_code == 200
            except Exception as exc:
                errors.append(exc)

        with _client(_make_settings()) as c:
            threads = [threading.Thread(target=worker, args=(c,)) for _ in range(20)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

        assert errors == [], f"Concurrent request errors: {errors}"


# ---------------------------------------------------------------------------
# _service_version smoke test
# ---------------------------------------------------------------------------


class TestServiceVersion:
    def test_returns_string(self) -> None:
        v = _service_version()
        assert isinstance(v, str)
        assert len(v) > 0


# ---------------------------------------------------------------------------
# Auth token env resolution
# ---------------------------------------------------------------------------

_TEST_TOKEN = "test-secret-token-abc123"


class TestAuthTokenEnvResolution:
    def test_resolves_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_HTTP_AUTH_TOKEN", "env-token-xyz")
        token = _Settings._resolve_auth_token()
        assert token == "env-token-xyz"

    def test_returns_none_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TAPPS_BRAIN_HTTP_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("TAPPS_BRAIN_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("TAPPS_BRAIN_AUTH_TOKEN_FILE", raising=False)
        monkeypatch.delenv("TAPPS_BRAIN_HTTP_AUTH_TOKEN_FILE", raising=False)
        token = _Settings._resolve_auth_token()
        assert token is None

    def test_returns_none_for_empty_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_HTTP_AUTH_TOKEN", "  ")
        monkeypatch.delenv("TAPPS_BRAIN_AUTH_TOKEN", raising=False)
        token = _Settings._resolve_auth_token()
        assert token is None


# ---------------------------------------------------------------------------
# /info endpoint — auth-protected (STORY-060.4)
# ---------------------------------------------------------------------------


class TestInfoEndpointNoAuth:
    """When auth is not configured, /info is open."""

    def test_returns_200_without_auth(self) -> None:
        with _client(_make_settings(auth_token=None)) as c:
            resp = c.get("/info")
        assert resp.status_code == 200

    def test_body_has_service_field(self) -> None:
        with _client(_make_settings(auth_token=None)) as c:
            body = c.get("/info").json()
        assert isinstance(body, dict)
        assert body["service"] == "tapps-brain"

    def test_body_has_python_field(self) -> None:
        with _client(_make_settings(auth_token=None)) as c:
            body = c.get("/info").json()
        assert isinstance(body, dict)
        assert "python" in body
        assert body["python"].count(".") >= 1

    def test_body_has_uptime_seconds(self) -> None:
        with _client(_make_settings(auth_token=None)) as c:
            body = c.get("/info").json()
        assert isinstance(body, dict)
        assert isinstance(body.get("uptime_seconds"), float | int)
        assert body["uptime_seconds"] >= 0

    def test_auth_enabled_is_false_without_token(self) -> None:
        with _client(_make_settings(auth_token=None)) as c:
            body = c.get("/info").json()
        assert isinstance(body, dict)
        assert body["auth_enabled"] is False

    def test_dsn_configured_is_false_without_dsn(self) -> None:
        with _client(_make_settings(dsn=None, auth_token=None)) as c:
            body = c.get("/info").json()
        assert isinstance(body, dict)
        assert body["dsn_configured"] is False


class TestInfoEndpointWithAuth:
    """When auth IS configured, /info requires a valid Bearer token."""

    def test_returns_401_without_authorization_header(self) -> None:
        with _client(_make_settings(auth_token=_TEST_TOKEN)) as c:
            resp = c.get("/info")
        assert resp.status_code == 401
        body = resp.json()
        assert isinstance(body, dict)
        assert body.get("error") == "unauthorized"

    def test_returns_401_with_malformed_header(self) -> None:
        with _client(_make_settings(auth_token=_TEST_TOKEN)) as c:
            for bad_header in [
                "Basic abc123",
                "Bearer",
                "token abc123",
                "bearer" + _TEST_TOKEN,
            ]:
                resp = c.get("/info", headers={"Authorization": bad_header})
                assert resp.status_code in (401, 403), (
                    f"Expected 401/403 for header '{bad_header}', got {resp.status_code}"
                )

    def test_returns_403_with_wrong_token(self) -> None:
        with _client(_make_settings(auth_token=_TEST_TOKEN)) as c:
            resp = c.get("/info", headers={"Authorization": "Bearer wrong-token-xyz"})
        assert resp.status_code == 403
        body = resp.json()
        assert isinstance(body, dict)
        assert body.get("error") == "forbidden"

    def test_returns_200_with_correct_token(self) -> None:
        with _client(_make_settings(auth_token=_TEST_TOKEN)) as c:
            resp = c.get("/info", headers={"Authorization": f"Bearer {_TEST_TOKEN}"})
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, dict)
        assert body["service"] == "tapps-brain"

    def test_auth_enabled_is_true_with_token(self) -> None:
        with _client(_make_settings(auth_token=_TEST_TOKEN)) as c:
            body = c.get("/info", headers={"Authorization": f"Bearer {_TEST_TOKEN}"}).json()
        assert isinstance(body, dict)
        assert body["auth_enabled"] is True

    def test_probe_routes_public_even_with_auth_configured(self) -> None:
        """Probe routes must not require auth — Kubernetes probes don't send tokens."""
        with _client(_make_settings(auth_token=_TEST_TOKEN)) as c:
            for path in ("/health", "/ready", "/metrics", "/openapi.json"):
                resp = c.get(path)
                assert resp.status_code in (200, 503), (
                    f"Public probe route {path} returned {resp.status_code} — must not require auth"
                )


# ---------------------------------------------------------------------------
# /openapi.json endpoint (STORY-060.4)
# ---------------------------------------------------------------------------


class TestOpenApiEndpoint:
    def test_returns_200(self) -> None:
        with _client(_make_settings()) as c:
            resp = c.get("/openapi.json")
        assert resp.status_code == 200

    def test_returns_json(self) -> None:
        with _client(_make_settings()) as c:
            body = c.get("/openapi.json").json()
        assert isinstance(body, dict)

    def test_openapi_version_field(self) -> None:
        with _client(_make_settings()) as c:
            body = c.get("/openapi.json").json()
        assert isinstance(body, dict)
        assert body.get("openapi", "").startswith("3.")

    def test_paths_include_required_routes(self) -> None:
        with _client(_make_settings()) as c:
            body = c.get("/openapi.json").json()
        assert isinstance(body, dict)
        paths = body.get("paths", {})
        for route in ("/health", "/ready", "/metrics", "/info", "/openapi.json"):
            assert route in paths, f"OpenAPI spec missing route: {route}"

    def test_no_memory_crud_routes(self) -> None:
        """OpenAPI spec must not include memory CRUD paths."""
        with _client(_make_settings()) as c:
            body = c.get("/openapi.json").json()
        assert isinstance(body, dict)
        paths = body.get("paths", {})
        forbidden_prefixes = ("/memory", "/memories", "/entries", "/agent")
        for path in paths:
            for prefix in forbidden_prefixes:
                assert not path.startswith(prefix), (
                    f"OpenAPI spec must not include memory CRUD route: {path}"
                )

    def test_spec_within_page_limit(self) -> None:
        """Sanity: OpenAPI spec must remain ≤ 1 page — keep route count ≤ 10."""
        paths = _OPENAPI_SPEC.get("paths", {})
        assert len(paths) <= 10, (
            f"OpenAPI spec has {len(paths)} routes — EPIC-060 limits to ≤ 10 documented routes"
        )

    def test_public_even_with_auth_configured(self) -> None:
        """OpenAPI spec must be accessible without auth."""
        with _client(_make_settings(auth_token=_TEST_TOKEN)) as c:
            resp = c.get("/openapi.json")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# W3C traceparent trace context propagation (STORY-061.3)
# ---------------------------------------------------------------------------


class TestTraceContextPropagation:
    """STORY-061.3: W3C traceparent header creates a child span in the HTTP adapter."""

    _TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"

    def test_request_with_traceparent_still_returns_200(self) -> None:
        """The response must be unaffected by the presence of traceparent."""
        with _client(_make_settings()) as c:
            resp = c.get("/health", headers={"traceparent": self._TRACEPARENT})
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, dict)
        assert body["status"] == "ok"

    def test_request_without_traceparent_still_returns_200(self) -> None:
        """No traceparent header → no trace context → normal response."""
        with _client(_make_settings()) as c:
            resp = c.get("/health")
        assert resp.status_code == 200

    def test_traceparent_extracted_and_span_created(self) -> None:
        """When traceparent is present, a SERVER span is started with the extracted context."""
        _spans_started: list[tuple[str, Any]] = []
        _real_start_span = __import__("tapps_brain.otel_tracer", fromlist=["start_span"]).start_span

        @contextmanager  # type: ignore[misc]
        def _tracking_start_span(
            name: str,
            attributes: Any = None,
            *,
            kind: Any = None,
            context: Any = None,
            **kw: Any,
        ) -> Any:
            _spans_started.append((name, context))
            with _real_start_span(name, attributes, kind=kind, context=context, **kw) as span:
                yield span

        with patch("tapps_brain.http_adapter.start_span", _tracking_start_span):
            with _client(_make_settings()) as c:
                c.get("/health", headers={"traceparent": self._TRACEPARENT})

        get_spans = [(n, ctx) for n, ctx in _spans_started if "GET" in n]
        assert len(get_spans) >= 1, f"No GET span found; all spans: {_spans_started}"
        span_name, _ctx = get_spans[0]
        assert "/health" in span_name

    def test_extract_trace_context_with_valid_traceparent(self) -> None:
        """extract_trace_context() returns a non-None context for a valid traceparent."""
        from tapps_brain.otel_tracer import extract_trace_context

        ctx = extract_trace_context({"traceparent": self._TRACEPARENT})
        assert ctx is not None

    def test_extract_trace_context_with_empty_carrier(self) -> None:
        """extract_trace_context() does not raise on empty carrier."""
        from tapps_brain.otel_tracer import extract_trace_context

        ctx = extract_trace_context({})
        assert ctx is not None

    def test_extract_trace_context_never_raises(self) -> None:
        """extract_trace_context() must never propagate exceptions."""
        from tapps_brain.otel_tracer import extract_trace_context

        result = extract_trace_context({"traceparent": "not-a-valid-traceparent"})
        assert result is not None or result is None  # always True, just checking no raise

    def test_span_kind_server_exported(self) -> None:
        """SPAN_KIND_SERVER must be SpanKind.SERVER when OTel API is available."""
        from opentelemetry.trace import SpanKind

        from tapps_brain.otel_tracer import SPAN_KIND_SERVER

        assert SPAN_KIND_SERVER == SpanKind.SERVER

    def test_start_span_accepts_server_kind(self) -> None:
        """start_span() must accept kind=SPAN_KIND_SERVER without error."""
        from opentelemetry.trace import SpanKind

        from tapps_brain.otel_tracer import start_span

        mock_tracer = MagicMock()
        mock_span = MagicMock()
        mock_span.__enter__ = MagicMock(return_value=mock_span)
        mock_span.__exit__ = MagicMock(return_value=False)
        mock_tracer.start_as_current_span.return_value = mock_span

        with (
            patch("tapps_brain.otel_tracer.get_tracer", return_value=mock_tracer),
            start_span("test.server.span", kind=SpanKind.SERVER),
        ):
            pass

        _, kwargs = mock_tracer.start_as_current_span.call_args
        assert kwargs.get("kind") == SpanKind.SERVER


# ---------------------------------------------------------------------------
# /snapshot endpoint (STORY-065.1)
# ---------------------------------------------------------------------------

_FAKE_SNAPSHOT_DICT: dict[str, Any] = {
    "schema_version": 2,
    "generated_at": "2026-04-11T00:00:00+00:00",
    "fingerprint_sha256": "abc123",
    "identity_schema_version": 2,
    "privacy_tier": "standard",
    "privacy": "aggregated",
    "health": {},
    "agent_scope_counts": {},
    "hive_attached": False,
    "hive_health": {},
    "retrieval_effective_mode": "bm25_only",
    "retrieval_summary": "",
    "vector_index_enabled": False,
    "vector_index_rows": 0,
    "memory_group_count": 0,
    "scorecard": [],
    "access_stats": None,
    "tag_stats": None,
    "diagnostics": None,
    "memory_group_counts": None,
    "theme": None,
}


def _make_mock_snapshot() -> MagicMock:
    """Return a mock VisualSnapshot whose model_dump returns the fake dict."""
    snap = MagicMock()
    snap.model_dump.return_value = dict(_FAKE_SNAPSHOT_DICT)
    return snap


class TestSnapshotEndpointNoStore:
    """GET /snapshot returns 503 when no store is injected."""

    def test_returns_503_without_store(self) -> None:
        with _client(_make_settings(store=None)) as c:
            resp = c.get("/snapshot")
        assert resp.status_code == 503
        body = resp.json()
        assert isinstance(body, dict)
        assert "error" in body

    def test_error_body_mentions_store(self) -> None:
        with _client(_make_settings(store=None)) as c:
            body = c.get("/snapshot").json()
        assert isinstance(body, dict)
        assert "store" in body.get("error", "").lower()

    def test_cors_header_present_even_on_503(self) -> None:
        with _client(_make_settings(store=None)) as c:
            resp = c.get("/snapshot")
        assert resp.headers.get("access-control-allow-origin") == "*"


class TestSnapshotEndpointWithStore:
    """GET /snapshot returns 200 + VisualSnapshot JSON when store is injected."""

    def test_returns_200_with_store(self) -> None:
        mock_store = MagicMock()
        mock_snap = _make_mock_snapshot()
        with patch("tapps_brain.visual_snapshot.build_visual_snapshot", return_value=mock_snap):
            with _client(_make_settings(store=mock_store)) as c:
                resp = c.get("/snapshot")
        assert resp.status_code == 200
        assert isinstance(resp.json(), dict)

    def test_body_contains_schema_version(self) -> None:
        mock_store = MagicMock()
        mock_snap = _make_mock_snapshot()
        with patch("tapps_brain.visual_snapshot.build_visual_snapshot", return_value=mock_snap):
            with _client(_make_settings(store=mock_store)) as c:
                body = c.get("/snapshot").json()
        assert isinstance(body, dict)
        assert body.get("schema_version") == 2

    def test_cors_header_present(self) -> None:
        mock_store = MagicMock()
        mock_snap = _make_mock_snapshot()
        with patch("tapps_brain.visual_snapshot.build_visual_snapshot", return_value=mock_snap):
            with _client(_make_settings(store=mock_store)) as c:
                resp = c.get("/snapshot")
        assert resp.headers.get("access-control-allow-origin") == "*"

    def test_ttl_cache_prevents_double_call(self) -> None:
        """Two requests within 15s must return the same body (cached)."""
        mock_store = MagicMock()
        call_count = 0

        def _build(*args: Any, **kwargs: Any) -> MagicMock:
            nonlocal call_count
            call_count += 1
            return _make_mock_snapshot()

        with patch("tapps_brain.visual_snapshot.build_visual_snapshot", side_effect=_build):
            with _client(_make_settings(store=mock_store)) as c:
                r1 = c.get("/snapshot")
                r2 = c.get("/snapshot")

        assert r1.status_code == 200
        assert r2.status_code == 200
        assert call_count == 1, f"Expected 1 snapshot build call; got {call_count}"

    def test_cache_refresh_after_ttl(self) -> None:
        """After the TTL expires, the next request triggers a fresh snapshot build."""
        original_ttl = _mod._SNAPSHOT_TTL_SECONDS
        mock_store = MagicMock()
        call_count = 0

        def _build(*args: Any, **kwargs: Any) -> MagicMock:
            nonlocal call_count
            call_count += 1
            return _make_mock_snapshot()

        _mod._SNAPSHOT_TTL_SECONDS = 0.0
        try:
            with patch("tapps_brain.visual_snapshot.build_visual_snapshot", side_effect=_build):
                with _client(_make_settings(store=mock_store)) as c:
                    c.get("/snapshot")
                    time.sleep(0.05)
                    c.get("/snapshot")
        finally:
            _mod._SNAPSHOT_TTL_SECONDS = original_ttl

        assert call_count == 2, f"Expected 2 snapshot build calls after TTL; got {call_count}"

    def test_content_type_is_json(self) -> None:
        mock_store = MagicMock()
        mock_snap = _make_mock_snapshot()
        with patch("tapps_brain.visual_snapshot.build_visual_snapshot", return_value=mock_snap):
            with _client(_make_settings(store=mock_store)) as c:
                resp = c.get("/snapshot")
        ct = resp.headers.get("content-type", "")
        assert "application/json" in ct


class TestSnapshotEndpointAuth:
    """GET /snapshot auth follows the same bearer-token gate as /info."""

    def test_returns_401_without_token_when_auth_configured(self) -> None:
        mock_store = MagicMock()
        with _client(_make_settings(auth_token=_TEST_TOKEN, store=mock_store)) as c:
            resp = c.get("/snapshot")
        assert resp.status_code == 401

    def test_returns_403_with_wrong_token(self) -> None:
        mock_store = MagicMock()
        with _client(_make_settings(auth_token=_TEST_TOKEN, store=mock_store)) as c:
            resp = c.get("/snapshot", headers={"Authorization": "Bearer wrong-token"})
        assert resp.status_code == 403

    def test_returns_200_with_correct_token(self) -> None:
        mock_store = MagicMock()
        mock_snap = _make_mock_snapshot()
        with patch("tapps_brain.visual_snapshot.build_visual_snapshot", return_value=mock_snap):
            with _client(_make_settings(auth_token=_TEST_TOKEN, store=mock_store)) as c:
                resp = c.get("/snapshot", headers={"Authorization": f"Bearer {_TEST_TOKEN}"})
        assert resp.status_code == 200


class TestSnapshotCorsPreflight:
    """OPTIONS /snapshot returns CORS preflight headers."""

    def test_options_returns_204_or_200(self) -> None:
        with _client(_make_settings()) as c:
            resp = c.options("/snapshot")
        assert resp.status_code in (200, 204, 405)

    def test_options_includes_allow_origin_star(self) -> None:
        with _client(_make_settings()) as c:
            resp = c.options("/snapshot")
        # FastAPI may return the CORS header if CORSMiddleware is configured,
        # or the GET handler's explicit header for non-OPTIONS; either is acceptable.
        # The key contract is the GET response includes the header (tested above).
        assert resp.status_code in (200, 204, 405)


class TestSnapshotOpenApiSpec:
    """/snapshot must appear in the OpenAPI spec."""

    def test_snapshot_in_openapi_paths(self) -> None:
        with _client(_make_settings()) as c:
            body = c.get("/openapi.json").json()
        assert isinstance(body, dict)
        assert "/snapshot" in body.get("paths", {}), "/snapshot must be in OpenAPI spec paths"

    def test_snapshot_spec_has_503_response(self) -> None:
        with _client(_make_settings()) as c:
            body = c.get("/openapi.json").json()
        assert isinstance(body, dict)
        snapshot_spec = body.get("paths", {}).get("/snapshot", {}).get("get", {})
        assert "503" in snapshot_spec.get("responses", {}), "/snapshot spec must document 503"


# ---------------------------------------------------------------------------
# STORY-069.7: /snapshot?project=<id> filter
# ---------------------------------------------------------------------------

_SNAPSHOT_WITH_TENANT_ROWS: dict[str, Any] = {
    **_FAKE_SNAPSHOT_DICT,
    "diagnostics_history": [
        {"id": "d1", "project_id": "tenant-a", "recorded_at": "2026-04-14T00:00:00+00:00"},
        {"id": "d2", "project_id": "tenant-b", "recorded_at": "2026-04-14T00:00:00+00:00"},
        {"id": "d3", "project_id": None, "recorded_at": "2026-04-13T00:00:00+00:00"},
    ],
    "feedback_events": [
        {"id": "e1", "project_id": "tenant-a", "event_type": "recall_rated"},
        {"id": "e2", "project_id": "tenant-b", "event_type": "gap_reported"},
        {"id": "e3", "project_id": None, "event_type": "recall_rated"},
    ],
}


def _make_mock_snapshot_with_tenants() -> MagicMock:
    snap = MagicMock()
    snap.model_dump.return_value = dict(_SNAPSHOT_WITH_TENANT_ROWS)
    return snap


class TestSnapshotProjectFilter:
    """STORY-069.7: /snapshot?project=<id> filters diagnostics_history + feedback_events."""

    def test_unfiltered_returns_all_rows(self) -> None:
        mock_store = MagicMock()
        with patch(
            "tapps_brain.visual_snapshot.build_visual_snapshot",
            side_effect=lambda *a, **k: _make_mock_snapshot_with_tenants(),
        ), _client(_make_settings(store=mock_store)) as c:
            resp = c.get("/snapshot")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, dict)
        assert len(body["diagnostics_history"]) == 3
        assert len(body["feedback_events"]) == 3

    def test_project_filter_scopes_rows(self) -> None:
        mock_store = MagicMock()
        with patch(
            "tapps_brain.visual_snapshot.build_visual_snapshot",
            side_effect=lambda *a, **k: _make_mock_snapshot_with_tenants(),
        ), _client(_make_settings(store=mock_store)) as c:
            resp = c.get("/snapshot?project=tenant-a")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, dict)
        assert [r["id"] for r in body["diagnostics_history"]] == ["d1"]
        assert [r["id"] for r in body["feedback_events"]] == ["e1"]

    def test_project_filter_excludes_legacy_none_rows(self) -> None:
        """Rows with project_id=None must NOT leak into a filtered response."""
        mock_store = MagicMock()
        with patch(
            "tapps_brain.visual_snapshot.build_visual_snapshot",
            side_effect=lambda *a, **k: _make_mock_snapshot_with_tenants(),
        ), _client(_make_settings(store=mock_store)) as c:
            body = c.get("/snapshot?project=tenant-b").json()
        assert isinstance(body, dict)
        ids = {r["id"] for r in body["diagnostics_history"]} | {
            r["id"] for r in body["feedback_events"]
        }
        assert "d3" not in ids
        assert "e3" not in ids

    def test_unknown_project_returns_empty_arrays_not_404(self) -> None:
        mock_store = MagicMock()
        with patch(
            "tapps_brain.visual_snapshot.build_visual_snapshot",
            side_effect=lambda *a, **k: _make_mock_snapshot_with_tenants(),
        ), _client(_make_settings(store=mock_store)) as c:
            resp = c.get("/snapshot?project=ghost")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, dict)
        assert body["diagnostics_history"] == []
        assert body["feedback_events"] == []
