"""Performance benchmarks for tapps-brain core operations.

Run with: pytest tests/benchmarks/ -v --benchmark-only
Or:       pytest tests/benchmarks/ -v --benchmark-sort=mean

Story: STORY-002.6 from EPIC-002
"""

from __future__ import annotations

import random
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from tapps_brain.bm25 import BM25Scorer
from tapps_brain.decay import DecayConfig, calculate_decayed_confidence
from tapps_brain.retrieval import MemoryRetriever
from tapps_brain.store import MemoryStore
from tests.factories import make_entry

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.benchmark


# ---------------------------------------------------------------------------
# Store CRUD benchmarks
# ---------------------------------------------------------------------------


class TestStoreSaveBenchmark:
    def test_save_500_entries(self, benchmark, tmp_path: Path) -> None:
        """Benchmark: 500 sequential saves to a fresh store.

        Note: pytest-benchmark invokes the benchmarked function multiple rounds
        for statistical accuracy.  The first round performs inserts; subsequent
        rounds perform updates (same deterministic keys already exist).  Both
        write paths exercise the WAL/FTS5 pipeline and are valid benchmarks.
        """
        store = MemoryStore(tmp_path)
        try:

            def do_saves() -> None:
                for i in range(500):
                    store.save(
                        key=f"key-{i:04d}",
                        value=f"Value for entry {i} with enough content for indexing",
                        tier="pattern",
                        source="agent",
                        tags=[f"tag-{i % 10}"],
                        confidence=0.7,
                    )

            benchmark(do_saves)
        finally:
            store.close()


class TestSaveManyBatchingBenchmark:
    """TAP-2800 before/after: per-row save loop vs one batched save_many.

    The win is the elimination of N-1 DB round-trips, so a small per-round-trip
    latency is injected to model real Postgres write-through cost.  Run both and
    compare the means::

        pytest tests/benchmarks/test_benchmarks.py -k SaveManyBatching \\
            --benchmark-only --benchmark-sort=name

    ``test_before_per_row_save_loop`` issues ``_ROWS`` round-trips;
    ``test_after_batched_save_many`` issues exactly one.  The mean of the
    "after" case should be roughly ``_ROWS``x lower, demonstrating the fix.
    """

    _ROWS = 100
    _LATENCY_S = 0.005  # simulated per-round-trip DB latency (~remote Postgres)

    def _store_with_latency(self, tmp_path: Path) -> MemoryStore:
        # embedding_provider=None isolates the DB round-trip cost — embedding is
        # per-row CPU that is identical in both paths and would otherwise mask
        # the round-trip win this benchmark is meant to demonstrate.
        store = MemoryStore(tmp_path, embedding_provider=None)
        backend = store._persistence  # type: ignore[attr-defined]
        orig_save = backend.save
        orig_save_many = backend.save_many
        latency = self._LATENCY_S

        def slow_save(entry: object) -> None:
            time.sleep(latency)  # one round-trip per entry
            orig_save(entry)

        def slow_save_many(entries: list) -> None:
            time.sleep(latency)  # one round-trip for the whole batch
            orig_save_many(entries)

        backend.save = slow_save  # type: ignore[assignment]
        backend.save_many = slow_save_many  # type: ignore[assignment]
        return store

    def _items(self) -> list[dict[str, object]]:
        # Disable dedup / conflict-check / consolidation so the ONLY backend
        # writes are the main per-entry persists — isolating the round-trip win.
        return [
            {
                "key": f"k{i}",
                "value": f"batch value {i} with content",
                "skip_consolidation": True,
                "dedup": False,
                "conflict_check": False,
            }
            for i in range(self._ROWS)
        ]

    def test_before_per_row_save_loop(self, benchmark, tmp_path: Path) -> None:
        store = self._store_with_latency(tmp_path)
        items = self._items()
        try:

            def do_loop() -> None:
                for item in items:
                    store.save(**item)  # type: ignore[arg-type]

            benchmark(do_loop)
        finally:
            store.close()

    def test_after_batched_save_many(self, benchmark, tmp_path: Path) -> None:
        store = self._store_with_latency(tmp_path)
        items = self._items()
        try:
            benchmark(lambda: store.save_many(items))
        finally:
            store.close()


class TestStoreGetBenchmark:
    def test_get_1000_random_reads(self, benchmark, populated_store: MemoryStore) -> None:
        """Benchmark: 1000 random reads from a full 500-entry store."""
        keys = [f"bench-key-{i:04d}" for i in range(500)]
        rng = random.Random(42)

        def do_reads():
            for _ in range(1000):
                populated_store.get(rng.choice(keys))

        benchmark(do_reads)


class TestStoreSearchBenchmark:
    def test_fts5_search(self, benchmark, populated_store: MemoryStore) -> None:
        """Benchmark: FTS5 search queries against a full store."""
        queries = ["decisions", "component", "observations", "architectural", "pattern"]

        def do_searches():
            for q in queries:
                populated_store.search(q)

        benchmark(do_searches)


# ---------------------------------------------------------------------------
# Reinforcement benchmark
# ---------------------------------------------------------------------------


class TestReinforceBenchmark:
    def test_reinforce_500(self, benchmark, populated_store: MemoryStore) -> None:
        """Benchmark: 500 reinforcement operations."""
        keys = [f"bench-key-{i:04d}" for i in range(500)]

        def do_reinforcements():
            for key in keys:
                populated_store.reinforce(key, confidence_boost=0.05)

        benchmark(do_reinforcements)


# ---------------------------------------------------------------------------
# Retrieval benchmark
# ---------------------------------------------------------------------------


class TestRetrievalBenchmark:
    def test_search_100_queries(self, benchmark, populated_store: MemoryStore) -> None:
        """Benchmark: 100 ranked retrieval queries via MemoryRetriever."""
        retriever = MemoryRetriever(config=DecayConfig())
        queries = [f"component {i} decisions" for i in range(20)] * 5

        def do_retrieval():
            for q in queries:
                retriever.search(q, populated_store)

        benchmark(do_retrieval)


# ---------------------------------------------------------------------------
# BM25 benchmark
# ---------------------------------------------------------------------------


class TestBM25Benchmark:
    def test_bm25_scoring(self, benchmark) -> None:
        """Benchmark: BM25 build_index + score over 500 documents."""
        documents = [
            f"Document {i} about architectural decisions and pattern observations "
            f"for component {i % 20} in the project"
            for i in range(500)
        ]

        def do_scoring():
            scorer = BM25Scorer()
            scorer.build_index(documents)
            scorer.score("architectural decisions component")

        benchmark(do_scoring)


# ---------------------------------------------------------------------------
# Decay benchmark
# ---------------------------------------------------------------------------


class TestDecayBenchmark:
    def test_decay_10000_calculations(self, benchmark) -> None:
        """Benchmark: 10,000 decay confidence calculations with varied ages and tiers."""
        config = DecayConfig()
        now = datetime.now(tz=UTC)
        tiers = ["architectural", "pattern", "procedural", "context"]
        entries = []
        for i in range(10000):
            age_days = (i % 365) + 1
            tier = tiers[i % len(tiers)]
            updated = (now - timedelta(days=age_days)).isoformat()
            entries.append(
                make_entry(
                    key=f"decay-{i}",
                    value=f"entry {i}",
                    tier=tier,
                    confidence=0.5 + (i % 50) / 100.0,
                    updated_at=updated,
                )
            )

        def do_decay():
            for entry in entries:
                calculate_decayed_confidence(entry, config, now=now)

        benchmark(do_decay)
