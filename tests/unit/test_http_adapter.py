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

import http.client
import json
import socket
import threading
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tapps_brain.http_adapter import _OPENAPI_SPEC, HttpAdapter, _probe_db, _service_version

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _free_port() -> int:
    """Find an available TCP port on localhost."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(port: int, path: str) -> tuple[int, dict[str, Any] | str]:
    """Issue a GET request and return (status_code, parsed_body)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8")
        content_type = resp.getheader("Content-Type", "")
        if "application/json" in content_type:
            return resp.status, json.loads(raw)
        return resp.status, raw
    finally:
        conn.close()


def _get_with_headers(
    port: int, path: str, headers: dict[str, str]
) -> tuple[int, dict[str, Any] | str]:
    """Issue a GET request with custom headers and return (status_code, parsed_body)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8")
        content_type = resp.getheader("Content-Type", "")
        if "application/json" in content_type:
            return resp.status, json.loads(raw)
        return resp.status, raw
    finally:
        conn.close()


def _get_full(
    port: int, path: str, headers: dict[str, str] | None = None
) -> tuple[int, dict[str, Any] | str, dict[str, str]]:
    """Issue a GET request and return (status_code, parsed_body, response_headers)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET", path, headers=headers or {})
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8")
        resp_headers = {k.lower(): v for k, v in resp.getheaders()}
        content_type = resp.getheader("Content-Type", "")
        if "application/json" in content_type:
            return resp.status, json.loads(raw), resp_headers
        return resp.status, raw, resp_headers
    finally:
        conn.close()


def _options(port: int, path: str) -> tuple[int, dict[str, str]]:
    """Issue an OPTIONS request and return (status_code, response_headers)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("OPTIONS", path)
        resp = conn.getresponse()
        resp.read()  # drain
        resp_headers = {k.lower(): v for k, v in resp.getheaders()}
        return resp.status, resp_headers
    finally:
        conn.close()


def _wait_for_server(port: int, timeout: float = 3.0) -> None:
    """Poll until the server accepts connections."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"HTTP adapter did not start on port {port} within {timeout}s")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def adapter_no_dsn():
    """Start an HttpAdapter without a DSN (no DB probing)."""
    port = _free_port()
    adapter = HttpAdapter(host="127.0.0.1", port=port, dsn=None)
    adapter.start()
    _wait_for_server(port)
    yield adapter
    adapter.stop()


@pytest.fixture()
def adapter_bad_dsn():
    """Start an HttpAdapter with a DSN that will fail to connect."""
    port = _free_port()
    # Use an unreachable Postgres DSN — psycopg import may not be available, which
    # also triggers the "degraded" path via exception handling in _probe_db.
    adapter = HttpAdapter(
        host="127.0.0.1",
        port=port,
        dsn="postgres://invalid_host_that_does_not_exist:5432/nodb",
    )
    adapter.start()
    _wait_for_server(port)
    yield adapter
    adapter.stop()


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
    def test_returns_200(self, adapter_no_dsn: HttpAdapter) -> None:
        status, _body = _get(adapter_no_dsn.address[1], "/health")
        assert status == 200

    def test_returns_json_with_ok_status(self, adapter_no_dsn: HttpAdapter) -> None:
        _status, body = _get(adapter_no_dsn.address[1], "/health")
        assert isinstance(body, dict)
        assert body["status"] == "ok"

    def test_returns_service_name(self, adapter_no_dsn: HttpAdapter) -> None:
        _, body = _get(adapter_no_dsn.address[1], "/health")
        assert isinstance(body, dict)
        assert body["service"] == "tapps-brain"

    def test_returns_version_field(self, adapter_no_dsn: HttpAdapter) -> None:
        _, body = _get(adapter_no_dsn.address[1], "/health")
        assert isinstance(body, dict)
        assert "version" in body

    def test_root_path_also_returns_200(self, adapter_no_dsn: HttpAdapter) -> None:
        status, body = _get(adapter_no_dsn.address[1], "/")
        assert status == 200
        assert isinstance(body, dict)
        assert body["status"] == "ok"

    def test_liveness_returns_200_even_with_db_down(self, adapter_bad_dsn: HttpAdapter) -> None:
        """STORY-061.4: /health must return 200 even when Postgres is unreachable.

        Kubernetes livenessProbe uses /health. If the DB is down, the pod
        should NOT be restarted — it's the DB that's unhealthy, not the
        process. Only /ready (readinessProbe) should return 503 in that case.
        """
        status, body = _get(adapter_bad_dsn.address[1], "/health")
        assert status == 200, (
            f"/health returned {status} with bad DSN — liveness must never call the DB"
        )
        assert isinstance(body, dict)
        assert body["status"] == "ok"


