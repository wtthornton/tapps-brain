"""Tests for save-time conflict detection (detect_save_conflicts)."""

from __future__ import annotations

from pathlib import Path

from tapps_brain.contradictions import (
    detect_save_conflicts,
    format_save_conflict_reason,
)
from tapps_brain.models import MemoryEntry, MemorySource, MemoryTier
from tapps_brain.profile import ConflictCheckConfig, LayerDefinition, MemoryProfile, ScoringConfig
from tapps_brain.store import MemoryStore


def _entry(key: str, value: str, tier: MemoryTier) -> MemoryEntry:
    return MemoryEntry(
        key=key,
        value=value,
        tier=tier,
        confidence=0.5,
        source=MemorySource.agent,
    )


def _conflict_profile(similarity_threshold: float) -> MemoryProfile:
    return MemoryProfile(
        name="conflict-test",
        layers=[LayerDefinition(name="pattern", half_life_days=60)],
        scoring=ScoringConfig(
            relevance=0.40,
            confidence=0.30,
            recency=0.15,
            frequency=0.15,
        ),
        conflict_check=ConflictCheckConfig(similarity_threshold=similarity_threshold),
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
    assert any(h.entry.key == "a" for h in hits)
    assert all(h.entry.key != "b" for h in hits)


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
    assert [h.entry.key for h in hits] == ["z", "y"]


def test_detect_save_conflicts_returns_similarity_scores() -> None:
    existing = [_entry("a", "alpha beta gamma", MemoryTier.pattern)]
    hits = detect_save_conflicts(
        "alpha beta gamma delta",
        MemoryTier.pattern.value,
        existing,
        similarity_threshold=0.2,
    )
    assert len(hits) == 1
    assert hits[0].entry.key == "a"
    assert 0.2 < hits[0].similarity <= 1.0


def test_format_save_conflict_reason_is_deterministic() -> None:
    r = format_save_conflict_reason(incoming_key="new-k", tier="pattern", similarity=0.81234567)
    assert r.startswith("Save-time conflict:")
    assert "new-k" in r
    assert "pattern" in r
    assert "0.8123" in r


def test_save_conflict_marks_contradicted_with_reason(tmp_path: Path) -> None:
    s = MemoryStore(tmp_path, profile=_conflict_profile(0.25))
    try:
        s.save("old", "the quick brown fox", tier="pattern", conflict_check=False)
        s.save("newer", "the quick brown fox jumps high", tier="pattern", conflict_check=True)
        old = s.get("old")
        assert old is not None
        assert old.invalid_at is not None
        assert old.contradicted is True
        assert old.contradiction_reason is not None
        assert "newer" in old.contradiction_reason
        assert "Save-time conflict" in old.contradiction_reason
    finally:
        s.close()
