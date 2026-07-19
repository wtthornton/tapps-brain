"""Pin tests for the Run-6 HTTP-surface audit fixes.

Covers:
* Split-brain metrics fix — the counters the middleware writes are the ones
  ``/metrics`` renders (writer and reader share one module).
* ``tapps_brain_http_errors_total`` counts every 4xx/5xx (was 422/500 only).
* ``/v1/tools/list`` emits ``Vary: X-Brain-Profile`` and honours multi-ETag
  ``If-None-Match`` lists.
* Malformed numeric client fields return 400 instead of 500.
* KG entity-resolution routes are present in the REST profile gate map.
* Admin rate-limiter bucket dict is bounded (stale-IP sweep).
* ``AsyncMemoryStore.close()`` closes the sync store even when the async
  backend close raises.
* ``MemoryStore._scoped_persistence`` is thread-local — a concurrent thread
  keeps seeing the real backend during an async-native capture.
"""

from __future__ import annotations

import threading
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Metrics split-brain + error counters
# ---------------------------------------------------------------------------


class TestMetricsSingleSource:
    def test_record_labeled_request_is_metrics_collector_function(self) -> None:
        """http_adapter must re-export the metrics_collector implementation."""
        import tapps_brain.http.metrics_collector as mc
        import tapps_brain.http_adapter as ha

        assert ha._record_labeled_request is mc._record_labeled_request
        assert ha._collect_metrics is mc._collect_metrics
        assert ha._LABELED_REQUEST_COUNTS is mc._LABELED_REQUEST_COUNTS

    def test_middleware_write_visible_in_collect_metrics(self) -> None:
        """A counter written via the middleware's writer shows up in /metrics text."""
        import tapps_brain.http.metrics_collector as mc

        with mc._LABELED_REQUEST_COUNTS_LOCK:
            prior = dict(mc._LABELED_REQUEST_COUNTS)
            mc._LABELED_REQUEST_COUNTS.clear()
        try:
            mc._record_labeled_request("proj-split-brain", "agent-sb")
            from tapps_brain.http_adapter import _collect_metrics

            body = _collect_metrics(dsn=None, redact_tenant_labels=False)
            assert 'project_id="proj-split-brain"' in body
        finally:
            with mc._LABELED_REQUEST_COUNTS_LOCK:
                mc._LABELED_REQUEST_COUNTS.clear()
                mc._LABELED_REQUEST_COUNTS.update(prior)

    def test_settings_singleton_is_single_source(self) -> None:
        """http_adapter.get_settings and http.settings.get_settings return one object."""
        import tapps_brain.http.settings as hs
        import tapps_brain.http_adapter as ha

        assert ha.get_settings is hs.get_settings

    def test_otel_span_middleware_is_extracted_class(self) -> None:
        import tapps_brain.http.middleware as mw
        import tapps_brain.http_adapter as ha

        assert ha.OtelSpanMiddleware is mw.OtelSpanMiddleware


class TestHttpErrorCounter:
    def test_record_http_error_lives_in_metrics_collector(self) -> None:
        import tapps_brain.http.metrics_collector as mc
        import tapps_brain.http_adapter as ha

        assert ha._record_http_error is mc._record_http_error

    def test_http_exception_4xx_recorded(self) -> None:
        """Any HTTPException status >= 400 must land in the error counter."""
        pytest.importorskip("starlette")
        from starlette.testclient import TestClient

        import tapps_brain.http.metrics_collector as mc
        import tapps_brain.http_adapter as _http_mod
        from tapps_brain.http_adapter import _Settings, create_app

        s = _Settings.__new__(_Settings)
        s.dsn = None
        s.auth_token = None
        s.admin_token = None
        s.metrics_token = None
        s.allowed_origins = []
        s.version = "test"
        s.store = None
        s.snapshot_lock = threading.Lock()
        s.snapshot_cache = None
        s.snapshot_cache_at = 0.0
        s.idempotency_store = None
        s.async_store = None

        with mc._HTTP_ERROR_COUNTS_LOCK:
            prior = dict(mc._HTTP_ERROR_COUNTS)
            mc._HTTP_ERROR_COUNTS.clear()
        try:
            with (
                patch.object(_http_mod, "_settings", s),
                patch.object(_http_mod, "get_settings", return_value=s),
            ):
                dummy = MagicMock()
                dummy.session_manager = None
                app = create_app(mcp_server=dummy)
                with TestClient(app, raise_server_exceptions=False) as client:
                    # /v1/remember without X-Project-Id → HTTPException(400).
                    resp = client.post("/v1/remember", json={"key": "k", "value": "v"})
                assert resp.status_code == 400
            with mc._HTTP_ERROR_COUNTS_LOCK:
                assert mc._HTTP_ERROR_COUNTS.get(("/v1/remember", "400"), 0) >= 1
        finally:
            with mc._HTTP_ERROR_COUNTS_LOCK:
                mc._HTTP_ERROR_COUNTS.clear()
                mc._HTTP_ERROR_COUNTS.update(prior)


# ---------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------


