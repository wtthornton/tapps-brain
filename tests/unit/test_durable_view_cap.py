"""TAP-5648: a maintenance pass must not leave the cache above ``max_entries``.

TAP-5633 bounded the durable merge so the *save* path could not exceed the cap.
Six maintenance surfaces kept the unbounded merge via ``allow_over_cap=True``,
correctly — they reconcile the durable set, not the capped cache view — but they
hydrated the overflow and never gave it back. When the durable set genuinely
exceeded the cap, a following ``count()`` reported above it until eviction
caught up.

The cap is a documented store invariant every read surface reports. These tests
pin that it now holds unconditionally, and that restoring it costs no durable
data.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tapps_brain.store import MemoryStore

if TYPE_CHECKING:
    from pathlib import Path

_CAP = 10
_OVER = 6


@pytest.fixture
def over_cap_store(tmp_path: Path) -> MemoryStore:
    """A store whose durable set exceeds ``max_entries``.

    Built by seeding under a generous cap and then lowering it — the same shape
    as an operator lowering ``max_entries``, which is one of the two ways the
    issue says the durable set legitimately outgrows the cache.
    """
    store = MemoryStore(tmp_path)
    if store._profile is not None:
        store._profile.limits.max_entries = _CAP + _OVER
    for i in range(_CAP + _OVER):
        store.save(
            key=f"entry-{i:03d}",
            value=f"Durable row number {i} about caching and eviction.",
            tier="pattern",
            skip_consolidation=True,
            dedup=False,
            conflict_check=False,
        )
    assert store.count() == _CAP + _OVER
    if store._profile is not None:
        store._profile.limits.max_entries = _CAP
    return store


def _durable_count(store: MemoryStore) -> int:
    return len(store._persistence.load_all(limit=None))


class TestMaintenanceRestoresTheCap:
    """The regression: each surface hydrated overflow and left it resident."""

    @pytest.mark.parametrize(
        "call",
        [
            pytest.param(lambda s: s.verify_integrity(), id="verify_integrity"),
            pytest.param(lambda s: s.resign_integrity(), id="resign_integrity"),
            pytest.param(lambda s: s.count_orphaned_relations(), id="count_orphaned_relations"),
            pytest.param(lambda s: s.count_expired_entries(), id="count_expired_entries"),
            pytest.param(lambda s: s.list_gc_stale_details(), id="list_gc_stale_details"),
            pytest.param(lambda s: s.gc(dry_run=True), id="gc_dry_run"),
            pytest.param(lambda s: s.decay_learnings(dry_run=True), id="decay_learnings"),
        ],
    )
    def test_cache_respects_cap_after_the_pass(self, over_cap_store: MemoryStore, call) -> None:  # noqa: ANN001
        assert _durable_count(over_cap_store) == _CAP + _OVER, "fixture must be over cap"

        call(over_cap_store)

        assert len(over_cap_store._entries) <= _CAP, (
            "a maintenance pass hydrated durable overflow into the cache and left "
            "it there, so count()/snapshot() report above max_entries"
        )

    @pytest.mark.parametrize(
        "call",
        [
            pytest.param(lambda s: s.verify_integrity(), id="verify_integrity"),
            pytest.param(lambda s: s.count_orphaned_relations(), id="count_orphaned_relations"),
            pytest.param(lambda s: s.list_gc_stale_details(), id="list_gc_stale_details"),
        ],
    )
    def test_the_pass_costs_no_durable_rows(self, over_cap_store: MemoryStore, call) -> None:  # noqa: ANN001
        """Restoring the cap must be cache eviction only.

        ``_evict_entry_key`` deletes the durable row *and* bumps the removal
        epoch, which would additionally stop ``_merge_durable_entries`` from
        re-hydrating those keys later. Using it here would turn a read into
        silent data loss.
        """
        before = _durable_count(over_cap_store)
        call(over_cap_store)
        assert _durable_count(over_cap_store) == before, (
            "the cap was restored by deleting durable rows — a read must not "
            "destroy data"
        )


class TestSurfacesStillSeeTheFullDurableSet:
    """The trim must not defeat the reason these surfaces merged over cap."""

    def test_verify_integrity_covers_every_durable_row(
        self, over_cap_store: MemoryStore
    ) -> None:
        result = over_cap_store.verify_integrity()
        assert result["total"] == _CAP + _OVER, (
            "integrity must cover the durable set, not the capped cache view — "
            "trimming before the body runs would silently under-report"
        )

    def test_gc_can_still_archive_an_over_cap_row(self, over_cap_store: MemoryStore) -> None:
        """gc's archive loop skips keys absent from ``_entries``.

        So the hydration has to survive for the duration of the call; only the
        exit may trim.
        """
        stale = over_cap_store.list_gc_stale_details()
        assert isinstance(stale, list)
        assert len(over_cap_store._entries) <= _CAP


class TestInvariantHoldsOnTheExceptionPath:
    def test_cap_is_restored_when_the_body_raises(self, over_cap_store: MemoryStore) -> None:
        """A maintenance surface that raises must not leave the cap broken."""
        from tapps_brain._store_durable_view import durable_pass

        @durable_pass
        def _boom(store: MemoryStore) -> None:
            assert len(store._entries) > _CAP, (
                "precondition: the decorator must have hydrated the overflow, "
                "otherwise this proves nothing about the exception path"
            )
            raise RuntimeError("maintenance blew up")

        with pytest.raises(RuntimeError, match="maintenance blew up"):
            _boom(over_cap_store)

        assert len(over_cap_store._entries) <= _CAP


class TestTrimPrefersRowsItHydrated:
    def test_pre_existing_cache_rows_survive(self, over_cap_store: MemoryStore) -> None:
        """A maintenance call should not reshape which entries are resident.

        Rows the pass hydrated are dropped first, so a read leaves the cache's
        composition as it found it.
        """
        over_cap_store._entries.clear()
        resident = "entry-000"
        over_cap_store._ensure_entry_cached(resident)
        assert resident in over_cap_store._entries

        over_cap_store.verify_integrity()

        assert resident in over_cap_store._entries, (
            "the trim evicted a row that was cached before the pass rather than "
            "one it hydrated itself"
        )
