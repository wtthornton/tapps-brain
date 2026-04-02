"""Tests for save-time conflict detection (detect_save_conflicts)."""

from __future__ import annotations

from tapps_brain.contradictions import detect_save_conflicts
from tapps_brain.models import MemoryEntry, MemorySource, MemoryTier


def _entry(key: str, value: str, tier: MemoryTier) -> MemoryEntry:
    return MemoryEntry(
        key=key,
        value=value,
        tier=tier,
        confidence=0.5,
        source=MemorySource.agent,
    )


def test_detect_save_conflicts_returns_similar_same_tier() -> None:
    existing = [
        _entry("a", "the quick brown fox", MemoryTier.pattern),
        _entry("b", "unrelated procedural text here", MemoryTier.procedural),
    ]
    hits = detect_save_conflicts(
        "the quick brown fox jumps",
        MemoryTier.pattern.value,
        existing,
        similarity_threshold=0.25,
    )
    assert any(e.key == "a" for e in hits)
    assert all(e.key != "b" for e in hits)


def test_detect_save_conflicts_skips_other_tier() -> None:
    existing = [_entry("a", "same words", MemoryTier.architectural)]
    hits = detect_save_conflicts(
        "same words plus",
        MemoryTier.pattern.value,
        existing,
        similarity_threshold=0.1,
    )
    assert hits == []


def test_detect_save_conflicts_skips_identical_normalized() -> None:
    existing = [_entry("a", "  Hello   World  ", MemoryTier.pattern)]
    hits = detect_save_conflicts(
        "hello world",
        MemoryTier.pattern.value,
        existing,
        similarity_threshold=0.99,
    )
    assert hits == []


def test_detect_save_conflicts_excludes_save_target_key() -> None:
    """Updating key K must not flag K's prior value as a separate conflict."""
    existing = [_entry("shared", "thread-0-iter-0", MemoryTier.pattern)]
    hits = detect_save_conflicts(
        "thread-1-iter-0",
        MemoryTier.pattern.value,
        existing,
        similarity_threshold=0.25,
        exclude_key="shared",
    )
    assert hits == []


def test_detect_save_conflicts_sorted_by_similarity_then_key() -> None:
    hi = _entry("z", "alpha beta gamma delta", MemoryTier.context)
    lo = _entry("y", "alpha beta", MemoryTier.context)
    hits = detect_save_conflicts(
        "alpha beta gamma delta epsilon",
        MemoryTier.context.value,
        [lo, hi],
        similarity_threshold=0.2,
    )
    assert [e.key for e in hits] == ["z", "y"]