# ---------------------------------------------------------------------------
# /ready endpoint — no DSN (degraded)
# ---------------------------------------------------------------------------


class TestReadyEndpointNoDsn:
    def test_returns_503_without_dsn(self, adapter_no_dsn: HttpAdapter) -> None:
        status, _body = _get(adapter_no_dsn.address[1], "/ready")
        assert status == 503

    def test_body_status_is_degraded(self, adapter_no_dsn: HttpAdapter) -> None:
        _, body = _get(adapter_no_dsn.address[1], "/ready")
        assert isinstance(body, dict)
        assert body["status"] == "degraded"

    def test_detail_mentions_dsn(self, adapter_no_dsn: HttpAdapter) -> None:
        _, body = _get(adapter_no_dsn.address[1], "/ready")
        assert isinstance(body, dict)
        assert "DSN" in body.get("detail", "") or "dsn" in body.get("detail", "").lower()


# ---------------------------------------------------------------------------
# /ready endpoint — bad DSN (DB-down scenario)
# ---------------------------------------------------------------------------


class TestReadyEndpointDbDown:
    def test_returns_503_on_db_failure(self, adapter_bad_dsn: HttpAdapter) -> None:
        status, _body = _get(adapter_bad_dsn.address[1], "/ready")
        assert status == 503

    def test_body_status_is_degraded(self, adapter_bad_dsn: HttpAdapter) -> None:
        _, body = _get(adapter_bad_dsn.address[1], "/ready")
        assert isinstance(body, dict)
        assert body["status"] == "degraded"

    def test_dsn_not_leaked_in_body(self, adapter_bad_dsn: HttpAdapter) -> None:
        """DSN (host/credentials) must not appear in any response field."""
        _, body = _get(adapter_bad_dsn.address[1], "/ready")
        body_str = json.dumps(body)
        # The DSN host string should not appear verbatim
        assert "invalid_host_that_does_not_exist" not in body_str


# ---------------------------------------------------------------------------
# /ready endpoint — DB healthy (mocked)
# ---------------------------------------------------------------------------


class TestReadyEndpointDbHealthy:
    def test_returns_200_when_db_ready(self) -> None:
        mock_status = MagicMock()
        mock_status.current_version = 5
        mock_status.pending_migrations = []
        port = _free_port()
        with patch(
            "tapps_brain.postgres_migrations.get_hive_schema_status", return_value=mock_status
        ):
            adapter = HttpAdapter(
                host="127.0.0.1",
                port=port,
                dsn="postgres://mockhost/testdb",
            )
            with adapter:
                _wait_for_server(port)
                status, body = _get(port, "/ready")
        assert status == 200
        assert isinstance(body, dict)
        assert body["status"] == "ready"

    def test_migration_version_in_body(self) -> None:
        mock_status = MagicMock()
        mock_status.current_version = 7
        mock_status.pending_migrations = []
        port = _free_port()
        with patch(
            "tapps_brain.postgres_migrations.get_hive_schema_status", return_value=mock_status
        ):
            adapter = HttpAdapter(
                host="127.0.0.1",
                port=port,
                dsn="postgres://mockhost/testdb",
            )
            with adapter:
                _wait_for_server(port)
                _, body = _get(port, "/ready")
        assert isinstance(body, dict)
        assert body["migration_version"] == 7


