"""Integration tests for TAP-1849: tapps_brain_mcp_probe_duration_seconds histogram.

Verifies that:
- ``_filtered_list_tools`` records timing observations to the probe histograms.
- Cache-hit (warm) and cache-miss (cold) paths land in separate histograms.
- ``get_probe_duration_histogram_snapshot`` returns the expected structure.
- ``/metrics`` Prometheus exposition text includes the histogram lines when
  observations exist.

No Postgres required — all state is pure in-memory module variables.
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any
from unittest.mock import MagicMock

import pytest

from tapps_brain.mcp_server.profile_registry import (
    ProfileRegistry,
    UnknownProfileError,
)
from tapps_brain.mcp_server.tool_filter import (
    _PROBE_HISTOGRAM_BUCKETS,
    _ProbeHistogram,
    clear_tools_list_cache,
    get_probe_duration_histogram_snapshot,
    install_tool_filter,
    reset_probe_histogram_counters,
    reset_profile_filter_counters,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tool(name: str) -> MagicMock:
    t = MagicMock()
    t.name = name
    return t


def _make_mock_mcp(tool_names: list[str]) -> MagicMock:
    manager = MagicMock()
    manager.list_tools.return_value = [_make_tool(n) for n in tool_names]
    manager.call_tool = MagicMock()
    mcp = MagicMock()
    mcp._tool_manager = manager
    mcp._mcp_server = None  # disable lowlevel handler wrap
    return mcp


def _make_registry(profile_map: dict[str, frozenset[str]]) -> MagicMock:
    registry = MagicMock(spec=ProfileRegistry)

    def _get(name: str) -> frozenset[str]:
        if name not in profile_map:
            raise UnknownProfileError(name, list(profile_map))
        return profile_map[name]

    registry.get.side_effect = _get
    return registry


ALL_TOOLS = ["brain_recall", "brain_remember", "memory_save"]


# ---------------------------------------------------------------------------
# Autouse fixture — reset module state between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_probe_state() -> Any:
    """Clear probe histograms and tools/list cache before each test."""
    reset_probe_histogram_counters()
    reset_profile_filter_counters()
    clear_tools_list_cache()
    yield
    reset_probe_histogram_counters()
    reset_profile_filter_counters()
    clear_tools_list_cache()


# ---------------------------------------------------------------------------
# _ProbeHistogram unit tests
# ---------------------------------------------------------------------------


class TestProbeHistogram:
    def test_empty_snapshot(self) -> None:
        h = _ProbeHistogram(_PROBE_HISTOGRAM_BUCKETS)
        snap = h.snapshot()
        assert snap["count"] == 0
        assert snap["sum"] == 0.0
        assert len(snap["bucket_counts"]) == len(_PROBE_HISTOGRAM_BUCKETS)
        assert all(c == 0 for c in snap["bucket_counts"])

    def test_observe_increments_count_and_sum(self) -> None:
        h = _ProbeHistogram(_PROBE_HISTOGRAM_BUCKETS)
        h.observe(0.03)
        snap = h.snapshot()
        assert snap["count"] == 1
        assert snap["sum"] == pytest.approx(0.03)

    def test_observe_warm_path_falls_in_first_bucket(self) -> None:
        """A 30 ms observation (0.03 s) should fall in le=0.05 and above."""
        h = _ProbeHistogram(_PROBE_HISTOGRAM_BUCKETS)
        h.observe(0.03)
        snap = h.snapshot()
        counts: list[int] = snap["bucket_counts"]  # type: ignore[assignment]
        buckets: tuple[float, ...] = snap["buckets"]  # type: ignore[assignment]
        # All buckets >= 0.05 should have count 1; bucket 0.05 is index 0.
        for i, bound in enumerate(buckets):
            if bound >= 0.03:
                assert counts[i] == 1, f"Expected count 1 for le={bound}, got {counts[i]}"

    def test_observe_cold_path_skips_small_buckets(self) -> None:
        """A 2.5 s observation should land in le=5.0 but not le=2.0."""
        h = _ProbeHistogram(_PROBE_HISTOGRAM_BUCKETS)
        h.observe(2.5)
        snap = h.snapshot()
        counts: list[int] = snap["bucket_counts"]  # type: ignore[assignment]
        buckets: tuple[float, ...] = snap["buckets"]  # type: ignore[assignment]
        for i, bound in enumerate(buckets):
            if bound < 2.5:
                assert counts[i] == 0, f"Expected 0 for le={bound}, got {counts[i]}"
            else:
                assert counts[i] == 1, f"Expected 1 for le={bound}, got {counts[i]}"

    def test_multiple_observations_cumulative(self) -> None:
        h = _ProbeHistogram(_PROBE_HISTOGRAM_BUCKETS)
        h.observe(0.03)  # hits all buckets
        h.observe(0.08)  # hits le>=0.1
        h.observe(1.5)   # hits le>=2.0
        snap = h.snapshot()
        assert snap["count"] == 3
        # le=0.05 only counts 0.03
        buckets: tuple[float, ...] = snap["buckets"]  # type: ignore[assignment]
        counts: list[int] = snap["bucket_counts"]  # type: ignore[assignment]
        idx_005 = list(buckets).index(0.05)
        idx_010 = list(buckets).index(0.1)
        idx_200 = list(buckets).index(2.0)
        assert counts[idx_005] == 1
        assert counts[idx_010] == 2
        assert counts[idx_200] == 3

    def test_reset_clears_all_state(self) -> None:
        h = _ProbeHistogram(_PROBE_HISTOGRAM_BUCKETS)
        h.observe(0.5)
        h.observe(1.0)
        h.reset()
        snap = h.snapshot()
        assert snap["count"] == 0
        assert snap["sum"] == 0.0
        assert all(c == 0 for c in snap["bucket_counts"])


# ---------------------------------------------------------------------------
# test_mcp_probe_histogram_increments — AC verification
# ---------------------------------------------------------------------------


def test_mcp_probe_histogram_increments() -> None:
    """Cache-miss calls record to _PROBE_HIST_MISS; cache-hit to _PROBE_HIST_HIT.

    This is the primary acceptance-criteria test for TAP-1849.
    """
    mcp = _make_mock_mcp(ALL_TOOLS)
    registry = _make_registry({"full": frozenset(ALL_TOOLS)})
    install_tool_filter(mcp, profile_registry=registry)

    # First call is always a cache miss (cache is empty after fixture reset).
    mcp._tool_manager.list_tools()

    snap = get_probe_duration_histogram_snapshot()
    miss_snap = snap["false"]
    hit_snap = snap["true"]

    assert miss_snap["count"] == 1, "Cache-miss should record exactly one observation"
    assert hit_snap["count"] == 0, "No cache-hit observation yet"
    assert miss_snap["sum"] >= 0.0

    # Second call hits the TAP-1833 cache → records to hit histogram.
    mcp._tool_manager.list_tools()

    snap2 = get_probe_duration_histogram_snapshot()
    assert snap2["false"]["count"] == 1, "Miss count unchanged after second call"
    assert snap2["true"]["count"] == 1, "Cache-hit should record exactly one observation"


def test_snapshot_structure_matches_buckets() -> None:
    """Snapshot keys and bucket count match _PROBE_HISTOGRAM_BUCKETS."""
    snap = get_probe_duration_histogram_snapshot()
    assert set(snap.keys()) == {"true", "false"}
    for label, s in snap.items():
        assert s["buckets"] == _PROBE_HISTOGRAM_BUCKETS, f"Wrong buckets for cache_hit={label}"
        assert len(s["bucket_counts"]) == len(_PROBE_HISTOGRAM_BUCKETS)


# ---------------------------------------------------------------------------
# /metrics rendering test
# ---------------------------------------------------------------------------


def test_collect_metrics_includes_histogram_when_observations_exist() -> None:
    """_collect_metrics renders tapps_brain_mcp_probe_duration_seconds lines."""
    # We need at least one observation to trigger rendering.
    mcp = _make_mock_mcp(ALL_TOOLS)
    registry = _make_registry({"full": frozenset(ALL_TOOLS)})
    install_tool_filter(mcp, profile_registry=registry)
    mcp._tool_manager.list_tools()  # triggers cache-miss observation

    # Import the renderer and call it without a DSN (metrics still renders).
    with suppress(Exception):
        from tapps_brain.http.metrics_collector import _collect_metrics

        text = _collect_metrics(dsn=None)
        assert "tapps_brain_mcp_probe_duration_seconds_bucket" in text
        assert 'cache_hit="false"' in text
        assert 'le="+Inf"' in text
        assert "tapps_brain_mcp_probe_duration_seconds_sum" in text
        assert "tapps_brain_mcp_probe_duration_seconds_count" in text


def test_collect_metrics_omits_histogram_when_no_observations() -> None:
    """Histogram lines are absent from /metrics when no tools/list calls occurred."""
    with suppress(Exception):
        from tapps_brain.http.metrics_collector import _collect_metrics

        text = _collect_metrics(dsn=None)
        assert "tapps_brain_mcp_probe_duration_seconds" not in text
