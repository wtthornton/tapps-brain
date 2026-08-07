"""Undo for save-time conflict invalidation (TAP-5782).

Save-time conflict detection marks an entry ``contradicted=True``, which removes
it from recall. Before this, that was a one-way door: ``consolidation-merge-undo``
only understands consolidation merges, and re-saving the key preserves the flag.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tapps_brain.contradictions import (
    format_save_conflict_reason,
    is_save_conflict_reason,
)
from tapps_brain.store import MemoryStore

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


@pytest.fixture
def store(tmp_path: Path) -> Generator[MemoryStore, None, None]:
    s = MemoryStore(tmp_path)
    yield s
    s.close()


def _invalidate(store: MemoryStore, key: str, *, incoming: str = "incoming-key") -> str:
    """Mark *key* contradicted exactly as save-time conflict detection does."""
    reason = format_save_conflict_reason(incoming_key=incoming, tier="procedural", similarity=0.64)
    store.update_fields(key, contradicted=True, contradiction_reason=reason)
    return reason


class TestReasonMarker:
    def test_formatted_reason_is_recognised(self) -> None:
        reason = format_save_conflict_reason(incoming_key="k", tier="procedural", similarity=0.6421)
        assert is_save_conflict_reason(reason)
        assert "similarity=0.6421" in reason

    @pytest.mark.parametrize(
        "reason",
        [
            None,
            "",
            "consolidated into linkedin-publish-c6c49bb0",
            "Doc validation: contradicts docs/adr/ADR-007.md",
            "manually superseded by an operator",
        ],
    )
    def test_other_reasons_are_not_save_conflicts(self, reason: str | None) -> None:
        assert not is_save_conflict_reason(reason)


class TestUndoSaveConflict:
    def test_restores_a_conflict_invalidated_entry(self, store: MemoryStore) -> None:
        store.save(key="victim", value="A distinct fact.", tier="procedural")
        _invalidate(store, "victim")
        assert store.get("victim").contradicted is True

        result = store.undo_save_conflict("victim")

        assert result["ok"] is True
        assert result["restored"] is True
        entry = store.get("victim")
        assert entry.contradicted is False
        assert entry.contradiction_reason is None

    def test_restored_entry_returns_to_ranked_recall(self, store: MemoryStore) -> None:
        """The point of the undo — contradicted entries are hidden from recall until restored.

        Both ``MemoryStore.search`` and ``MemoryRetriever.search`` filter contradicted
        entries by default (TAP-5783), so an entry that was contradicted is not found
        in ranked recall until its contradicted flag is cleared.
        """
        from tapps_brain.retrieval import MemoryRetriever

        store.save(
            key="observability-brief",
            value="Agent observability matters more than most teams think.",
            tier="procedural",
        )
        retriever = MemoryRetriever()

        _invalidate(store, "observability-brief")
        hidden = {r.entry.key for r in retriever.search("agent observability teams", store)}
        assert "observability-brief" not in hidden

        store.undo_save_conflict("observability-brief")

        found = {r.entry.key for r in retriever.search("agent observability teams", store)}
        assert "observability-brief" in found

    def test_store_search_filters_contradicted_by_default(self, store: MemoryStore) -> None:
        """Contradicted entries are filtered by MemoryStore.search by default (TAP-5783).

        This matches the behavior of ``MemoryRetriever.search`` so both surfaces
        agree on default filtering. Callers can opt-in with ``include_contradicted=True``
        to see all entries including contradicted ones.
        """
        store.save(key="filtered", value="A distinct fact.", tier="procedural")
        _invalidate(store, "filtered")

        # By default, contradicted entries are excluded
        assert "filtered" not in {e.key for e in store.search("distinct fact")}

        # With include_contradicted=True, the entry is visible
        assert "filtered" in {
            e.key for e in store.search("distinct fact", include_contradicted=True)
        }

    def test_preserves_every_other_field(self, store: MemoryStore) -> None:
        store.save(
            key="preserve-me",
            value="Original body text.",
            tier="procedural",
            tags=["alpha", "beta"],
            confidence=0.83,
        )
        _invalidate(store, "preserve-me")
        # Captured AFTER invalidation: the undo must not move anything from the
        # state it found, and the invalidation itself re-stamps updated_at.
        before = store.get("preserve-me")

        store.undo_save_conflict("preserve-me")

        after = store.get("preserve-me")
        assert after.value == before.value
        assert str(after.tier) == str(before.tier)
        assert after.tags == before.tags
        assert after.confidence == pytest.approx(before.confidence)
        # A recovery must not inflate the recency ranking signal.
        assert after.updated_at == before.updated_at
        assert after.created_at == before.created_at

    def test_dry_run_reports_without_writing(self, store: MemoryStore) -> None:
        store.save(key="preview", value="Body.", tier="procedural")
        _invalidate(store, "preview")

        result = store.undo_save_conflict("preview", dry_run=True)

        assert result["ok"] is True
        assert result["restored"] is False
        assert result["dry_run"] is True
        assert store.get("preview").contradicted is True

    def test_refuses_a_consolidation_source(self, store: MemoryStore) -> None:
        """A consolidation source carries superseded_by linkage this cannot unwind."""
        store.save(key="merged-source", value="Body.", tier="procedural")
        store.update_fields(
            "merged-source",
            contradicted=True,
            contradiction_reason="consolidated into some-merged-key",
            superseded_by="some-merged-key",
        )

        result = store.undo_save_conflict("merged-source")

        assert result["ok"] is False
        assert result["reason"] == "not_a_save_conflict"
        assert result["restored"] is False
        entry = store.get("merged-source")
        assert entry.contradicted is True
        assert entry.superseded_by == "some-merged-key"

    def test_reports_not_contradicted(self, store: MemoryStore) -> None:
        store.save(key="healthy", value="Body.", tier="procedural")
        result = store.undo_save_conflict("healthy")
        assert result["ok"] is False
        assert result["reason"] == "not_contradicted"

    def test_reports_not_found(self, store: MemoryStore) -> None:
        result = store.undo_save_conflict("no-such-key")
        assert result["ok"] is False
        assert result["reason"] == "not_found"

    def test_writes_an_audit_row(self, store: MemoryStore) -> None:
        store.save(key="audited", value="Body.", tier="procedural")
        reason = _invalidate(store, "audited")

        store.undo_save_conflict("audited")

        rows = store._persistence.query_audit(key="audited", event_type="save_conflict_undo")
        assert rows, "expected a save_conflict_undo audit row"
        assert (rows[-1].get("details") or {}).get("prior_contradiction_reason") == reason

    def test_increments_a_metric(self, store: MemoryStore) -> None:
        store.save(key="metered", value="Body.", tier="procedural")
        _invalidate(store, "metered")
        before = store.get_metrics().counters.get("store.save_conflict_undo", 0)

        store.undo_save_conflict("metered")

        after = store.get_metrics().counters.get("store.save_conflict_undo", 0)
        assert after == before + 1

    def test_is_idempotent(self, store: MemoryStore) -> None:
        """A second run is a no-op that reports why, not a crash."""
        store.save(key="twice", value="Body.", tier="procedural")
        _invalidate(store, "twice")

        assert store.undo_save_conflict("twice")["restored"] is True
        second = store.undo_save_conflict("twice")
        assert second["ok"] is False
        assert second["reason"] == "not_contradicted"
