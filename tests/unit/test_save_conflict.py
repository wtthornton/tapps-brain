"""Unit tests for ``_save_conflict.py`` — conflict detection helpers.

Exercises :func:`resolve_similarity_threshold` with and without a profile,
and :func:`plan_conflicts` across key branches: no hits, hits with active
entries, and hits whose entries are already invalidated.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tapps_brain._save_conflict import ConflictPlan, plan_conflicts, resolve_similarity_threshold
from tapps_brain.models import MemoryEntry, MemoryTier

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entry(
    key: str, value: str, tier: MemoryTier = MemoryTier.pattern, invalid_at: str | None = None
) -> MemoryEntry:
    """Minimal MemoryEntry for tabletop conflict fixtures."""
    return MemoryEntry(key=key, value=value, tier=tier, invalid_at=invalid_at)


def _conflict_hit(entry: MemoryEntry, similarity: float = 0.9) -> MagicMock:
    """Build a SaveConflictHit-like mock for patching detect_save_conflicts."""
    hit = MagicMock()
    hit.entry = entry
    hit.similarity = similarity
    return hit


# ---------------------------------------------------------------------------
# resolve_similarity_threshold
# ---------------------------------------------------------------------------


class TestResolveSimilarityThreshold:
    """Return the effective threshold from the profile or the default."""

    def test_no_profile_returns_default(self) -> None:
        threshold = resolve_similarity_threshold(None)
        # ConflictCheckConfig default is 0.6 (medium aggressiveness)
        assert isinstance(threshold, float)
        assert 0.0 < threshold <= 1.0

    def test_profile_without_conflict_check_returns_default(self) -> None:
        profile = MagicMock()
        profile.conflict_check = None
        threshold = resolve_similarity_threshold(profile)
        assert isinstance(threshold, float)
        assert 0.0 < threshold <= 1.0

    def test_profile_with_conflict_check_returns_profile_value(self) -> None:
        conflict_cfg = MagicMock()
        conflict_cfg.effective_similarity_threshold.return_value = 0.75
        profile = MagicMock()
        profile.conflict_check = conflict_cfg

        threshold = resolve_similarity_threshold(profile)
        assert threshold == pytest.approx(0.75)

    def test_profile_conflict_check_effective_called(self) -> None:
        conflict_cfg = MagicMock()
        conflict_cfg.effective_similarity_threshold.return_value = 0.5
        profile = MagicMock()
        profile.conflict_check = conflict_cfg

        resolve_similarity_threshold(profile)
        conflict_cfg.effective_similarity_threshold.assert_called_once()


# ---------------------------------------------------------------------------
# plan_conflicts — no conflicts
# ---------------------------------------------------------------------------


class TestPlanConflictsNoHits:
    """When detect_save_conflicts returns an empty list, plan_conflicts returns None."""

    def test_no_entries_returns_none(self) -> None:
        with patch("tapps_brain.contradictions.detect_save_conflicts", return_value=[]):
            result = plan_conflicts(
                key="new-key",
                value="some value",
                tier="pattern",
                entries_snapshot=[],
                similarity_threshold=0.6,
                now="2026-01-01T00:00:00Z",
            )
        assert result is None

    def test_low_similarity_entries_returns_none(self) -> None:
        """Even with entries present, no conflicts means None."""
        entry = _entry("old-key", "very different content")
        with patch("tapps_brain.contradictions.detect_save_conflicts", return_value=[]):
            result = plan_conflicts(
                key="new-key",
                value="completely unrelated",
                tier="pattern",
                entries_snapshot=[entry],
                similarity_threshold=0.6,
                now="2026-01-01T00:00:00Z",
            )
        assert result is None


# ---------------------------------------------------------------------------
# plan_conflicts — active conflicts
# ---------------------------------------------------------------------------


class TestPlanConflictsWithHits:
    """Active conflicting entries are included in the returned plan."""

    def test_single_active_conflict(self) -> None:
        existing = _entry("old-key", "similar content", invalid_at=None)
        hit = _conflict_hit(existing, similarity=0.85)

        with patch("tapps_brain.contradictions.detect_save_conflicts", return_value=[hit]):
            result = plan_conflicts(
                key="new-key",
                value="similar content v2",
                tier="pattern",
                entries_snapshot=[existing],
                similarity_threshold=0.6,
                now="2026-01-01T00:00:00Z",
            )

        assert result is not None
        assert isinstance(result, ConflictPlan)
        assert result.now == "2026-01-01T00:00:00Z"
        assert "old-key" in result.conflict_keys
        assert result.similarity_threshold == 0.6

    def test_invalidations_list_contains_conflict_key(self) -> None:
        existing = _entry("dup-key", "duplicate", invalid_at=None)
        hit = _conflict_hit(existing, similarity=0.9)

        with patch("tapps_brain.contradictions.detect_save_conflicts", return_value=[hit]):
            result = plan_conflicts(
                key="new-key",
                value="similar",
                tier="pattern",
                entries_snapshot=[existing],
                similarity_threshold=0.6,
                now="2026-01-01T12:00:00Z",
            )

        assert result is not None
        invalidation_keys = [k for k, _ in result.invalidations]
        assert "dup-key" in invalidation_keys

    def test_audit_list_populated(self) -> None:
        existing = _entry("audit-key", "content", invalid_at=None)
        hit = _conflict_hit(existing, similarity=0.88)

        with patch("tapps_brain.contradictions.detect_save_conflicts", return_value=[hit]):
            result = plan_conflicts(
                key="new-key",
                value="similar",
                tier="pattern",
                entries_snapshot=[existing],
                similarity_threshold=0.6,
                now="2026-01-01T00:00:00Z",
            )

        assert result is not None
        assert len(result.audit) == 1
        audit_row = result.audit[0]
        assert audit_row["key"] == "audit-key"
        assert "similarity" in audit_row

    def test_multiple_conflicts(self) -> None:
        e1 = _entry("key-1", "content a", invalid_at=None)
        e2 = _entry("key-2", "content b", invalid_at=None)
        hits = [_conflict_hit(e1, 0.9), _conflict_hit(e2, 0.8)]

        with patch("tapps_brain.contradictions.detect_save_conflicts", return_value=hits):
            result = plan_conflicts(
                key="new-key",
                value="content",
                tier="pattern",
                entries_snapshot=[e1, e2],
                similarity_threshold=0.6,
                now="2026-01-01T00:00:00Z",
            )

        assert result is not None
        assert len(result.conflict_keys) == 2
        assert len(result.audit) == 2


# ---------------------------------------------------------------------------
# plan_conflicts — already-invalidated entries are skipped
# ---------------------------------------------------------------------------


class TestPlanConflictsInvalidatedEntries:
    """Entries that are already invalidated are excluded from ``invalidations``."""

    def test_invalidated_entry_not_in_invalidations(self) -> None:
        already_invalid = _entry("old-key", "content", invalid_at="2025-12-01T00:00:00Z")
        hit = _conflict_hit(already_invalid, similarity=0.95)

        with patch("tapps_brain.contradictions.detect_save_conflicts", return_value=[hit]):
            result = plan_conflicts(
                key="new-key",
                value="similar content",
                tier="pattern",
                entries_snapshot=[already_invalid],
                similarity_threshold=0.6,
                now="2026-01-01T00:00:00Z",
            )

        # Hit is present (returned by detect_save_conflicts), but the entry is
        # already invalidated so it should not appear in invalidations.
        assert result is not None
        assert result.conflict_keys == ["old-key"]  # still in audit
        invalidation_keys = [k for k, _ in result.invalidations]
        assert "old-key" not in invalidation_keys

    def test_mixed_active_and_invalidated(self) -> None:
        active = _entry("active-key", "content", invalid_at=None)
        stale = _entry("stale-key", "content", invalid_at="2025-01-01T00:00:00Z")
        hits = [_conflict_hit(active, 0.9), _conflict_hit(stale, 0.85)]

        with patch("tapps_brain.contradictions.detect_save_conflicts", return_value=hits):
            result = plan_conflicts(
                key="new-key",
                value="similar",
                tier="pattern",
                entries_snapshot=[active, stale],
                similarity_threshold=0.6,
                now="2026-01-01T00:00:00Z",
            )

        assert result is not None
        invalidation_keys = [k for k, _ in result.invalidations]
        assert "active-key" in invalidation_keys
        assert "stale-key" not in invalidation_keys
