"""TAP-549 — bounded session-state growth in ``MemoryStore``.

The in-memory implicit-feedback helper dicts are keyed by ``session_id``.
Entries inside a given session are pruned lazily on same-session access,
but the outer ``session_id`` key was never removed — a client that
rotates ``session_id`` on every call slow-burns OOM the adapter.

These tests pin the two-layer fix:

1. ``MemoryStore.gc()`` drives ``_sweep_stale_sessions`` which drops
   ``session_id`` keys whose most recent log timestamp is older than
   ``2 * implicit_feedback_window_seconds``.
2. A hard LRU cap (``_SESSION_STATE_HARD_CAP``) evicts the least-recently-
   touched sessions when the cap is exceeded, even if none are yet
   "stale" by the time window.

Plus observability: ``MemoryStore.active_session_count`` and the
``StoreHealthReport.active_session_count`` field feed the
``tapps_brain_store_active_sessions`` Prometheus gauge.
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest

from tapps_brain.store import MemoryStore


@pytest.fixture()
def store(tmp_path: Path):
    s = MemoryStore(tmp_path)
    yield s
    s.close()


def _seed_session_state(
    store: MemoryStore,
    session_id: str,
    *,
    touch_time: float,
    key: str = "k",
) -> None:
    """Inject synthetic entries into every session-keyed helper dict.

    Bypasses the normal recall/save path so we can control ``monotonic``
    time stamps and test sweep behaviour without timing the test suite
    against real clocks.
    """
    with store._serialized():
        store._session_recall_log.setdefault(session_id, []).append((key, touch_time))
        store._session_query_log.setdefault(session_id, []).append(("q", [key], touch_time))
        store._session_recalled_values.setdefault(session_id, []).append(
            (key, "recalled-value", touch_time)
        )
        store._session_reinforced.setdefault(session_id, set()).add(key)
        store._hive_feedback_key_index.setdefault(session_id, {})[key] = "ns"


class TestSweepDropsStaleSessions:
    """AC: ``gc()`` sweeps stale session_ids in all four (+1) dicts."""

    def test_rotate_1000_sessions_then_gc_empties_all_dicts(self, store: MemoryStore) -> None:
        """Core acceptance test: rotate 1,000 distinct session_ids
        (each with an activity timestamp well older than the sweep
        cutoff), run ``gc()``, and assert every session-keyed helper
        dict is empty.
        """
        window = store._get_implicit_feedback_window()
        # Force "old" timestamps so every session is beyond 2*window.
        old_time = time.monotonic() - (2 * window + 10)
        for i in range(1_000):
            _seed_session_state(store, f"sess-{i:04d}", touch_time=old_time)

        assert store.active_session_count() == 1_000

        store.gc(dry_run=False)

        # Every session-keyed dict drained.
        assert store._session_recall_log == {}
        assert store._session_query_log == {}
        assert store._session_recalled_values == {}
        assert store._session_reinforced == {}
        assert store._hive_feedback_key_index == {}
        assert store.active_session_count() == 0

    def test_fresh_sessions_survive_sweep(self, store: MemoryStore) -> None:
        """Sessions with a recent timestamp stay put — sweep must only
        drop ``last_touch < now - 2*window`` sessions.
        """
        fresh = time.monotonic() - 1.0  # 1 second ago
        _seed_session_state(store, "fresh-sess", touch_time=fresh)
        assert store.active_session_count() == 1

        store.gc(dry_run=False)

        assert "fresh-sess" in store._session_recall_log
        assert store.active_session_count() == 1

    def test_sweep_mixed_fresh_and_stale(self, store: MemoryStore) -> None:
        """Stale sessions drop, fresh sessions stay, no cross-leak."""
        window = store._get_implicit_feedback_window()
        old = time.monotonic() - (2 * window + 60)
        fresh = time.monotonic() - 1.0

        for i in range(100):
            _seed_session_state(store, f"old-{i:03d}", touch_time=old)
        for i in range(25):
            _seed_session_state(store, f"fresh-{i:03d}", touch_time=fresh)

        store.gc(dry_run=False)

        assert store.active_session_count() == 25
        for i in range(25):
            assert f"fresh-{i:03d}" in store._session_recall_log
        for i in range(100):
            assert f"old-{i:03d}" not in store._session_recall_log

    def test_dry_run_gc_does_not_sweep(self, store: MemoryStore) -> None:
        """``gc(dry_run=True)`` must not touch session state — dry-run is
        a preview path for memory-entry archival; session dicts are
        process-local and have nothing to preview.
        """
        window = store._get_implicit_feedback_window()
        old = time.monotonic() - (2 * window + 60)
        _seed_session_state(store, "stale", touch_time=old)
        assert store.active_session_count() == 1

        store.gc(dry_run=True)

        assert store.active_session_count() == 1, "dry-run gc() must not mutate session state"


class TestLruHardCap:
    """AC (extension of #2 in the ticket): hard cap evicts LRU when the
    total session count exceeds the configured maximum, even if no
    session is yet time-stale.
    """

    def test_exceeding_hard_cap_evicts_oldest_sessions(self, store: MemoryStore) -> None:
        """Seed ``cap + 50`` fresh sessions with monotonically-increasing
        touch times; after sweep the oldest 50 must be gone and
        ``_sweep_stale_sessions`` must report ``lru_evicted=50``.
        """
        # Small cap so the test stays fast + readable.
        with patch("tapps_brain.store._SESSION_STATE_HARD_CAP", 100):
            base = time.monotonic() - 5.0  # fresh but ordered
            for i in range(150):
                _seed_session_state(
                    store,
                    f"sess-{i:03d}",
                    # Later indexes get newer timestamps → oldest indexes evict first.
                    touch_time=base + (i * 0.001),
                )
            assert store.active_session_count() == 150

            report = store._sweep_stale_sessions()

        assert report == {"stale_removed": 0, "lru_evicted": 50}
        assert store.active_session_count() == 100
        # The oldest 50 (sess-000 .. sess-049) are gone.
        for i in range(50):
            assert f"sess-{i:03d}" not in store._session_recall_log
        # The newest 100 (sess-050 .. sess-149) survive.
        for i in range(50, 150):
            assert f"sess-{i:03d}" in store._session_recall_log

    def test_no_eviction_under_hard_cap(self, store: MemoryStore) -> None:
        fresh = time.monotonic() - 1.0
        for i in range(50):
            _seed_session_state(store, f"sess-{i:03d}", touch_time=fresh)
        report = store._sweep_stale_sessions()
        assert report == {"stale_removed": 0, "lru_evicted": 0}
        assert store.active_session_count() == 50

    def test_eviction_increments_metrics_counter(self, store: MemoryStore) -> None:
        """When LRU eviction runs, ``store.session_state_evicted`` ticks
        up so ops can alert on sustained eviction (indicator of a
        ``session_id`` rotation regression in a client).
        """
        with patch("tapps_brain.store._SESSION_STATE_HARD_CAP", 10):
            base = time.monotonic() - 5.0
            for i in range(15):
                _seed_session_state(store, f"sess-{i:02d}", touch_time=base + i * 0.001)
            store._sweep_stale_sessions()

        snapshot = store._metrics.snapshot()
        assert snapshot.counters.get("store.session_state_evicted", 0) == 5


class TestHealthExposesActiveSessionCount:
    """AC: ``StoreHealthReport.active_session_count`` reflects the live
    in-memory cardinality so /metrics and /health expose it.
    """

    def test_health_report_includes_active_session_count(self, store: MemoryStore) -> None:
        fresh = time.monotonic() - 1.0
        for i in range(7):
            _seed_session_state(store, f"sess-{i}", touch_time=fresh)
        report = store.health()
        assert report.active_session_count == 7

    def test_active_session_count_after_sweep_drops_to_zero(self, store: MemoryStore) -> None:
        window = store._get_implicit_feedback_window()
        old = time.monotonic() - (2 * window + 10)
        for i in range(3):
            _seed_session_state(store, f"old-{i}", touch_time=old)
        assert store.active_session_count() == 3

        store.gc(dry_run=False)
        assert store.active_session_count() == 0
        # Health report mirrors the live state, not a cached value.
        assert store.health().active_session_count == 0


class TestMetricsGauge:
    """TAP-549 — ``/metrics`` exposes ``tapps_brain_store_active_sessions``."""

    def test_metrics_body_contains_active_sessions_gauge(self, store: MemoryStore) -> None:
        """``_collect_metrics`` renders the gauge with the live count."""
        from tapps_brain.http_adapter import _collect_metrics

        fresh = time.monotonic() - 1.0
        for i in range(4):
            _seed_session_state(store, f"sess-{i}", touch_time=fresh)

        body = _collect_metrics(dsn=None, store=store, redact_tenant_labels=True)
        assert "tapps_brain_store_active_sessions 4" in body

    def test_metrics_gauge_silent_when_store_is_none(self) -> None:
        """No store attached → metric simply isn't emitted (no crash)."""
        from tapps_brain.http_adapter import _collect_metrics

        body = _collect_metrics(dsn=None, store=None, redact_tenant_labels=True)
        assert "tapps_brain_store_active_sessions" not in body

    def test_metrics_gauge_robust_to_store_exception(self, store: MemoryStore) -> None:
        """A store that raises inside ``active_session_count`` must not
        crash ``/metrics`` — the gauge is best-effort telemetry.
        """
        from tapps_brain.http_adapter import _collect_metrics

        class _BrokenStore:
            def active_session_count(self) -> int:
                raise RuntimeError("boom")

        body = _collect_metrics(dsn=None, store=_BrokenStore(), redact_tenant_labels=True)
        assert "tapps_brain_store_active_sessions" not in body

    def test_metrics_gauge_respects_tenant_redaction_context(self, store: MemoryStore) -> None:
        """Gauge is a process-wide counter (no per-tenant labels), so it
        is emitted identically regardless of the ``redact_tenant_labels``
        setting.  This guards against future drift where someone decides
        the session count is tenant-identifying.
        """
        from tapps_brain.http_adapter import _collect_metrics

        fresh = time.monotonic() - 1.0
        _seed_session_state(store, "s1", touch_time=fresh)

        full = _collect_metrics(dsn=None, store=store, redact_tenant_labels=False)
        redacted = _collect_metrics(dsn=None, store=store, redact_tenant_labels=True)
        assert "tapps_brain_store_active_sessions 1" in full
        assert "tapps_brain_store_active_sessions 1" in redacted


class TestSweepIdempotency:
    """Running the sweep twice must not over-count or drop anything."""

    def test_second_sweep_is_noop(self, store: MemoryStore) -> None:
        fresh = time.monotonic() - 1.0
        for i in range(10):
            _seed_session_state(store, f"s{i}", touch_time=fresh)

        first = store._sweep_stale_sessions()
        second = store._sweep_stale_sessions()

        assert first == {"stale_removed": 0, "lru_evicted": 0}
        assert second == {"stale_removed": 0, "lru_evicted": 0}
        assert store.active_session_count() == 10