# ---------------------------------------------------------------------------
# /metrics endpoint
# ---------------------------------------------------------------------------


class TestMetricsEndpoint:
    def test_returns_200(self, adapter_no_dsn: HttpAdapter) -> None:
        status, _ = _get(adapter_no_dsn.address[1], "/metrics")
        assert status == 200

    def test_content_type_is_prometheus(self, adapter_no_dsn: HttpAdapter) -> None:
        port = adapter_no_dsn.address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            conn.request("GET", "/metrics")
            resp = conn.getresponse()
            ct = resp.getheader("Content-Type", "")
        finally:
            conn.close()
        assert "text/plain" in ct

    def test_contains_process_uptime_gauge(self, adapter_no_dsn: HttpAdapter) -> None:
        _, body = _get(adapter_no_dsn.address[1], "/metrics")
        assert isinstance(body, str)
        assert "tapps_brain_process_uptime_seconds" in body

    def test_contains_db_ready_gauge(self, adapter_no_dsn: HttpAdapter) -> None:
        _, body = _get(adapter_no_dsn.address[1], "/metrics")
        assert isinstance(body, str)
        assert "tapps_brain_db_ready" in body

    def test_db_ready_is_zero_without_dsn(self, adapter_no_dsn: HttpAdapter) -> None:
        _, body = _get(adapter_no_dsn.address[1], "/metrics")
        assert isinstance(body, str)
        assert "tapps_brain_db_ready 0.0" in body

    def test_no_high_cardinality_labels(self, adapter_no_dsn: HttpAdapter) -> None:
        """Metrics must not include query strings, keys, or agent IDs."""
        _, body = _get(adapter_no_dsn.address[1], "/metrics")
        assert isinstance(body, str)
        # No curly-brace label sets should appear (Prometheus label syntax)
        assert "{" not in body


# ---------------------------------------------------------------------------
# 404 for unknown routes
# ---------------------------------------------------------------------------


class TestUnknownRoute:
    def test_unknown_path_returns_404(self, adapter_no_dsn: HttpAdapter) -> None:
        status, _body = _get(adapter_no_dsn.address[1], "/nonexistent")
        assert status == 404

    def test_404_body_contains_error(self, adapter_no_dsn: HttpAdapter) -> None:
        _, body = _get(adapter_no_dsn.address[1], "/nonexistent")
        assert isinstance(body, dict)
        assert "error" in body


# ---------------------------------------------------------------------------
# Lifecycle / context manager
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_context_manager_starts_and_stops(self) -> None:
        port = _free_port()
        adapter = HttpAdapter(host="127.0.0.1", port=port, dsn=None)
        with adapter:
            _wait_for_server(port)
            assert adapter.is_running
            status, _ = _get(port, "/health")
            assert status == 200
        assert not adapter.is_running

    def test_double_start_is_idempotent(self) -> None:
        port = _free_port()
        adapter = HttpAdapter(host="127.0.0.1", port=port, dsn=None)
        try:
            adapter.start()
            _wait_for_server(port)
            server_before = adapter._server
            adapter.start()  # second start — should be a no-op
            assert adapter._server is server_before
        finally:
            adapter.stop()

    def test_stop_without_start_is_safe(self) -> None:
        adapter = HttpAdapter(host="127.0.0.1", port=_free_port(), dsn=None)
        adapter.stop()  # must not raise

    def test_address_property(self) -> None:
        port = _free_port()
        adapter = HttpAdapter(host="127.0.0.1", port=port, dsn=None)
        with adapter:
            _wait_for_server(port)
            host, actual_port = adapter.address
            assert host == "127.0.0.1"
            assert actual_port == port

    def test_concurrent_requests(self) -> None:
        """Server must handle a burst of concurrent requests without crashing."""
        port = _free_port()
        adapter = HttpAdapter(host="127.0.0.1", port=port, dsn=None)
        errors: list[Exception] = []

        def worker() -> None:
            try:
                status, _ = _get(port, "/health")
                assert status == 200
            except Exception as exc:
                errors.append(exc)

        with adapter:
            _wait_for_server(port)
            threads = [threading.Thread(target=worker) for _ in range(20)]
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
# Fixtures for auth tests (STORY-060.4)
# ---------------------------------------------------------------------------

