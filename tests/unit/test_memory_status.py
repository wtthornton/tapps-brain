"""Tests for TAP-732: MemoryStatus lifecycle status fields.

Covers:
- MemoryStatus enum values
- MemoryEntry fields: status, stale_reason, stale_date
- GC skips stale entries
- brain_recall filters stale/superseded by default
- brain_remember supersession candidate detection
- brain_remember supersedes parameter
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from tapps_brain.gc import MemoryGarbageCollector
from tapps_brain.models import MemoryEntry, MemoryStatus
from tapps_brain.services import memory_service
from tests.factories import make_entry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _old_entry(key: str = "test-key", confidence: float = 0.05) -> MemoryEntry:
    """Entry whose confidence is deeply below floor — would be archived if active."""
    now = datetime.now(tz=UTC)
    old_ts = (now - timedelta(days=600)).isoformat()
    return make_entry(key=key, confidence=confidence, updated_at=old_ts)


def _stale_entry(key: str = "stale-key", confidence: float = 0.05) -> MemoryEntry:
    """Entry with status=stale and decayed confidence."""
    now = datetime.now(tz=UTC)
    old_ts = (now - timedelta(days=600)).isoformat()
    return make_entry(
        key=key,
        confidence=confidence,
        updated_at=old_ts,
        status=MemoryStatus.stale,
        stale_reason="This guidance is outdated.",
    )


# ---------------------------------------------------------------------------
# MemoryStatus enum
# ---------------------------------------------------------------------------


class TestMemoryStatusEnum:
    def test_values(self) -> None:
        assert MemoryStatus.active == "active"
        assert MemoryStatus.stale == "stale"
        assert MemoryStatus.superseded == "superseded"
        assert MemoryStatus.archived == "archived"

    def test_from_string(self) -> None:
        assert MemoryStatus("active") is MemoryStatus.active
        assert MemoryStatus("stale") is MemoryStatus.stale
        assert MemoryStatus("superseded") is MemoryStatus.superseded
        assert MemoryStatus("archived") is MemoryStatus.archived

    def test_invalid_string_raises(self) -> None:
        with pytest.raises(ValueError):
            MemoryStatus("unknown")


# ---------------------------------------------------------------------------
# MemoryEntry fields
# ---------------------------------------------------------------------------


class TestMemoryEntryStatusFields:
    def test_default_status_is_active(self) -> None:
        entry = make_entry()
        assert entry.status == MemoryStatus.active

    def test_stale_status(self) -> None:
        entry = make_entry(
            status=MemoryStatus.stale,
            stale_reason="Outdated",
            stale_date="2026-04-20T00:00:00+00:00",
        )
        assert entry.status == MemoryStatus.stale
        assert entry.stale_reason == "Outdated"
        assert entry.stale_date == "2026-04-20T00:00:00+00:00"

    def test_superseded_status(self) -> None:
        entry = make_entry(status=MemoryStatus.superseded, superseded_by="new-key")
        assert entry.status == MemoryStatus.superseded
        assert entry.superseded_by == "new-key"

    def test_stale_reason_optional(self) -> None:
        entry = make_entry(status=MemoryStatus.stale)
        assert entry.stale_reason is None
        assert entry.stale_date is None


# ---------------------------------------------------------------------------
# GC: stale entries survive
# ---------------------------------------------------------------------------


class TestGCSkipsStale:
    def setup_method(self) -> None:
        self.gc = MemoryGarbageCollector()

    def test_active_decayed_entry_is_candidate(self) -> None:
        entry = _old_entry(key="active-old")
        candidates = self.gc.identify_candidates([entry])
        assert any(e.key == "active-old" for e in candidates)

    def test_stale_entry_is_not_candidate(self) -> None:
        """stale entry must NEVER be auto-archived, even when confidence is floored."""
        entry = _stale_entry(key="stale-protected")
        candidates = self.gc.identify_candidates([entry])
        assert not any(e.key == "stale-protected" for e in candidates)

    def test_stale_entry_excluded_from_reasons(self) -> None:
        entry = _stale_entry()
        reasons = self.gc._archive_reasons(entry, datetime.now(tz=UTC))
        assert reasons == []

    def test_mixed_list_only_archives_active_decayed(self) -> None:
        active_old = _old_entry(key="active-old")
        stale_old = _stale_entry(key="stale-old")
        fresh = make_entry(key="fresh", confidence=0.9)

        candidates = self.gc.identify_candidates([active_old, stale_old, fresh])
        keys = {e.key for e in candidates}
        assert "active-old" in keys
        assert "stale-old" not in keys
        assert "fresh" not in keys

    def test_stale_candidate_details_excludes_stale(self) -> None:
        """stale_candidate_details should return no row for stale entries."""
        stale_old = _stale_entry(key="stale-no-detail")
        details = self.gc.stale_candidate_details([stale_old])
        assert not any(d.key == "stale-no-detail" for d in details)

    def test_superseded_entry_is_not_candidate(self) -> None:
        """superseded entries must also survive GC — they preserve audit history."""
        now = datetime.now(tz=UTC)
        old_ts = (now - timedelta(days=600)).isoformat()
        entry = make_entry(
            key="superseded-protected",
            confidence=0.05,
            updated_at=old_ts,
            status=MemoryStatus.superseded,
        )
        candidates = self.gc.identify_candidates([entry])
        assert not any(e.key == "superseded-protected" for e in candidates)

    def test_superseded_entry_excluded_from_reasons(self) -> None:
        """_archive_reasons returns [] for superseded entries."""
        now_dt = datetime.now(tz=UTC)
        old_ts = (now_dt - timedelta(days=600)).isoformat()
        entry = make_entry(
            key="sup-reasons",
            confidence=0.05,
            updated_at=old_ts,
            status=MemoryStatus.superseded,
        )
        reasons = self.gc._archive_reasons(entry, now_dt)
        assert reasons == []

    def test_mixed_list_skips_stale_and_superseded(self) -> None:
        """GC only archives active decayed entries; stale and superseded are both skipped."""
        now = datetime.now(tz=UTC)
        old_ts = (now - timedelta(days=600)).isoformat()
        active_old = _old_entry(key="active-old2")
        stale_old = _stale_entry(key="stale-old2")
        superseded_old = make_entry(
            key="superseded-old2", confidence=0.05, updated_at=old_ts,
            status=MemoryStatus.superseded,
        )
        candidates = self.gc.identify_candidates([active_old, stale_old, superseded_old])
        keys = {e.key for e in candidates}
        assert "active-old2" in keys
        assert "stale-old2" not in keys
        assert "superseded-old2" not in keys


# ---------------------------------------------------------------------------
# brain_recall filtering
# ---------------------------------------------------------------------------


class _MockStore:
    """Minimal store stub for service-layer tests."""

    def __init__(self, entries: list[MemoryEntry]) -> None:
        self._entries_map = {e.key: e for e in entries}

    def search(self, query: str, **_kwargs: Any) -> list[MemoryEntry]:
        return list(self._entries_map.values())

    def list_all(self) -> list[MemoryEntry]:
        return list(self._entries_map.values())

    def get(self, key: str) -> MemoryEntry | None:
        return self._entries_map.get(key)

    def save(self, **kwargs: Any) -> MemoryEntry:
        entry = make_entry(
            **{
                k: v
                for k, v in kwargs.items()
                if k not in {"skip_consolidation", "conflict_check", "dedup"}
            }
        )
        self._entries_map[entry.key] = entry
        return entry

    def delete(self, key: str) -> bool:
        return self._entries_map.pop(key, None) is not None


class TestBrainRecallFiltering:
    def _run_recall(
        self,
        entries: list[MemoryEntry],
        *,
        include_stale: bool = False,
        max_results: int = 10,
    ) -> list[dict[str, Any]]:
        store = _MockStore(entries)
        return memory_service.brain_recall(
            store,
            "proj",
            "agent",
            query="test",
            max_results=max_results,
            include_stale=include_stale,
        )

    def test_active_entries_returned(self) -> None:
        entries = [make_entry(key="active", value="active content")]
        results = self._run_recall(entries)
        assert len(results) == 1
        assert results[0]["key"] == "active"

    def test_stale_excluded_by_default(self) -> None:
        entries = [
            make_entry(key="active"),
            make_entry(key="stale", status=MemoryStatus.stale),
        ]
        results = self._run_recall(entries)
        keys = {r["key"] for r in results}
        assert "active" in keys
        assert "stale" not in keys

    def test_superseded_excluded_by_default(self) -> None:
        entries = [
            make_entry(key="active"),
            make_entry(key="old", status=MemoryStatus.superseded),
        ]
        results = self._run_recall(entries)
        keys = {r["key"] for r in results}
        assert "active" in keys
        assert "old" not in keys

    def test_archived_excluded_by_default(self) -> None:
        entries = [
            make_entry(key="active"),
            make_entry(key="archived", status=MemoryStatus.archived),
        ]
        results = self._run_recall(entries)
        keys = {r["key"] for r in results}
        assert "active" in keys
        assert "archived" not in keys

    def test_include_stale_returns_all(self) -> None:
        entries = [
            make_entry(key="active"),
            make_entry(key="stale", status=MemoryStatus.stale),
            make_entry(key="superseded", status=MemoryStatus.superseded),
        ]
        results = self._run_recall(entries, include_stale=True)
        keys = {r["key"] for r in results}
        assert keys == {"active", "stale", "superseded"}

    def test_include_stale_surfaces_status_field(self) -> None:
        entries = [make_entry(key="stale", status=MemoryStatus.stale, stale_reason="old")]
        results = self._run_recall(entries, include_stale=True)
        assert len(results) == 1
        assert results[0].get("status") == "stale"
        assert results[0].get("stale_reason") == "old"

    def test_active_entries_do_not_include_status_field(self) -> None:
        """active status should NOT appear in result dicts (not noise)."""
        entries = [make_entry(key="active")]
        results = self._run_recall(entries)
        assert "status" not in results[0]

    def test_max_results_applies_after_filter(self) -> None:
        entries = [
            make_entry(key="a1"),
            make_entry(key="a2"),
            make_entry(key="stale", status=MemoryStatus.stale),
        ]
        results = self._run_recall(entries, max_results=1)
        assert len(results) == 1


# ---------------------------------------------------------------------------
# brain_remember: supersession candidate detection
# ---------------------------------------------------------------------------


class TestSupersessionCandidateDetection:
    """Tests that brain_remember returns supersession_candidate when a prefix match is found."""

    def _run_remember(
        self,
        fact: str,
        existing: list[MemoryEntry],
        *,
        supersedes: str | None = None,
    ) -> dict[str, Any]:
        """Run brain_remember with a mock store and return the result."""

        class _Store(_MockStore):
            def save(inner_self, **kwargs: Any) -> MemoryEntry:  # noqa: N805
                key = kwargs.get("key", "test-key")
                status_val = kwargs.get("status", MemoryStatus.active)
                superseded_by_val = kwargs.get("superseded_by")
                entry = make_entry(
                    key=key,
                    value=kwargs.get("value", ""),
                    status=MemoryStatus(status_val) if isinstance(status_val, str) else status_val,
                    superseded_by=superseded_by_val,
                )
                inner_self._entries_map[key] = entry
                return entry

        store = _Store(existing)
        return memory_service.brain_remember(
            store,
            "proj",
            "agent",
            fact=fact,
            supersedes=supersedes,
        )

    def test_no_candidate_when_store_empty(self) -> None:
        result = self._run_remember("the best approach to X is Y", [])
        assert "supersession_candidate" not in result
        assert result["saved"] is True

    def test_candidate_detected_on_prefix_match(self) -> None:
        from tapps_brain.agent_brain import _content_key

        fact = "the best approach to X is Y"
        new_key = _content_key(fact)

        # Extract prefix (everything before the trailing -<16hexchars>)
        import re

        prefix = re.sub(r"-[0-9a-f]{16}$", "", new_key)

        # Make an existing entry whose key shares the prefix
        old_key = f"{prefix}-aabbccdd11223344"  # same prefix, different hash
        existing = [make_entry(key=old_key, value="old content")]

        result = self._run_remember(fact, existing)
        assert result.get("supersession_candidate") == old_key

    def test_no_candidate_when_existing_is_stale(self) -> None:
        """Stale entries are not returned as candidates."""
        import re

        from tapps_brain.agent_brain import _content_key

        fact = "the best approach to X is Y"
        new_key = _content_key(fact)
        prefix = re.sub(r"-[0-9a-f]{16}$", "", new_key)
        old_key = f"{prefix}-aabbccdd11223344"

        existing = [make_entry(key=old_key, status=MemoryStatus.stale)]
        result = self._run_remember(fact, existing)
        assert "supersession_candidate" not in result

    def test_no_candidate_when_existing_is_superseded(self) -> None:
        import re

        from tapps_brain.agent_brain import _content_key

        fact = "the best approach to X is Y"
        new_key = _content_key(fact)
        prefix = re.sub(r"-[0-9a-f]{16}$", "", new_key)
        old_key = f"{prefix}-aabbccdd11223344"

        existing = [make_entry(key=old_key, status=MemoryStatus.superseded)]
        result = self._run_remember(fact, existing)
        assert "supersession_candidate" not in result


# ---------------------------------------------------------------------------
# brain_remember: supersedes parameter
# ---------------------------------------------------------------------------


class TestSupersessionWorkflow:
    def _build_store(self, entries: list[MemoryEntry]) -> _MockStore:
        class _SaveTrackingStore(_MockStore):
            def __init__(inner_self, entries: list[MemoryEntry]) -> None:  # noqa: N805
                super().__init__(entries)
                inner_self.save_calls: list[dict[str, Any]] = []

            def save(inner_self, **kwargs: Any) -> MemoryEntry:  # noqa: N805
                inner_self.save_calls.append(dict(kwargs))
                key = kwargs.get("key", "unknown")
                status_raw = kwargs.get("status", "active")
                status = MemoryStatus(status_raw) if isinstance(status_raw, str) else status_raw
                superseded_by_val = kwargs.get("superseded_by")
                entry = make_entry(
                    key=key,
                    value=kwargs.get("value", ""),
                    status=status,
                    superseded_by=superseded_by_val,
                )
                inner_self._entries_map[key] = entry
                return entry

        return _SaveTrackingStore(entries)

    def test_supersedes_marks_old_entry_superseded(self) -> None:
        old_entry = make_entry(key="old-key", value="old guidance")
        store = self._build_store([old_entry])

        memory_service.brain_remember(
            store,
            "proj",
            "agent",
            fact="new improved guidance",
            supersedes="old-key",
        )

        # Find the save call for the old entry
        save_for_old = next(
            (c for c in store.save_calls if c.get("key") == "old-key"),
            None,
        )
        assert save_for_old is not None
        assert save_for_old["status"] == "superseded"

    def test_supersedes_sets_superseded_by_on_old(self) -> None:
        from tapps_brain.agent_brain import _content_key

        old_entry = make_entry(key="old-key", value="old guidance")
        store = self._build_store([old_entry])

        fact = "new improved guidance"
        new_key = _content_key(fact)

        memory_service.brain_remember(
            store,
            "proj",
            "agent",
            fact=fact,
            supersedes="old-key",
        )

        save_for_old = next(
            (c for c in store.save_calls if c.get("key") == "old-key"),
            None,
        )
        assert save_for_old is not None
        assert save_for_old.get("superseded_by") == new_key

    def test_supersedes_response_contains_superseded_key(self) -> None:
        old_entry = make_entry(key="old-key", value="old guidance")
        store = self._build_store([old_entry])

        result = memory_service.brain_remember(
            store,
            "proj",
            "agent",
            fact="new improved guidance",
            supersedes="old-key",
        )
        assert result["saved"] is True
        assert result["superseded"] == "old-key"

    def test_supersedes_nonexistent_key_does_not_raise(self) -> None:
        """Graceful handling when the supersedes target doesn't exist."""
        store = self._build_store([])
        result = memory_service.brain_remember(
            store,
            "proj",
            "agent",
            fact="new guidance",
            supersedes="nonexistent-key",
        )
        assert result["saved"] is True

    def test_supersession_candidate_not_returned_when_supersedes_given(self) -> None:
        """When supersedes is explicitly given, don't also return a candidate."""
        old_entry = make_entry(key="old-key", value="old guidance")
        store = self._build_store([old_entry])

        result = memory_service.brain_remember(
            store,
            "proj",
            "agent",
            fact="new improved guidance",
            supersedes="old-key",
        )
        assert "supersession_candidate" not in result
