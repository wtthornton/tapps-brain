"""Observability integration tests (STORY-007.6).

Perform mixed operations on a real MemoryStore + SQLite, then verify:
- Metrics snapshot reflects all operations
- Audit trail records correct event sequence
- Health report reflects actual store state
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tapps_brain.store import MemoryStore

if TYPE_CHECKING:
    from pathlib import Path


class TestMetricsAccumulation:
    """50 mixed operations should all appear in the metrics snapshot."""

    def test_mixed_operations_metrics(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)

        # 20 saves
        for i in range(20):
            store.save(key=f"m-{i}", value=f"Memory about topic {i}", tier="pattern")

        # 10 gets (mix of hit and miss)
        for i in range(10):
            store.get(f"m-{i}")  # hits
        for i in range(5):
            store.get(f"nonexistent-{i}")  # misses

        # 5 searches
        for i in range(5):
            store.search(f"topic {i}")

        # 5 recalls
        for i in range(5):
            store.recall(f"What is topic {i}?")

        # 3 supersedes
        for i in range(3):
            store.supersede(f"m-{i}", f"Updated memory {i}")

        # 2 GC dry runs
        for _ in range(2):
            store.gc(dry_run=True)

        snap = store.get_metrics()

        # Verify counters (save count includes supersede's internal saves)
        assert snap.counters["store.save"] >= 20
        assert snap.counters["store.get"] == 15  # 10 hits + 5 misses
        assert snap.counters["store.get.hit"] == 10
        assert snap.counters["store.get.miss"] == 5
        # search count >= 5 because recall also triggers internal searches
        assert snap.counters["store.search"] >= 5
        assert snap.counters["store.recall"] == 5
        assert snap.counters["store.supersede"] == 3
        assert snap.counters["store.gc"] == 2

        # Verify latency histograms exist and have correct counts
        assert snap.histograms["store.save_ms"].count >= 20
        assert snap.histograms["store.get_ms"].count == 15
        assert snap.histograms["store.search_ms"].count >= 5
        assert snap.histograms["store.recall_ms"].count == 5

        # All latencies should be positive
        for name, stats in snap.histograms.items():
            assert stats.min > 0, f"{name} has non-positive min"

        store.close()


class TestAuditTrailIntegration:
    """Audit trail should reflect the actual mutation sequence."""

    def test_audit_after_mutations(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)

        store.save(key="alpha", value="first version")
        store.save(key="beta", value="another entry")
        store.delete("beta")
        store.supersede("alpha", "second version")

        # Query all audit entries
        all_entries = store.audit(limit=100)
        assert len(all_entries) >= 4  # at least save, save, delete, save+update

        # Query by key
        alpha_entries = store.audit(key="alpha")
        assert len(alpha_entries) >= 1
        assert all(e.key == "alpha" for e in alpha_entries)

        # Query by event type
        deletes = store.audit(event_type="delete")
        assert len(deletes) >= 1
        assert deletes[0].key == "beta"

        store.close()

    def test_audit_time_range(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)

        store.save(key="t1", value="timed entry")

        # All entries should be within a wide window
        entries = store.audit(since="2020-01-01", until="2099-12-31")
        assert len(entries) >= 1

        # No entries in the future
        future = store.audit(since="2099-01-01")
        assert len(future) == 0

        store.close()


class TestHealthReportIntegration:
    """Health report should reflect real store state."""

    def test_health_near_capacity(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)

        # Save enough entries to have meaningful stats
        for i in range(50):
            tier = ["architectural", "pattern", "procedural", "context"][i % 4]
            store.save(key=f"h-{i}", value=f"Health test entry {i}", tier=tier)

        health = store.health()

        assert health.entry_count == 50
        assert health.max_entries == 5000
        assert health.schema_version >= 1
        assert health.store_path == str(tmp_path)

        # Tier distribution should have all 4 tiers
        assert len(health.tier_distribution) == 4
        for tier_name in ["architectural", "pattern", "procedural", "context"]:
            assert tier_name in health.tier_distribution

        # Oldest entry age should be very small (just created)
        assert health.oldest_entry_age_days < 1.0
        assert health.sqlcipher_enabled is False

        store.close()

    def test_health_empty_store(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        health = store.health()
        assert health.entry_count == 0
        assert health.tier_distribution == {}
        assert health.sqlcipher_enabled is False
        store.close()