_TEST_TOKEN = "test-secret-token-abc123"


@pytest.fixture()
def adapter_with_auth():
    """Start an HttpAdapter with a fixed auth token."""
    port = _free_port()
    adapter = HttpAdapter(host="127.0.0.1", port=port, dsn=None, auth_token=_TEST_TOKEN)
    adapter.start()
    _wait_for_server(port)
    yield adapter
    adapter.stop()


@pytest.fixture()
def adapter_no_auth():
    """Start an HttpAdapter without auth (same as adapter_no_dsn but named for clarity)."""
    port = _free_port()
    adapter = HttpAdapter(host="127.0.0.1", port=port, dsn=None, auth_token=None)
    adapter.start()
    _wait_for_server(port)
    yield adapter
    adapter.stop()


# ---------------------------------------------------------------------------
# /info endpoint — auth-protected (STORY-060.4)
# ---------------------------------------------------------------------------


class TestInfoEndpointNoAuth:
    """When auth is not configured, /info is open."""

    def test_returns_200_without_auth(self, adapter_no_auth: HttpAdapter) -> None:
        status, _body = _get(adapter_no_auth.address[1], "/info")
        assert status == 200

    def test_body_has_service_field(self, adapter_no_auth: HttpAdapter) -> None:
        _, body = _get(adapter_no_auth.address[1], "/info")
        assert isinstance(body, dict)
        assert body["service"] == "tapps-brain"

    def test_body_has_python_field(self, adapter_no_auth: HttpAdapter) -> None:
        _, body = _get(adapter_no_auth.address[1], "/info")
        assert isinstance(body, dict)
        assert "python" in body
        # Should look like "3.12.x"
        assert body["python"].count(".") >= 1

    def test_body_has_uptime_seconds(self, adapter_no_auth: HttpAdapter) -> None:
        _, body = _get(adapter_no_auth.address[1], "/info")
        assert isinstance(body, dict)
        assert isinstance(body.get("uptime_seconds"), float | int)
        assert body["uptime_seconds"] >= 0

    def test_auth_enabled_is_false_without_token(self, adapter_no_auth: HttpAdapter) -> None:
        _, body = _get(adapter_no_auth.address[1], "/info")
        assert isinstance(body, dict)
        assert body["auth_enabled"] is False

    def test_dsn_configured_is_false_without_dsn(self, adapter_no_auth: HttpAdapter) -> None:
        _, body = _get(adapter_no_auth.address[1], "/info")
        assert isinstance(body, dict)
        assert body["dsn_configured"] is False


class TestInfoEndpointWithAuth:
    """When auth IS configured, /info requires a valid Bearer token."""

    def test_returns_401_without_authorization_header(self, adapter_with_auth: HttpAdapter) -> None:
        """Fuzz: missing Authorization header → 401."""
        status, body = _get(adapter_with_auth.address[1], "/info")
        assert status == 401
        assert isinstance(body, dict)
        assert body.get("error") == "unauthorized"

    def test_returns_401_with_malformed_header(self, adapter_with_auth: HttpAdapter) -> None:
        """Fuzz: Authorization header present but not 'Bearer <token>' form → 401."""
        port = adapter_with_auth.address[1]
        for bad_header in [
            "Basic abc123",
            "Bearer",  # missing token
            "token abc123",
            "bearer" + _TEST_TOKEN,  # no space
        ]:
            status, _body = _get_with_headers(port, "/info", {"Authorization": bad_header})
            assert status in (401, 403), f"Expected 401/403 for header '{bad_header}', got {status}"

    def test_returns_403_with_wrong_token(self, adapter_with_auth: HttpAdapter) -> None:
        """Fuzz: wrong token → 403."""
        port = adapter_with_auth.address[1]
        status, body = _get_with_headers(port, "/info", {"Authorization": "Bearer wrong-token-xyz"})
        assert status == 403
        assert isinstance(body, dict)
        assert body.get("error") == "forbidden"

    def test_returns_200_with_correct_token(self, adapter_with_auth: HttpAdapter) -> None:
        """Correct token → 200."""
        port = adapter_with_auth.address[1]
        status, body = _get_with_headers(port, "/info", {"Authorization": f"Bearer {_TEST_TOKEN}"})
        assert status == 200
        assert isinstance(body, dict)
        assert body["service"] == "tapps-brain"

    def test_auth_enabled_is_true_with_token(self, adapter_with_auth: HttpAdapter) -> None:
        port = adapter_with_auth.address[1]
        _, body = _get_with_headers(port, "/info", {"Authorization": f"Bearer {_TEST_TOKEN}"})
        assert isinstance(body, dict)
        assert body["auth_enabled"] is True

    def test_probe_routes_public_even_with_auth_configured(
        self, adapter_with_auth: HttpAdapter
    ) -> None:
        """Probe routes must not require auth — Kubernetes probes don't send tokens."""
        port = adapter_with_auth.address[1]
        for path in ("/health", "/ready", "/metrics", "/openapi.json"):
            status, _ = _get(port, path)
            assert status in (200, 503), (
                f"Public probe route {path} returned {status} — must not require auth"
            )


