"""Benchmark: /v1/tools/list warm-path latency gate (TAP-1855).

Asserts p95 < 200 ms and p99 < 500 ms for the static snapshot route
served by ``GET /v1/tools/list`` after ASGI lifespan startup.

The snapshot is built once during ASGI lifespan from
``mcp._tool_manager.list_tools()``.  Every subsequent request reads from
an in-memory ``bytes`` buffer — no serialisation, no DB round-trip.

Run with::

    pytest tests/benchmarks/test_http_adapter_tools_list.py -v --benchmark-only

Or via the release gate (with baseline comparison)::

    SKIP_BENCHMARKS=0 bash scripts/release-ready.sh

See ``docs/perf/benchmarks.md`` for baseline refresh procedure.
"""

from __future__ import annotations

import threading
import types
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest

import tapps_brain.http_adapter as _http_mod
from tapps_brain.http_adapter import _service_version, _Settings, create_app

if TYPE_CHECKING:
    pass

pytestmark = pytest.mark.benchmark

# Latency gates (TAP-1855 ACs)
_P95_LIMIT_S: float = 0.200  # 200 ms
_P99_LIMIT_S: float = 0.500  # 500 ms


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tool(name: str, description: str) -> Any:
    """Return a SimpleNamespace tool stub.

    MagicMock.name is a reserved attribute for mock naming and cannot be
    overridden via dot assignment.  SimpleNamespace has no such restriction.
    """
    return types.SimpleNamespace(
        name=name,
        description=description,
        parameters={"type": "object", "properties": {}},
    )


def _make_bench_settings() -> _Settings:
    """Construct a minimal _Settings with no auth (unauthenticated route)."""
    s = _Settings.__new__(_Settings)
    s.dsn = None
    s.auth_token = None
    s.admin_token = None
    s.metrics_token = None
    s.allowed_origins = []
    s.version = _service_version()
    s.store = None
    s.snapshot_lock = threading.Lock()
    s.snapshot_cache = None
    s.snapshot_cache_at = 0.0
    s.idempotency_store = None
    s.async_store = None
    return s


# ---------------------------------------------------------------------------
# Module-scoped fixture (app created once, TestClient holds ASGI lifespan)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def tools_list_client():  # type: ignore[return]
    """Return a Starlette TestClient with a live ASGI lifespan.

    Module scope: the ASGI app and its startup snapshot are built once for
    the entire benchmark module, so every benchmark round hits the warm path.
    Uses ``starlette.testclient.TestClient`` — httpx ASGITransport does NOT
    run the ASGI lifespan; TestClient does.
    """
    from starlette.testclient import TestClient

    mock_tool_manager = MagicMock()
    mock_tool_manager.list_tools.return_value = [
        _make_tool("brain_remember", "Store a memory entry"),
        _make_tool("brain_recall", "Retrieve memory entries"),
        _make_tool("brain_forget", "Delete a memory entry by key"),
        _make_tool("brain_search", "Full-text search over memory entries"),
        _make_tool("brain_status", "Return memory system health and metrics"),
    ]

    mcp_server = MagicMock()
    mcp_server.session_manager = None
    mcp_server._tool_manager = mock_tool_manager

    settings = _make_bench_settings()

    with (
        patch.object(_http_mod, "_settings", settings),
        patch.object(_http_mod, "get_settings", return_value=settings),
    ):
        app = create_app(mcp_server=mcp_server)
        with TestClient(app, raise_server_exceptions=True) as client:
            yield client


# ---------------------------------------------------------------------------
# Benchmark class
# ---------------------------------------------------------------------------


class TestV1ToolsListBenchmark:
    """Warm-path latency benchmark for ``GET /v1/tools/list`` (TAP-1855)."""

    def test_warm_p95_under_200ms(self, benchmark: Any, tools_list_client: Any) -> None:
        """Warm once, benchmark 100 rounds; assert p95 < 200 ms, p99 < 500 ms.

        AC (TAP-1855): benchmark warms the cache once (1 call), then runs 100
        calls; p95 < 200 ms, p99 < 500 ms.

        The snapshot is an in-memory bytes buffer (no Python serialisation per
        request) so the hot path should complete in single-digit milliseconds.
        The 200 ms / 500 ms limits are conservative and leave headroom for CI
        runner variance.
        """

        def _hit() -> None:
            resp = tools_list_client.get("/v1/tools/list")
            assert resp.status_code == 200

        # pedantic mode: 1 warmup (not counted) + 100 measured rounds.
        benchmark.pedantic(_hit, warmup_rounds=1, rounds=100, iterations=1)

        # --- Assert p95 / p99 from raw timing data ---
        # benchmark.stats is a Metadata dict subclass (pytest-benchmark v5.x).
        # Attribute-style access fails for "data"; use dict key access.
        data = sorted(benchmark.stats["data"])
        n = len(data)
        assert n >= 100, f"expected ≥100 measured rounds, got {n}"

        p95 = data[min(int(0.95 * n), n - 1)]
        p99 = data[min(int(0.99 * n), n - 1)]

        assert p95 < _P95_LIMIT_S, (
            f"p95 latency {p95 * 1000:.1f} ms ≥ {_P95_LIMIT_S * 1000:.0f} ms limit "
            f"(TAP-1855 AC); check for eager imports or synchronous DB calls on the hot path"
        )
        assert p99 < _P99_LIMIT_S, (
            f"p99 latency {p99 * 1000:.1f} ms ≥ {_P99_LIMIT_S * 1000:.0f} ms limit (TAP-1855 AC)"
        )
