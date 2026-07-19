"""Tests for memory garbage collection (Epic 24.4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    pass

from tapps_brain.decay import DecayConfig
from tapps_brain.gc import GCResult, MemoryGarbageCollector
from tapps_brain.models import MemoryEntry, MemoryScope, MemoryTier
from tests.factories import make_entry


def _make_entry(
    *,
    key: str = "test-key",
    tier: MemoryTier = MemoryTier.pattern,
    confidence: float = 0.8,
    updated_at: str | None = None,
    scope: MemoryScope = MemoryScope.project,
    contradicted: bool = False,
) -> MemoryEntry:
    """Helper to create a MemoryEntry with controlled state."""
    return make_entry(
        key=key,
        tier=tier,
        confidence=confidence,
        updated_at=updated_at,
        scope=scope,
        contradicted=contradicted,
        contradiction_reason="test" if contradicted else None,
    )


@pytest.fixture
def config() -> DecayConfig:
    return DecayConfig()


@pytest.fixture
def gc(config: DecayConfig) -> MemoryGarbageCollector:
    return MemoryGarbageCollector(config)


class TestIdentifyCandidates:
    def test_deeply_decayed_memory_archived(self, gc: MemoryGarbageCollector) -> None:
        """Memory at confidence floor for 30+ days gets archived."""
        now = datetime.now(tz=UTC)
        # Pattern half-life is 60 days. At ~600 days, confidence is deeply floored.
        old_update = (now - timedelta(days=600)).isoformat()
        entry = _make_entry(confidence=0.8, updated_at=old_update)

        candidates = gc.identify_candidates([entry], now=now)
        assert len(candidates) == 1
        assert candidates[0].key == "test-key"

    def test_contradicted_low_confidence_archived(self, gc: MemoryGarbageCollector) -> None:
        """Contradicted memory with low effective confidence gets archived."""
        now = datetime.now(tz=UTC)
        # Make it old enough that effective confidence < 0.2
        old_update = (now - timedelta(days=180)).isoformat()
        entry = _make_entry(confidence=0.5, updated_at=old_update, contradicted=True)

        candidates = gc.identify_candidates([entry], now=now)
        assert len(candidates) == 1

    def test_above_threshold_survives(self, gc: MemoryGarbageCollector) -> None:
        """A reasonably fresh memory is NOT a GC candidate."""
        now = datetime.now(tz=UTC)
        entry = _make_entry(confidence=0.8)

        candidates = gc.identify_candidates([entry], now=now)
        assert len(candidates) == 0

    def test_session_scoped_expired(self, gc: MemoryGarbageCollector) -> None:
        """Session-scoped memory older than 7 days gets archived."""
        now = datetime.now(tz=UTC)
        old_update = (now - timedelta(days=10)).isoformat()
        entry = _make_entry(scope=MemoryScope.session, updated_at=old_update)

        candidates = gc.identify_candidates([entry], now=now)
        assert len(candidates) == 1

    def test_session_scoped_fresh_survives(self, gc: MemoryGarbageCollector) -> None:
        """Recent session-scoped memory is NOT archived."""
        now = datetime.now(tz=UTC)
        entry = _make_entry(scope=MemoryScope.session)

        candidates = gc.identify_candidates([entry], now=now)
        assert len(candidates) == 0


class TestStaleCandidateDetails:
    def test_matches_identify_candidates(self, gc: MemoryGarbageCollector) -> None:
        now = datetime.now(tz=UTC)
        old_update = (now - timedelta(days=600)).isoformat()
        entry = _make_entry(confidence=0.8, updated_at=old_update)
        fresh = _make_entry(key="fresh", confidence=0.9)

        cands = gc.identify_candidates([entry, fresh], now=now)
        details = gc.stale_candidate_details([entry, fresh], now=now)
        assert len(details) == len(cands) == 1
        assert details[0].key == entry.key
        assert "floor_retention" in details[0].reasons

    def test_session_reason_metadata(self, gc: MemoryGarbageCollector) -> None:
        now = datetime.now(tz=UTC)
        old_update = (now - timedelta(days=10)).isoformat()
        entry = _make_entry(scope=MemoryScope.session, updated_at=old_update)
        details = gc.stale_candidate_details([entry], now=now)
        assert len(details) == 1
        assert "session_expired" in details[0].reasons
        assert details[0].days_since_update is not None
        assert details[0].days_since_update >= 10.0

    def test_stale_details_default_now(self, gc: MemoryGarbageCollector) -> None:
        """``now=None`` uses UTC now (covers default branch)."""
        entry = _make_entry(confidence=0.9)
        details = gc.stale_candidate_details([entry])
        assert details == []


class TestGCResult:
    def test_default_values(self) -> None:
        result = GCResult()
        assert result.archived_count == 0
        assert result.remaining_count == 0
        assert result.archived_keys == []
        assert result.dry_run is False
        assert result.reason_counts == {}
        assert result.archive_bytes == 0
        assert result.estimated_archive_bytes == 0


class TestGcReasonAggregation:
    def test_aggregate_gc_reason_counts(self) -> None:
        from tapps_brain.gc import StaleCandidateDetail, aggregate_gc_reason_counts

        details = [
            StaleCandidateDetail(
                key="a",
                tier="context",
                reasons=["session_expired"],
                effective_confidence=0.1,
                stored_confidence=0.5,
                contradicted=False,
                scope="session",
            ),
            StaleCandidateDetail(
                key="b",
                tier="pattern",
                reasons=["floor_retention", "contradicted_low_confidence"],
                effective_confidence=0.02,
                stored_confidence=0.1,
                contradicted=True,
                scope="project",
            ),
        ]
        assert aggregate_gc_reason_counts(details) == {
            "contradicted_low_confidence": 1,
            "floor_retention": 1,
            "session_expired": 1,
        }


def test_floor_retention_uses_layer_confidence_floor() -> None:
    """Architectural entries at their layer floor must still qualify for archival."""
    from datetime import UTC, datetime, timedelta

    from tapps_brain.decay import DecayConfig
    from tapps_brain.gc import MemoryGarbageCollector
    from tapps_brain.models import MemoryEntry, MemorySource, MemoryTier

    dcfg = DecayConfig(
        confidence_floor=0.05,
        layer_confidence_floors={
            "architectural": 0.10,
            "pattern": 0.10,
            "procedural": 0.10,
            "context": 0.05,
        },
    )
    gc = MemoryGarbageCollector(config=dcfg, floor_retention_days=30)
    old = (datetime.now(tz=UTC) - timedelta(days=2000)).isoformat()
    entry = MemoryEntry(
        key="arch-old",
        value="decision",
        tier=MemoryTier.architectural,
        source=MemorySource.human,
        confidence=0.9,
        created_at=old,
        updated_at=old,
    )
    reasons = gc._archive_reasons(entry, datetime.now(tz=UTC))
    assert "floor_retention" in reasons


def test_gc_demotes_instead_of_archiving_when_layer_defines_demotion_to(tmp_path) -> None:
    """EPIC-010: floor-retention candidates with a demotion target move down a tier.

    Previously check_demotion had no production caller — every builtin
    profile's demotion_to was dead configuration and stale entries went
    straight to gc_archive.
    """
    from datetime import UTC, datetime, timedelta

    from tapps_brain.models import MemoryTier
    from tapps_brain.store import MemoryStore

    store = MemoryStore(tmp_path)
    try:
        assert store.profile is not None  # repo-brain default: architectural -> pattern

        store.save(key="old-arch", value="ancient decision", tier="architectural")
        old = (datetime.now(tz=UTC) - timedelta(days=2000)).isoformat()
        entry = store._entries["old-arch"]
        stale = entry.model_copy(
            update={
                "created_at": old,
                "updated_at": old,
                "last_accessed": old,
                "confidence": 0.2,
            }
        )
        store._entries["old-arch"] = stale
        store._persistence.save(stale)

        dry = store.gc(dry_run=True)
        assert "old-arch" in dry.demoted_keys
        assert "old-arch" not in dry.archived_keys
        # Dry run applies nothing.
        assert store._entries["old-arch"].tier == MemoryTier.architectural

        live = store.gc()
        assert "old-arch" in live.demoted_keys
        assert live.demoted_count == 1
        assert "old-arch" not in live.archived_keys
        assert store._entries["old-arch"].tier == MemoryTier.pattern
    finally:
        store.close()