# ---------------------------------------------------------------------------
# /openapi.json endpoint (STORY-060.4)
# ---------------------------------------------------------------------------


class TestOpenApiEndpoint:
    def test_returns_200(self, adapter_no_dsn: HttpAdapter) -> None:
        status, _ = _get(adapter_no_dsn.address[1], "/openapi.json")
        assert status == 200

    def test_returns_json(self, adapter_no_dsn: HttpAdapter) -> None:
        _, body = _get(adapter_no_dsn.address[1], "/openapi.json")
        assert isinstance(body, dict)

    def test_openapi_version_field(self, adapter_no_dsn: HttpAdapter) -> None:
        _, body = _get(adapter_no_dsn.address[1], "/openapi.json")
        assert isinstance(body, dict)
        assert body.get("openapi", "").startswith("3.")

    def test_paths_include_required_routes(self, adapter_no_dsn: HttpAdapter) -> None:
        _, body = _get(adapter_no_dsn.address[1], "/openapi.json")
        assert isinstance(body, dict)
        paths = body.get("paths", {})
        for route in ("/health", "/ready", "/metrics", "/info", "/openapi.json"):
            assert route in paths, f"OpenAPI spec missing route: {route}"

    def test_no_memory_crud_routes(self, adapter_no_dsn: HttpAdapter) -> None:
        """OpenAPI spec must not include memory CRUD paths."""
        _, body = _get(adapter_no_dsn.address[1], "/openapi.json")
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

    def test_public_even_with_auth_configured(self, adapter_with_auth: HttpAdapter) -> None:
        """OpenAPI spec must be accessible without auth."""
        port = adapter_with_auth.address[1]
        status, _ = _get(port, "/openapi.json")
        assert status == 200


# ---------------------------------------------------------------------------
# Auth env-var resolution
# ---------------------------------------------------------------------------


class TestAuthTokenEnvResolution:
    def test_resolves_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_HTTP_AUTH_TOKEN", "env-token-xyz")
        token = HttpAdapter._resolve_auth_token_from_env()
        assert token == "env-token-xyz"

    def test_returns_none_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TAPPS_BRAIN_HTTP_AUTH_TOKEN", raising=False)
        token = HttpAdapter._resolve_auth_token_from_env()
        assert token is None

    def test_returns_none_for_empty_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_HTTP_AUTH_TOKEN", "  ")
        token = HttpAdapter._resolve_auth_token_from_env()
        assert token is None


# ---------------------------------------------------------------------------
# W3C traceparent trace context propagation (STORY-061.3)
# ---------------------------------------------------------------------------


