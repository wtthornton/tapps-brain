"""Tests for memory garbage collection (Epic 24.4)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

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


class TestAppendToArchive:
    def test_writes_jsonl(self, tmp_path: Path) -> None:
        """Archived entries are written to a JSONL file."""
        archive_path = tmp_path / "archive.jsonl"
        entry = _make_entry(key="archived-key")

        MemoryGarbageCollector.append_to_archive([entry], archive_path)

        text = archive_path.read_text(encoding="utf-8")
        lines = [line for line in text.strip().splitlines() if line.strip()]
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["key"] == "archived-key"
        assert "archived_at" in data

    def test_appends_multiple(self, tmp_path: Path) -> None:
        """Multiple entries append to the same file."""
        archive_path = tmp_path / "archive.jsonl"
        entries = [_make_entry(key=f"key-{i}") for i in range(3)]

        MemoryGarbageCollector.append_to_archive(entries, archive_path)

        text = archive_path.read_text(encoding="utf-8")
        lines = [line for line in text.strip().splitlines() if line.strip()]
        assert len(lines) == 3
        # Each line is valid JSON with the expected keys
        keys_found = {json.loads(line)["key"] for line in lines}
        assert keys_found == {"key-0", "key-1", "key-2"}


class TestGCResult:
    def test_default_values(self) -> None:
        result = GCResult()
        assert result.archived_count == 0
        assert result.remaining_count == 0
        assert result.archived_keys == []
