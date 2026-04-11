"""Contract tests for tapps_brain.http_adapter (STORY-060.3).

Tests cover:
- /health: always 200, JSON body
- /ready: 200 when DB reachable, 503 when DB down / DSN missing
- /metrics: Prometheus text format, correct Content-Type, key gauge names
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

from tapps_brain.http_adapter import HttpAdapter, _probe_db, _service_version

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