class TestTraceContextPropagation:
    """STORY-061.3: W3C traceparent header creates a child span in the HTTP adapter."""

    # A valid W3C traceparent: version=00, trace-id (32 hex), span-id (16 hex), flags=01
    _TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"

    def test_request_with_traceparent_still_returns_200(self, adapter_no_dsn: HttpAdapter) -> None:
        """The response must be unaffected by the presence of traceparent."""
        port = adapter_no_dsn.address[1]
        status, body = _get_with_headers(port, "/health", {"traceparent": self._TRACEPARENT})
        assert status == 200
        assert isinstance(body, dict)
        assert body["status"] == "ok"

    def test_request_without_traceparent_still_returns_200(
        self, adapter_no_dsn: HttpAdapter
    ) -> None:
        """No traceparent header → no trace context → normal response."""
        port = adapter_no_dsn.address[1]
        status, _body = _get(port, "/health")
        assert status == 200

    def test_traceparent_extracted_and_span_created(self, adapter_no_dsn: HttpAdapter) -> None:
        """When traceparent is present, a SERVER span is started with the extracted context."""
        from unittest.mock import patch

        _spans_started: list[tuple[str, Any]] = []

        _real_start_span = __import__("tapps_brain.otel_tracer", fromlist=["start_span"]).start_span
        from contextlib import contextmanager

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

        port = adapter_no_dsn.address[1]
        with patch("tapps_brain.http_adapter.start_span", _tracking_start_span):
            # Send a fresh request after patching; the server is already running
            # so we patch the module-level name
            _get_with_headers(port, "/health", {"traceparent": self._TRACEPARENT})

        # The SERVER span for GET /health must have been started
        get_spans = [(n, ctx) for n, ctx in _spans_started if "GET" in n]
        assert len(get_spans) >= 1, f"No GET span found; all spans: {_spans_started}"
        span_name, _ctx = get_spans[0]
        assert "/health" in span_name

    def test_extract_trace_context_with_valid_traceparent(self) -> None:
        """extract_trace_context() returns a non-None context for a valid traceparent."""
        from tapps_brain.otel_tracer import extract_trace_context

        ctx = extract_trace_context({"traceparent": self._TRACEPARENT})
        # When the OTel API is available, we always get a context object back
        assert ctx is not None

    def test_extract_trace_context_with_empty_carrier(self) -> None:
        """extract_trace_context() does not raise on empty carrier."""
        from tapps_brain.otel_tracer import extract_trace_context

        ctx = extract_trace_context({})
        # Empty carrier → no remote parent, but still a valid (empty) context
        assert ctx is not None  # OTel API returns an empty context, not None

    def test_extract_trace_context_never_raises(self) -> None:
        """extract_trace_context() must never propagate exceptions."""
        from tapps_brain.otel_tracer import extract_trace_context

        # Malformed traceparent — should not raise
        result = extract_trace_context({"traceparent": "not-a-valid-traceparent"})
        # Result is either None (no API) or a context object — never an exception
        assert result is not None or result is None  # always True, just checking no raise

    def test_span_kind_server_exported(self) -> None:
        """SPAN_KIND_SERVER must be SpanKind.SERVER when OTel API is available."""
        from opentelemetry.trace import SpanKind

        from tapps_brain.otel_tracer import SPAN_KIND_SERVER

        assert SPAN_KIND_SERVER == SpanKind.SERVER

    def test_start_span_accepts_server_kind(self) -> None:
        """start_span() must accept kind=SPAN_KIND_SERVER without error."""
        from unittest.mock import MagicMock

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

# Minimal VisualSnapshot-shaped dict returned by the mock
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
        port = _free_port()
        adapter = HttpAdapter(host="127.0.0.1", port=port, dsn=None, store=None)
        with adapter:
            _wait_for_server(port)
            status, body = _get(port, "/snapshot")
        assert status == 503
        assert isinstance(body, dict)
        assert "error" in body

    def test_error_body_mentions_store(self) -> None:
        port = _free_port()
        adapter = HttpAdapter(host="127.0.0.1", port=port, dsn=None, store=None)
        with adapter:
            _wait_for_server(port)
            _, body = _get(port, "/snapshot")
        assert isinstance(body, dict)
        assert "store" in body.get("error", "").lower()

    def test_cors_header_present_even_on_503(self) -> None:
        port = _free_port()
        adapter = HttpAdapter(host="127.0.0.1", port=port, dsn=None, store=None)
        with adapter:
            _wait_for_server(port)
            _, _, resp_headers = _get_full(port, "/snapshot")
        assert resp_headers.get("access-control-allow-origin") == "*"


