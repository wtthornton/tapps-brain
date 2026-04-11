"""HTTP adapter for tapps-brain runtime API (STORY-060.3).

Exposes three read-only probe/metrics endpoints using **only** the Python
standard library — no external HTTP framework is required.

Routes
------
``GET /health``   — Liveness: always ``200 OK`` while the process is alive.
                    Does **not** require a database connection.
``GET /ready``    — Readiness: DB ping + highest applied migration version.
                    Returns ``200`` when ready, ``503`` when degraded.
``GET /metrics``  — Prometheus text-format exposition of basic runtime counters.
``GET /``         — Redirects to ``/health`` (convenience).

Usage::

    from tapps_brain.http_adapter import HttpAdapter

    adapter = HttpAdapter(host="0.0.0.0", port=8080, dsn="postgres://...")
    adapter.start()   # non-blocking background thread
    ...
    adapter.stop()

Or as a context manager::

    with HttpAdapter(port=8080, dsn="postgres://...") as adapter:
        ...  # adapter is running

The ``dsn`` parameter is used only for ``/ready`` and ``/metrics`` — it is
never included in response bodies or log output (ADR-007 / EPIC-063 secret
hygiene).

EPIC-060 STORY-060.3
"""

from __future__ import annotations

import http.server
import json
import os
import platform
import sys
import threading
import time
from typing import Any

import structlog

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Version / service metadata helpers
# ---------------------------------------------------------------------------

_SERVICE_NAME = "tapps-brain"


def _service_version() -> str:
    """Return the installed package version, or 'unknown'."""
    try:
        from importlib.metadata import version

        return version("tapps-brain")
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# DB probe helpers
# ---------------------------------------------------------------------------


def _probe_db(dsn: str | None) -> tuple[bool, int | None, str]:
    """Probe the Postgres database for readiness.

    Returns ``(is_ready, migration_version, message)``.

    - ``is_ready``:  False when the DB is unreachable.
    - ``migration_version``: Highest applied schema version, or ``None``.
    - ``message``: Human-readable status detail.

    Never raises — all errors are caught and reported via the return value.
    """
    if not dsn:
        return False, None, "no DSN configured (set TAPPS_BRAIN_DATABASE_URL)"

    try:
        from tapps_brain.postgres_migrations import get_hive_schema_status

        status = get_hive_schema_status(dsn)
        version = status.current_version if status.current_version else None
        pending = len(status.pending_migrations)
        if pending > 0:
            return (
                True,
                version,
                f"ready (migration_version={version}, pending={pending})",
            )
        return True, version, f"ready (migration_version={version})"
    except Exception as exc:
        return False, None, f"db_error: {exc}"


# ---------------------------------------------------------------------------
# Metrics helpers — Prometheus text format
# ---------------------------------------------------------------------------


def _collect_metrics(dsn: str | None) -> str:
    """Collect basic runtime metrics and return Prometheus text format.

    Deliberately minimal — high-cardinality labels such as query strings,
    memory keys, or agent IDs are **never** included (EPIC-061 redaction
    policy).
    """
    lines: list[str] = []

    def gauge(name: str, value: float, help_text: str = "") -> None:
        if help_text:
            lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name} {value}")

    # Process uptime (seconds since import)
    gauge(
        "tapps_brain_process_start_time_seconds",
        _PROCESS_START_TIME,
        "Unix timestamp when tapps-brain HTTP adapter was started.",
    )
    gauge(
        "tapps_brain_process_uptime_seconds",
        time.time() - _PROCESS_START_TIME,
        "Seconds since tapps-brain HTTP adapter started.",
    )

    # Python runtime
    gauge(
        "tapps_brain_python_info",
        1.0,
        f"Python version info (version={sys.version_info.major}.{sys.version_info.minor}).",
    )

    # DB readiness
    is_ready, migration_version, _ = _probe_db(dsn)
    gauge(
        "tapps_brain_db_ready",
        1.0 if is_ready else 0.0,
        "1 if the configured Postgres database responded to a probe, 0 otherwise.",
    )
    if migration_version is not None:
        gauge(
            "tapps_brain_db_migration_version",
            float(migration_version),
            "Highest applied Hive schema migration version.",
        )

    lines.append("")
    return "\n".join(lines)


