"""Tests for the TAP-2094 ``top_score`` and ``oldest_returned_age_days`` fields.

Exercises :func:`tapps_brain.recall._compute_recall_quality` against the
memory-dict shape produced by ``inject_memories``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tapps_brain.recall import _compute_recall_quality


def _ts(days_ago: float) -> str:
    return (datetime.now(tz=UTC) - timedelta(days=days_ago)).isoformat()


def test_empty_memories_returns_none_for_both_fields() -> None:
    top, age = _compute_recall_quality([])
    assert top is None
    assert age is None


def test_single_memory_returns_its_score_and_age() -> None:
    top, age = _compute_recall_quality([{"key": "a", "score": 0.91, "last_accessed": _ts(3.0)}])
    assert top == 0.91
    assert age is not None
    assert 2.99 < age < 3.01


def test_top_score_is_max_across_memories() -> None:
    top, _ = _compute_recall_quality(
        [
            {"key": "a", "score": 0.4, "last_accessed": _ts(1.0)},
            {"key": "b", "score": 0.7, "last_accessed": _ts(1.0)},
            {"key": "c", "score": 0.55, "last_accessed": _ts(1.0)},
        ]
    )
    assert top == 0.7


def test_oldest_age_is_max_age_across_memories() -> None:
    _, age = _compute_recall_quality(
        [
            {"key": "a", "score": 0.5, "last_accessed": _ts(2.0)},
            {"key": "b", "score": 0.5, "last_accessed": _ts(10.5)},
            {"key": "c", "score": 0.5, "last_accessed": _ts(0.1)},
        ]
    )
    assert age is not None
    assert 10.4 < age < 10.6


def test_missing_score_defaults_to_zero() -> None:
    top, _ = _compute_recall_quality(
        [
            {"key": "a", "last_accessed": _ts(1.0)},  # no score key
            {"key": "b", "score": 0.3, "last_accessed": _ts(1.0)},
        ]
    )
    assert top == 0.3


def test_malformed_last_accessed_skipped_in_age_calc() -> None:
    top, age = _compute_recall_quality(
        [
            {"key": "a", "score": 0.5, "last_accessed": "not-a-timestamp"},
            {"key": "b", "score": 0.5, "last_accessed": _ts(4.0)},
        ]
    )
    assert top == 0.5
    assert age is not None
    assert 3.99 < age < 4.01


def test_all_missing_last_accessed_returns_none_age() -> None:
    """If every entry has a broken timestamp, oldest_age is None — not 0."""
    top, age = _compute_recall_quality(
        [
            {"key": "a", "score": 0.5, "last_accessed": ""},
            {"key": "b", "score": 0.5, "last_accessed": "garbage"},
        ]
    )
    assert top == 0.5
    assert age is None


def test_z_suffix_iso_timestamps_are_accepted() -> None:
    """Postgres returns ``2026-05-17T12:00:00Z``-style timestamps."""
    iso_z = (datetime.now(tz=UTC) - timedelta(days=5)).isoformat().replace("+00:00", "Z")
    _, age = _compute_recall_quality([{"key": "a", "score": 0.5, "last_accessed": iso_z}])
    assert age is not None
    assert 4.99 < age < 5.01