class TestSnapshotEndpointWithStore:
    """GET /snapshot returns 200 + VisualSnapshot JSON when store is injected."""

    def test_returns_200_with_store(self) -> None:
        port = _free_port()
        mock_store = MagicMock()
        mock_snap = _make_mock_snapshot()
        with patch("tapps_brain.visual_snapshot.build_visual_snapshot", return_value=mock_snap):
            adapter = HttpAdapter(host="127.0.0.1", port=port, dsn=None, store=mock_store)
            with adapter:
                _wait_for_server(port)
                status, body = _get(port, "/snapshot")
        assert status == 200
        assert isinstance(body, dict)

    def test_body_contains_schema_version(self) -> None:
        port = _free_port()
        mock_store = MagicMock()
        mock_snap = _make_mock_snapshot()
        with patch("tapps_brain.visual_snapshot.build_visual_snapshot", return_value=mock_snap):
            adapter = HttpAdapter(host="127.0.0.1", port=port, dsn=None, store=mock_store)
            with adapter:
                _wait_for_server(port)
                _, body = _get(port, "/snapshot")
        assert isinstance(body, dict)
        assert body.get("schema_version") == 2

    def test_cors_header_present(self) -> None:
        port = _free_port()
        mock_store = MagicMock()
        mock_snap = _make_mock_snapshot()
        with patch("tapps_brain.visual_snapshot.build_visual_snapshot", return_value=mock_snap):
            adapter = HttpAdapter(host="127.0.0.1", port=port, dsn=None, store=mock_store)
            with adapter:
                _wait_for_server(port)
                _, _, resp_headers = _get_full(port, "/snapshot")
        assert resp_headers.get("access-control-allow-origin") == "*"

    def test_ttl_cache_prevents_double_call(self) -> None:
        """Two requests within 15s must return the same body (cached)."""
        port = _free_port()
        mock_store = MagicMock()
        call_count = 0

        def _build(*args: Any, **kwargs: Any) -> MagicMock:
            nonlocal call_count
            call_count += 1
            return _make_mock_snapshot()

        with patch("tapps_brain.visual_snapshot.build_visual_snapshot", side_effect=_build):
            adapter = HttpAdapter(host="127.0.0.1", port=port, dsn=None, store=mock_store)
            with adapter:
                _wait_for_server(port)
                status1, _body1 = _get(port, "/snapshot")
                status2, _body2 = _get(port, "/snapshot")

        assert status1 == 200
        assert status2 == 200
        # build_visual_snapshot must have been called exactly once (second hit uses cache)
        assert call_count == 1, f"Expected 1 snapshot build call; got {call_count}"

    def test_cache_refresh_after_ttl(self) -> None:
        """After the TTL expires, the next request triggers a fresh snapshot build."""
        import tapps_brain.http_adapter as _mod

        original_ttl = _mod._SNAPSHOT_TTL_SECONDS
        port = _free_port()
        mock_store = MagicMock()
        call_count = 0

        def _build(*args: Any, **kwargs: Any) -> MagicMock:
            nonlocal call_count
            call_count += 1
            return _make_mock_snapshot()

        # Patch TTL to near-zero so second request triggers a rebuild
        _mod._SNAPSHOT_TTL_SECONDS = 0.0
        try:
            with patch("tapps_brain.visual_snapshot.build_visual_snapshot", side_effect=_build):
                adapter = HttpAdapter(host="127.0.0.1", port=port, dsn=None, store=mock_store)
                with adapter:
                    _wait_for_server(port)
                    _get(port, "/snapshot")
                    time.sleep(0.05)  # TTL=0 → guaranteed miss
                    _get(port, "/snapshot")
        finally:
            _mod._SNAPSHOT_TTL_SECONDS = original_ttl

        assert call_count == 2, f"Expected 2 snapshot build calls after TTL; got {call_count}"

    def test_content_type_is_json(self) -> None:
        port = _free_port()
        mock_store = MagicMock()
        mock_snap = _make_mock_snapshot()
        conn = None
        with patch("tapps_brain.visual_snapshot.build_visual_snapshot", return_value=mock_snap):
            adapter = HttpAdapter(host="127.0.0.1", port=port, dsn=None, store=mock_store)
            with adapter:
                _wait_for_server(port)
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                try:
                    conn.request("GET", "/snapshot")
                    resp = conn.getresponse()
                    ct = resp.getheader("Content-Type", "")
                    resp.read()
                finally:
                    if conn:
                        conn.close()
        assert "application/json" in ct