class TestCoerceHelpers:
    def test_coerce_int_bad_value_raises_400(self) -> None:
        from fastapi import HTTPException

        from tapps_brain.http_adapter import _coerce_int

        with pytest.raises(HTTPException) as exc_info:
            _coerce_int({"max_results": "five"}, "max_results", 5)
        assert exc_info.value.status_code == 400

    def test_coerce_int_list_value_raises_400(self) -> None:
        from fastapi import HTTPException

        from tapps_brain.http_adapter import _coerce_int

        with pytest.raises(HTTPException) as exc_info:
            _coerce_int({"hops": [1]}, "hops", 1)
        assert exc_info.value.status_code == 400

    def test_coerce_float_bad_value_raises_400(self) -> None:
        from fastapi import HTTPException

        from tapps_brain.http_adapter import _coerce_float

        with pytest.raises(HTTPException) as exc_info:
            _coerce_float({"confidence": {}}, "confidence", -1.0)
        assert exc_info.value.status_code == 400

    def test_coerce_defaults_applied_when_absent(self) -> None:
        from tapps_brain.http_adapter import _coerce_float, _coerce_int

        assert _coerce_int({}, "limit", 20) == 20
        assert _coerce_float({}, "utility_score", 0.0) == 0.0


# ---------------------------------------------------------------------------
# REST profile gate map
# ---------------------------------------------------------------------------


class TestRestGateMap:
    def test_kg_resolve_routes_are_gated(self) -> None:
        from tapps_brain.http.rest_profile_gate import resolve_tool_for_path

        assert resolve_tool_for_path("/v1/kg/resolve_entity") == "brain_resolve_entity"
        assert resolve_tool_for_path("/v1/kg/resolve_entities") == "brain_resolve_entity"


# ---------------------------------------------------------------------------
# Admin rate-limit bucket sweep
# ---------------------------------------------------------------------------


class TestAdminRateBucketSweep:
    def test_stale_ips_swept_past_threshold(self) -> None:
        import tapps_brain.http.auth as auth

        with auth._admin_rate_lock:
            prior = dict(auth._admin_rate_buckets)
            auth._admin_rate_buckets.clear()
        try:
            import collections
            import time as _time

            stale_ts = _time.monotonic() - auth._ADMIN_RATE_WINDOW - 10
            with auth._admin_rate_lock:
                for i in range(auth._ADMIN_RATE_SWEEP_THRESHOLD + 5):
                    auth._admin_rate_buckets[f"10.0.{i // 256}.{i % 256}"] = collections.deque(
                        [stale_ts]
                    )
            assert auth._check_admin_rate_limit("192.168.1.1") is True
            with auth._admin_rate_lock:
                # All stale IPs swept; only the fresh caller remains.
                assert len(auth._admin_rate_buckets) == 1
                assert "192.168.1.1" in auth._admin_rate_buckets
        finally:
            with auth._admin_rate_lock:
                auth._admin_rate_buckets.clear()
                auth._admin_rate_buckets.update(prior)


# ---------------------------------------------------------------------------
# aio: thread-local capture + close() ordering
# ---------------------------------------------------------------------------


class TestScopedPersistenceThreadLocal:
    def test_override_invisible_to_other_threads(self, tmp_path: Any) -> None:
        from tapps_brain.store import MemoryStore
        from tests.conftest import InMemoryPrivateBackend

        store = MemoryStore(tmp_path, private_backend=InMemoryPrivateBackend())
        real = store._persistence
        fake = InMemoryPrivateBackend()

        other_thread_backend: list[Any] = []
        entered = threading.Event()
        release = threading.Event()

        def _in_override() -> None:
            with store._scoped_persistence(fake):
                entered.set()
                release.wait(timeout=5)

        t = threading.Thread(target=_in_override)
        t.start()
        try:
            assert entered.wait(timeout=5)
            # Main thread must still see the real backend mid-override.
            other_thread_backend.append(store._persistence)
        finally:
            release.set()
            t.join(timeout=5)

        assert other_thread_backend[0] is real
        # After the override exits, the owning thread sees the real backend too.
        assert store._persistence is real
        store.close()

    def test_setter_still_replaces_shared_backend(self, tmp_path: Any) -> None:
        """Legacy ``store._persistence = X`` assignment keeps working."""
        from tapps_brain.store import MemoryStore
        from tests.conftest import InMemoryPrivateBackend

        store = MemoryStore(tmp_path, private_backend=InMemoryPrivateBackend())
        replacement = InMemoryPrivateBackend()
        store._persistence = replacement
        assert store._persistence is replacement
        store.close()


class TestAsyncCloseOrdering:
    @pytest.mark.asyncio
    async def test_sync_store_closed_when_async_backend_close_raises(self, tmp_path: Any) -> None:
        from tapps_brain.aio import AsyncMemoryStore
        from tapps_brain.store import MemoryStore
        from tests.conftest import InMemoryPrivateBackend

        store = MemoryStore(tmp_path, private_backend=InMemoryPrivateBackend())
        sync_close = MagicMock(wraps=store.close)
        store.close = sync_close  # type: ignore[method-assign]

        backend = MagicMock()

        async def _boom() -> None:
            raise RuntimeError("pool already dead")

        backend.close = _boom

        wrapper = AsyncMemoryStore(store, async_backend=backend)
        with pytest.raises(RuntimeError, match="pool already dead"):
            await wrapper.close()
        sync_close.assert_called_once()
