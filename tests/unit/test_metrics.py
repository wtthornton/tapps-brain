"""Tests for the metrics collector (STORY-007.1)."""

from __future__ import annotations

import threading
import time

from tapps_brain.metrics import (
    MetricsCollector,
    MetricsSnapshot,
    MetricsTimer,
    _Reservoir,
    compact_save_phase_summary,
)


class TestReservoir:
    def test_empty_stats(self):
        r = _Reservoir()
        stats = r.stats()
        assert stats.count == 0
        assert stats.min == 0.0
        assert stats.max == 0.0
        assert stats.mean == 0.0

    def test_single_value(self):
        r = _Reservoir()
        r.add(42.0)
        stats = r.stats()
        assert stats.count == 1
        assert stats.min == 42.0
        assert stats.max == 42.0
        assert stats.mean == 42.0
        assert stats.p50 == 42.0

    def test_multiple_values(self):
        r = _Reservoir()
        for v in [10.0, 20.0, 30.0, 40.0, 50.0]:
            r.add(v)
        stats = r.stats()
        assert stats.count == 5
        assert stats.min == 10.0
        assert stats.max == 50.0
        assert stats.mean == 30.0
        assert stats.p50 == 30.0

    def test_reservoir_overflow(self):
        """When more than _RESERVOIR_SIZE values are added, stats still work."""
        r = _Reservoir()
        for i in range(2000):
            r.add(float(i))
        stats = r.stats()
        assert stats.count == 2000
        assert stats.min == 0.0
        assert stats.max == 1999.0
        # Mean should be approximately 999.5
        assert 900 < stats.mean < 1100


class TestMetricsCollector:
    def test_increment_default(self):
        mc = MetricsCollector()
        mc.increment("test.counter")
        snap = mc.snapshot()
        assert snap.counters["test.counter"] == 1

    def test_increment_with_value(self):
        mc = MetricsCollector()
        mc.increment("test.counter", 5)
        mc.increment("test.counter", 3)
        snap = mc.snapshot()
        assert snap.counters["test.counter"] == 8

    def test_increment_with_tags(self):
        mc = MetricsCollector()
        mc.increment("ops", tags={"method": "save"})
        mc.increment("ops", tags={"method": "get"})
        snap = mc.snapshot()
        assert snap.counters["ops{method=save}"] == 1
        assert snap.counters["ops{method=get}"] == 1

    def test_observe(self):
        mc = MetricsCollector()
        for v in [1.0, 2.0, 3.0, 4.0, 5.0]:
            mc.observe("latency", v)
        snap = mc.snapshot()
        assert snap.histograms["latency"].count == 5
        assert snap.histograms["latency"].min == 1.0
        assert snap.histograms["latency"].max == 5.0
        assert snap.histograms["latency"].mean == 3.0

    def test_snapshot_frozen(self):
        mc = MetricsCollector()
        mc.increment("a", 1)
        snap1 = mc.snapshot()
        mc.increment("a", 1)
        snap2 = mc.snapshot()
        assert snap1.counters["a"] == 1
        assert snap2.counters["a"] == 2

    def test_reset(self):
        mc = MetricsCollector()
        mc.increment("x", 10)
        mc.observe("y", 42.0)
        mc.reset()
        snap = mc.snapshot()
        assert snap.counters == {}
        assert snap.histograms == {}

    def test_snapshot_has_captured_at(self):
        mc = MetricsCollector()
        snap = mc.snapshot()
        assert snap.captured_at != ""
        assert "T" in snap.captured_at  # ISO format

    def test_thread_safety(self):
        """Concurrent increments from multiple threads."""
        mc = MetricsCollector()
        num_threads = 10
        increments_per_thread = 100

        def worker():
            for _ in range(increments_per_thread):
                mc.increment("concurrent.counter")

        threads = [threading.Thread(target=worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        snap = mc.snapshot()
        assert snap.counters["concurrent.counter"] == num_threads * increments_per_thread

    def test_thread_safety_histogram(self):
        """Concurrent observations from multiple threads."""
        mc = MetricsCollector()
        num_threads = 10
        obs_per_thread = 100

        def worker():
            for i in range(obs_per_thread):
                mc.observe("concurrent.latency", float(i))

        threads = [threading.Thread(target=worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        snap = mc.snapshot()
        assert snap.histograms["concurrent.latency"].count == num_threads * obs_per_thread


class TestCompactSavePhaseSummary:
    def test_empty_when_no_histograms(self) -> None:
        snap = MetricsSnapshot()
        assert compact_save_phase_summary(snap) == ""

    def test_includes_observed_phases(self) -> None:
        from tapps_brain.metrics import HistogramStats

        snap = MetricsSnapshot(
            histograms={
                "store.save.phase.lock_build_ms": HistogramStats(
                    count=2, min=0.1, max=2.0, mean=1.0, p50=1.0, p95=1.8, p99=1.9
                ),
                "store.save.phase.persist_ms": HistogramStats(
                    count=2, min=5.0, max=6.0, mean=5.5, p50=5.5, p95=5.9, p99=5.95
                ),
            }
        )
        s = compact_save_phase_summary(snap)
        assert "lock_build_ms p50=1.0ms n=2" in s
        assert "persist_ms p50=5.5ms n=2" in s


class TestMetricsSnapshot:
    def test_serializable(self):
        mc = MetricsCollector()
        mc.increment("a", 1)
        mc.observe("b", 2.0)
        snap = mc.snapshot()
        d = snap.to_dict()
        assert isinstance(d, dict)
        assert d["counters"]["a"] == 1
        assert d["histograms"]["b"]["count"] == 1

    def test_roundtrip(self):
        mc = MetricsCollector()
        mc.increment("test", 42)
        snap = mc.snapshot()
        restored = MetricsSnapshot.model_validate(snap.to_dict())
        assert restored.counters["test"] == 42


class TestMetricsTimer:
    def test_records_elapsed(self):
        mc = MetricsCollector()
        with MetricsTimer(mc, "timer_test"):
            time.sleep(0.01)
        snap = mc.snapshot()
        stats = snap.histograms["timer_test"]
        assert stats.count == 1
        assert stats.min >= 5.0  # at least 5ms (sleep(0.01) = 10ms)
        assert stats.max < 1000.0  # but not absurd