class TestSnapshotEndpointAuth:
    """GET /snapshot auth follows the same bearer-token gate as /info."""

    def test_returns_401_without_token_when_auth_configured(self) -> None:
        port = _free_port()
        mock_store = MagicMock()
        adapter = HttpAdapter(
            host="127.0.0.1", port=port, dsn=None, auth_token=_TEST_TOKEN, store=mock_store
        )
        with adapter:
            _wait_for_server(port)
            status, _ = _get(port, "/snapshot")
        assert status == 401

    def test_returns_403_with_wrong_token(self) -> None:
        port = _free_port()
        mock_store = MagicMock()
        adapter = HttpAdapter(
            host="127.0.0.1", port=port, dsn=None, auth_token=_TEST_TOKEN, store=mock_store
        )
        with adapter:
            _wait_for_server(port)
            status, _ = _get_with_headers(
                port, "/snapshot", {"Authorization": "Bearer wrong-token"}
            )
        assert status == 403

    def test_returns_200_with_correct_token(self) -> None:
        port = _free_port()
        mock_store = MagicMock()
        mock_snap = _make_mock_snapshot()
        adapter = HttpAdapter(
            host="127.0.0.1", port=port, dsn=None, auth_token=_TEST_TOKEN, store=mock_store
        )
        with (
            patch("tapps_brain.visual_snapshot.build_visual_snapshot", return_value=mock_snap),
            adapter,
        ):
            _wait_for_server(port)
            status, _ = _get_with_headers(
                port, "/snapshot", {"Authorization": f"Bearer {_TEST_TOKEN}"}
            )
        assert status == 200


class TestSnapshotCorsPreflight:
    """OPTIONS /snapshot returns CORS preflight headers."""

    def test_options_returns_204_or_200(self) -> None:
        port = _free_port()
        adapter = HttpAdapter(host="127.0.0.1", port=port, dsn=None)
        with adapter:
            _wait_for_server(port)
            status, _ = _options(port, "/snapshot")
        assert status in (200, 204)

    def test_options_includes_allow_origin_star(self) -> None:
        port = _free_port()
        adapter = HttpAdapter(host="127.0.0.1", port=port, dsn=None)
        with adapter:
            _wait_for_server(port)
            _, resp_headers = _options(port, "/snapshot")
        assert resp_headers.get("access-control-allow-origin") == "*"


class TestSnapshotOpenApiSpec:
    """/snapshot must appear in the OpenAPI spec."""

    def test_snapshot_in_openapi_paths(self, adapter_no_dsn: HttpAdapter) -> None:
        _, body = _get(adapter_no_dsn.address[1], "/openapi.json")
        assert isinstance(body, dict)
        assert "/snapshot" in body.get("paths", {}), "/snapshot must be in OpenAPI spec paths"

    def test_snapshot_spec_has_503_response(self, adapter_no_dsn: HttpAdapter) -> None:
        _, body = _get(adapter_no_dsn.address[1], "/openapi.json")
        assert isinstance(body, dict)
        snapshot_spec = body.get("paths", {}).get("/snapshot", {}).get("get", {})
        assert "503" in snapshot_spec.get("responses", {}), "/snapshot spec must document 503"
