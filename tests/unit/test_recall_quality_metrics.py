"""Tests for ``memory_service.recall_quality_metrics`` (TAP-2094).

Exercises the percentile aggregator against a seeded ring buffer.
"""

from __future__ import annotations

import time

import pytest

from tapps_brain import recall_quality_buffer
from tapps_brain.services import memory_service


@pytest.fixture(autouse=True)
def _isolate_buffer() -> None:
    recall_quality_buffer.configure(recall_quality_buffer.DEFAULT_MAX_SAMPLES_PER_PROJECT)
    yield
    recall_quality_buffer.reset()


def _seed(project_id: str, samples: list[tuple[float | None, float | None, int]]) -> None:
    """Seed *samples* into the buffer with timestamps centred on now."""
    now = time.time()
    for top, age, count in samples:
        recall_quality_buffer.record(
            project_id=project_id,
            top_score=top,
            oldest_returned_age_days=age,
            memory_count=count,
            timestamp=now,
        )


def test_empty_buffer_returns_zero_sample_count() -> None:
    report = memory_service.recall_quality_metrics(store=None, project_id="proj-a", agent_id="cli")
    assert report["sample_count"] == 0
    assert report["p50_top_score"] is None
    assert report["p95_top_score"] is None
    assert report["empty_recall_rate"] == 0.0
    assert report["window_seconds"] == 3600
    assert report["project_id"] == "proj-a"


def test_invalid_window_returns_error() -> None:
    report = memory_service.recall_quality_metrics(
        store=None, project_id="proj-a", agent_id="cli", window_seconds=0
    )
    assert report["error"] == "invalid_window"


def test_single_sample_p50_equals_value() -> None:
    _seed("proj-a", [(0.75, 2.0, 3)])
    report = memory_service.recall_quality_metrics(store=None, project_id="proj-a", agent_id="cli")
    assert report["sample_count"] == 1
    assert report["p50_top_score"] == pytest.approx(0.75)
    assert report["p95_top_score"] == pytest.approx(0.75)
    assert report["p50_oldest_age_days"] == pytest.approx(2.0)
    assert report["empty_recall_rate"] == 0.0


def test_percentiles_with_uniform_distribution() -> None:
    _seed("proj-a", [(s, None, 1) for s in [0.1, 0.2, 0.3, 0.4, 0.5]])
    report = memory_service.recall_quality_metrics(store=None, project_id="proj-a", agent_id="cli")
    # Linear interp on sorted [0.1, 0.2, 0.3, 0.4, 0.5] (n=5): p50 = index 2 = 0.3,
    # p95 = index 3.8 = 0.4 + 0.8 * (0.5 - 0.4) = 0.48.
    assert report["p50_top_score"] == pytest.approx(0.3)
    assert report["p95_top_score"] == pytest.approx(0.48)
    assert report["sample_count"] == 5


def test_empty_recall_rate_counts_zero_memory_samples() -> None:
    _seed(
        "proj-a",
        [(0.8, 1.0, 2), (None, None, 0), (None, None, 0), (0.4, 3.0, 1)],
    )
    report = memory_service.recall_quality_metrics(store=None, project_id="proj-a", agent_id="cli")
    assert report["sample_count"] == 4
    assert report["empty_recall_rate"] == pytest.approx(0.5)


def test_window_filter_excludes_old_samples() -> None:
    now = time.time()
    # Old sample (1 day ago) — out of 60s window
    recall_quality_buffer.record("proj-a", 0.1, 99.0, 1, timestamp=now - 86400)
    # Recent sample — within 60s window
    recall_quality_buffer.record("proj-a", 0.9, 0.5, 3, timestamp=now)
    report = memory_service.recall_quality_metrics(
        store=None,
        project_id="proj-a",
        agent_id="cli",
        window_seconds=60,
    )
    assert report["sample_count"] == 1
    assert report["p50_top_score"] == pytest.approx(0.9)


def test_target_project_id_overrides_caller_project() -> None:
    _seed("proj-target", [(0.9, 1.0, 1)])
    report = memory_service.recall_quality_metrics(
        store=None,
        project_id="proj-caller",
        agent_id="cli",
        target_project_id="proj-target",
    )
    assert report["project_id"] == "proj-target"
    assert report["sample_count"] == 1


def test_missing_top_score_samples_excluded_from_percentile() -> None:
    """Empty recalls (top_score=None) must not skew p50/p95 computation."""
    _seed("proj-a", [(None, None, 0), (None, None, 0), (0.6, 2.0, 1)])
    report = memory_service.recall_quality_metrics(store=None, project_id="proj-a", agent_id="cli")
    assert report["sample_count"] == 3
    assert report["p50_top_score"] == pytest.approx(0.6)
    assert report["empty_recall_rate"] == pytest.approx(2 / 3, rel=1e-3)