# Module-level start time recorded once at import
_PROCESS_START_TIME: float = time.time()


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------


class _Handler(http.server.BaseHTTPRequestHandler):
    """Request handler for the tapps-brain HTTP adapter."""

    # Injected by HttpAdapter before serving
    _dsn: str | None = None
    _version: str = "unknown"

    # ------------------------------------------------------------------
    # Route dispatch
    # ------------------------------------------------------------------

    def do_GET(self) -> None:
        """Handle GET requests."""
        path = self.path.split("?", 1)[0]  # strip query string
        if path in ("/", "/health"):
            self._handle_health()
        elif path == "/ready":
            self._handle_ready()
        elif path == "/metrics":
            self._handle_metrics()
        else:
            self._send_json(404, {"error": "not_found", "path": path})

    def _handle_health(self) -> None:
        """Liveness: always 200 while process is alive."""
        body: dict[str, Any] = {
            "status": "ok",
            "service": _SERVICE_NAME,
            "version": self._version,
        }
        self._send_json(200, body)

    def _handle_ready(self) -> None:
        """Readiness: DB ping + migration version."""
        is_ready, migration_version, message = _probe_db(self._dsn)
        body: dict[str, Any] = {
            "status": "ready" if is_ready else "degraded",
            "migration_version": migration_version,
            "detail": message,
        }
        status_code = 200 if is_ready else 503
        self._send_json(status_code, body)

    def _handle_metrics(self) -> None:
        """Prometheus text-format metrics."""
        payload = _collect_metrics(self._dsn)
        encoded = payload.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _send_json(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args: object) -> None:
        """Redirect access log to structlog (suppress noisy stdout output)."""
        logger.debug(
            "http_adapter.request",
            remote=self.address_string(),
            method=self.command,
            path=self.path,
            status=args[1] if len(args) > 1 else "",
        )


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class HttpAdapter:
    """Minimal HTTP adapter exposing liveness, readiness, and metrics probes.

    Parameters
    ----------
    host:
        Bind address (default ``"127.0.0.1"``).
    port:
        TCP port to listen on (default ``8080``).
    dsn:
        PostgreSQL DSN used for ``/ready`` and ``/metrics``.
        Falls back to the ``TAPPS_BRAIN_DATABASE_URL`` environment variable,
        then ``TAPPS_BRAIN_HIVE_DSN``.  When neither is set, ``/ready``
        returns ``503`` and ``/metrics`` reports ``tapps_brain_db_ready 0``.
    """

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8080,
        dsn: str | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._dsn = dsn or self._resolve_dsn_from_env()
        self._server: http.server.HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._version = _service_version()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the HTTP server in a background daemon thread."""
        if self._server is not None:
            return  # already running

        # Build a handler subclass with the DSN bound in
        dsn = self._dsn
        version = self._version

        class BoundHandler(_Handler):
            _dsn = dsn
            _version = version

        self._server = http.server.HTTPServer((self._host, self._port), BoundHandler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
            name="tapps-brain-http",
        )
        self._thread.start()
        logger.info(
            "http_adapter.started",
            host=self._host,
            port=self._port,
            platform=platform.system(),
        )

    def stop(self) -> None:
        """Shut down the HTTP server and wait for the background thread to exit."""
        if self._server is None:
            return
        self._server.shutdown()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._server.server_close()
        self._server = None
        self._thread = None
        logger.info("http_adapter.stopped")

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def __enter__(self) -> HttpAdapter:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def address(self) -> tuple[str, int]:
        """Return ``(host, port)`` for the bound server."""
        if self._server is not None:
            return self._server.server_address  # type: ignore[return-value]
        return (self._host, self._port)

    @property
    def is_running(self) -> bool:
        """True if the server thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_dsn_from_env() -> str | None:
        dsn = (
            os.environ.get("TAPPS_BRAIN_DATABASE_URL")
            or os.environ.get("TAPPS_BRAIN_HIVE_DSN")
            or ""
        ).strip()
        return dsn or None
