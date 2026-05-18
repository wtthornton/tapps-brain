"""Tests for the in-process recall-quality ring buffer (TAP-2094)."""

from __future__ import annotations

import pytest

from tapps_brain import recall_quality_buffer
from tapps_brain.recall_quality_buffer import RecallQualitySample


@pytest.fixture(autouse=True)
def _isolate_buffer() -> None:
    """Reset the global buffer (and default size) before and after each test."""
    recall_quality_buffer.configure(recall_quality_buffer.DEFAULT_MAX_SAMPLES_PER_PROJECT)
    yield
    recall_quality_buffer.reset()


def test_snapshot_of_unknown_project_is_empty() -> None:
    assert recall_quality_buffer.snapshot("proj-x") == []


def test_record_single_sample_round_trips() -> None:
    recall_quality_buffer.record(
        project_id="proj-a",
        top_score=0.91,
        oldest_returned_age_days=3.5,
        memory_count=4,
        timestamp=1000.0,
    )
    samples = recall_quality_buffer.snapshot("proj-a")
    assert samples == [
        RecallQualitySample(
            timestamp=1000.0,
            top_score=0.91,
            oldest_returned_age_days=3.5,
            memory_count=4,
        )
    ]


def test_per_project_isolation() -> None:
    recall_quality_buffer.record("proj-a", 0.5, 1.0, 1, timestamp=10.0)
    recall_quality_buffer.record("proj-b", 0.9, 7.0, 3, timestamp=20.0)
    a = recall_quality_buffer.snapshot("proj-a")
    b = recall_quality_buffer.snapshot("proj-b")
    assert len(a) == 1
    assert len(b) == 1
    assert a[0].top_score == 0.5
    assert b[0].top_score == 0.9


def test_eviction_keeps_only_last_maxlen_samples() -> None:
    recall_quality_buffer.configure(3)
    for i in range(5):
        recall_quality_buffer.record(
            "proj-a",
            top_score=float(i),
            oldest_returned_age_days=None,
            memory_count=1,
            timestamp=float(i),
        )
    samples = recall_quality_buffer.snapshot("proj-a")
    assert [s.top_score for s in samples] == [2.0, 3.0, 4.0]


def test_empty_recall_sample_is_recorded_with_nones() -> None:
    recall_quality_buffer.record("proj-a", None, None, 0, timestamp=42.0)
    samples = recall_quality_buffer.snapshot("proj-a")
    assert samples[0].memory_count == 0
    assert samples[0].top_score is None
    assert samples[0].oldest_returned_age_days is None


def test_configure_resets_existing_samples() -> None:
    recall_quality_buffer.record("proj-a", 0.5, 1.0, 1, timestamp=10.0)
    assert recall_quality_buffer.snapshot("proj-a")
    recall_quality_buffer.configure(50)
    assert recall_quality_buffer.snapshot("proj-a") == []


def test_configure_non_positive_falls_back_to_default() -> None:
    recall_quality_buffer.configure(0)
    # Default is 1000; record 1001 to verify eviction kicks in at the default.
    for i in range(1001):
        recall_quality_buffer.record(
            "proj-a",
            top_score=float(i),
            oldest_returned_age_days=None,
            memory_count=1,
            timestamp=float(i),
        )
    samples = recall_quality_buffer.snapshot("proj-a")
    assert len(samples) == 1000
    assert samples[0].top_score == 1.0
    assert samples[-1].top_score == 1000.0
