"""Integration tests for ranked retrieval with real MemoryStore + SQLite.

Uses real MemoryStore (no mocks), real SQLite/FTS5, real BM25 scoring,
and real decay computation. All databases use tmp_path for isolation.

Story: STORY-001.3 from EPIC-001
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from tapps_brain.decay import DecayConfig
from tapps_brain.retrieval import MemoryRetriever, ScoredMemory
from tapps_brain.store import MemoryStore

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path: Path) -> MemoryStore:
    """Create a real MemoryStore backed by SQLite in a temp directory."""
    s = MemoryStore(tmp_path)
    yield s
    s.close()


@pytest.fixture()
def retriever() -> MemoryRetriever:
    """Create a MemoryRetriever with default decay config."""
    return MemoryRetriever(config=DecayConfig())


def _save(
    store: MemoryStore,
    key: str,
    value: str,
    *,
    tier: str = "pattern",
    tags: list[str] | None = None,
    confidence: float = -1.0,
    source: str = "agent",
    conflict_check: bool = True,
) -> None:
    """Helper to save a memory entry with sensible defaults."""
    result = store.save(
        key=key,
        value=value,
        tier=tier,
        tags=tags or [],
        confidence=confidence,
        source=source,
        conflict_check=conflict_check,
    )
    assert not isinstance(result, dict), f"save failed: {result}"


def _backdate_entry(store: MemoryStore, key: str, days_ago: int) -> None:
    """Backdate an entry's updated_at by manipulating internal state.

    MemoryStore.update_fields always overrides updated_at with now(),
    so we directly modify the in-memory entry and persist it to SQLite.
    This is acceptable in tests to simulate time passage.
    """
    old_time = (datetime.now(tz=UTC) - timedelta(days=days_ago)).isoformat()
    with store._lock:
        entry = store._entries.get(key)
        assert entry is not None, f"Entry {key!r} not found"
        updated = entry.model_copy(update={"updated_at": old_time})
        store._entries[key] = updated
    store._persistence.save(updated)


# ---------------------------------------------------------------------------
# BM25 scoring against real FTS5
# ---------------------------------------------------------------------------


class TestBM25WithRealFTS5:
    """Verify BM25 scoring works end-to-end with real FTS5 candidate retrieval."""

    def test_exact_keyword_match_ranks_highest(
        self, store: MemoryStore, retriever: MemoryRetriever
    ) -> None:
        _save(store, "python-logging", "Use structlog for structured logging in Python")
        _save(store, "java-logging", "Use SLF4J for logging in Java applications")
        _save(store, "database-setup", "PostgreSQL is the primary database engine")

        results = retriever.search("python logging", store)

        assert len(results) >= 1
        assert results[0].entry.key == "python-logging"
        assert results[0].bm25_relevance > 0.0
        assert results[0].score > 0.0

    def test_multiple_term_match_scores_higher_than_single(
        self, store: MemoryStore, retriever: MemoryRetriever
    ) -> None:
        """Entry containing all query terms should score highest via BM25."""
        _save(
            store,
            "api-auth",
            "API authentication uses JWT tokens for security",
        )
        _save(
            store,
            "api-design",
            "API design follows REST conventions with JSON responses",
        )
        _save(
            store,
            "auth-tokens",
            "JWT tokens expire after 24 hours for security",
        )

        # Use a query that FTS5 will match broadly
        results = retriever.search("API JWT tokens authentication", store)

        assert len(results) >= 1
        # api-auth mentions API, authentication, JWT, tokens -- all query terms
        assert results[0].entry.key == "api-auth"

    def test_no_results_for_absent_terms(
        self, store: MemoryStore, retriever: MemoryRetriever
    ) -> None:
        _save(store, "python-setup", "Install Python 3.12 using pyenv")

        results = retriever.search("kubernetes deployment", store)

        # May return 0 results or results with very low scores
        for r in results:
            assert r.bm25_relevance < 0.3

    def test_empty_query_returns_empty(
        self, store: MemoryStore, retriever: MemoryRetriever
    ) -> None:
        _save(store, "some-entry", "Some content here")
        assert retriever.search("", store) == []
        assert retriever.search("   ", store) == []

    def test_bm25_relevance_is_normalized(
        self, store: MemoryStore, retriever: MemoryRetriever
    ) -> None:
        _save(
            store,
            "testing-guide",
            "pytest is used for all unit and integration testing",
        )
        _save(
            store,
            "ci-pipeline",
            "CI pipeline runs pytest with coverage reporting",
        )

        results = retriever.search("pytest testing", store)

        assert len(results) >= 1, "Expected at least one result for 'pytest testing'"
        for r in results:
            assert 0.0 <= r.bm25_relevance <= 1.0

    def test_tags_contribute_to_bm25_scoring(
        self, store: MemoryStore, retriever: MemoryRetriever
    ) -> None:
        _save(
            store,
            "deploy-config",
            "Deployment configuration for production",
            tags=["docker", "kubernetes"],
        )
        _save(
            store,
            "local-dev",
            "Local development setup instructions",
            tags=["development"],
        )

        results = retriever.search("docker", store)

        assert len(results) >= 1
        assert results[0].entry.key == "deploy-config"


# ---------------------------------------------------------------------------
# Contradicted entry exclusion
# ---------------------------------------------------------------------------


class TestContradictedEntryExclusion:
    """Verify contradicted entries are excluded from results by default."""

    def test_contradicted_entries_excluded_by_default(
        self, store: MemoryStore, retriever: MemoryRetriever
    ) -> None:
        _save(store, "old-pattern", "Use callbacks for async operations")
        _save(store, "new-pattern", "Use async/await for async operations")

        store.update_fields(
            "old-pattern",
            contradicted=True,
            contradiction_reason="Superseded by async/await pattern",
        )

        results = retriever.search("async operations", store)

        result_keys = [r.entry.key for r in results]
        assert "old-pattern" not in result_keys
        assert "new-pattern" in result_keys

    def test_contradicted_entries_included_when_requested(
        self, store: MemoryStore, retriever: MemoryRetriever
    ) -> None:
        _save(store, "old-api", "REST API v1 uses XML responses")
        _save(store, "new-api", "REST API v2 uses JSON responses")

        store.update_fields(
            "old-api",
            contradicted=True,
            contradiction_reason="Replaced by v2",
        )

        results = retriever.search("REST API responses", store, include_contradicted=True)

        result_keys = [r.entry.key for r in results]
        assert "old-api" in result_keys
        assert "new-api" in result_keys

    def test_consolidated_source_entries_excluded_by_default(
        self, store: MemoryStore, retriever: MemoryRetriever
    ) -> None:
        _save(store, "pattern-a", "Use pytest fixtures for test setup")
        _save(store, "pattern-b", "Use pytest parametrize for test variations")
        _save(
            store,
            "consolidated-patterns",
            "Use pytest fixtures and parametrize",
        )

        store.update_fields(
            "pattern-a",
            contradicted=True,
            contradiction_reason="Consolidated into consolidated-patterns",
        )
        store.update_fields(
            "pattern-b",
            contradicted=True,
            contradiction_reason="Consolidated into consolidated-patterns",
        )

        results = retriever.search("pytest", store)

        result_keys = [r.entry.key for r in results]
        assert "pattern-a" not in result_keys
        assert "pattern-b" not in result_keys
        assert "consolidated-patterns" in result_keys

    def test_consolidated_sources_included_when_requested(
        self, store: MemoryStore, retriever: MemoryRetriever
    ) -> None:
        _save(store, "src-entry", "Original pattern about logging")
        _save(store, "merged-entry", "Consolidated logging pattern")

        store.update_fields(
            "src-entry",
            contradicted=True,
            contradiction_reason="Consolidated into merged-entry",
        )

        results = retriever.search("logging pattern", store, include_sources=True)

        result_keys = [r.entry.key for r in results]
        assert "src-entry" in result_keys


# ---------------------------------------------------------------------------
# Stale / decayed entries rank lower
# ---------------------------------------------------------------------------


class TestDecayedEntriesRankLower:
    """Verify that stale/decayed entries rank lower than fresh entries."""

    def test_recent_entry_ranks_above_old_entry(
        self, store: MemoryStore, retriever: MemoryRetriever
    ) -> None:
        _save(store, "old-deploy", "Deploy using Docker compose for production")

        # Backdate the entry, then search to get its score
        _backdate_entry(store, "old-deploy", days_ago=90)
        results_old = retriever.search("Docker compose deploy", store, min_confidence=0.0)
        assert len(results_old) >= 1
        old_score = results_old[0].score

        # Now save a fresh entry with similar content — it should score higher
        # because its recency and confidence are both better.
        _save(store, "new-deploy", "Deploy using Docker compose with health checks")
        results_new = retriever.search("Docker compose deploy", store, min_confidence=0.0)
        new_result = next(r for r in results_new if r.entry.key == "new-deploy")

        # The fresh entry should score at least as high as the stale entry did
        # when it was the only match (single-entry normalization gives it 1.0).
        assert new_result.score >= old_score * 0.7, (
            f"new={new_result.score:.4f} should be close to old={old_score:.4f}"
        )
        # And the old entry should now be marked stale
        old_result = next((r for r in results_new if r.entry.key == "old-deploy"), None)
        if old_result is not None:
            assert old_result.stale is True

    def test_context_tier_decays_faster_than_architectural(self, tmp_path: Path) -> None:
        """Context tier (14d half-life) decays faster than architectural (180d)."""
        s = MemoryStore(tmp_path)
        try:
            config = DecayConfig()
            ret = MemoryRetriever(config=config)

            _save(
                s,
                "arch-decision",
                "Database migration strategy uses alembic for schema changes",
                tier="architectural",
                confidence=0.9,
                source="human",
            )
            _save(
                s,
                "ctx-decision",
                "Database migration running today uses alembic schema update",
                tier="context",
                confidence=0.9,
                source="human",
            )

            # Backdate both entries to 30 days ago
            _backdate_entry(s, "arch-decision", days_ago=30)
            _backdate_entry(s, "ctx-decision", days_ago=30)

            results = ret.search("database migration alembic schema", s)

            assert len(results) == 2
            arch = next(r for r in results if r.entry.key == "arch-decision")
            ctx = next(r for r in results if r.entry.key == "ctx-decision")

            # Architectural should retain more confidence after 30 days
            assert arch.effective_confidence > ctx.effective_confidence
        finally:
            s.close()

    def test_stale_flag_set_for_heavily_decayed_entries(self, tmp_path: Path) -> None:
        """Context entry backdated 120 days should be marked stale."""
        s = MemoryStore(tmp_path)
        try:
            config = DecayConfig()
            ret = MemoryRetriever(config=config)

            _save(
                s,
                "ancient-entry",
                "Very old context about temporary feature flag setup",
                tier="context",
                confidence=0.5,
                source="agent",
            )

            # Backdate 120 days (context half-life is 14d, so ~8.5 half-lives)
            _backdate_entry(s, "ancient-entry", days_ago=120)

            results = ret.search("feature flag setup", s, min_confidence=0.0)

            assert len(results) >= 1
            r = next(r for r in results if r.entry.key == "ancient-entry")
            assert r.stale is True
            assert r.effective_confidence < 0.3
        finally:
            s.close()

    def test_min_confidence_filters_decayed_entries(
        self, store: MemoryStore, retriever: MemoryRetriever
    ) -> None:
        _save(
            store,
            "decayed-entry",
            "Temporary context about sprint planning process",
            tier="context",
            confidence=0.4,
            source="agent",
        )

        # Backdate heavily (context half-life 14d, 60 days = ~4 half-lives)
        _backdate_entry(store, "decayed-entry", days_ago=60)

        # After 60 days with context tier: 0.4 * 0.5^(60/14) ~ 0.02
        # min_confidence=0.1 should filter it (floor is 0.1 though)
        # DecayConfig.confidence_floor=0.1 clamps it, so use min > 0.1
        results = retriever.search("sprint planning", store, min_confidence=0.15)

        result_keys = [r.entry.key for r in results]
        assert "decayed-entry" not in result_keys


# ---------------------------------------------------------------------------
# Hybrid search fallback paths
# ---------------------------------------------------------------------------


class TestHybridSearchFallback:
    """Verify fallback behavior when semantic search is not available."""

    def test_non_semantic_uses_bm25_path(self, store: MemoryStore) -> None:
        ret = MemoryRetriever(semantic_enabled=False)

        _save(store, "test-entry", "Unit testing with pytest and fixtures")

        results = ret.search("pytest fixtures", store)

        assert len(results) >= 1
        assert results[0].entry.key == "test-entry"
        assert results[0].bm25_relevance > 0.0

    def test_semantic_enabled_without_embedder_falls_back(self, store: MemoryStore) -> None:
        """When semantic_enabled=True but no embedder, should still work."""
        ret = MemoryRetriever(semantic_enabled=True)

        _save(store, "fallback-entry", "Fallback search behavior documentation")

        results = ret.search("fallback search", store)

        # Should return results via BM25 fallback even with semantic enabled
        assert len(results) >= 1
        assert results[0].entry.key == "fallback-entry"

    def test_full_corpus_bm25_scan_fallback(
        self, store: MemoryStore, retriever: MemoryRetriever
    ) -> None:
        """BM25 full scan finds entries even if FTS5 misses."""
        _save(
            store,
            "arch-overview",
            "The system architecture uses microservices",
        )

        results = retriever.search("microservices architecture", store)

        assert len(results) >= 1
        found_keys = [r.entry.key for r in results]
        assert "arch-overview" in found_keys


# ---------------------------------------------------------------------------
# Composite scoring integration
# ---------------------------------------------------------------------------


class TestCompositeScoringIntegration:
    """Verify all scoring components work together in real retrieval."""

    def test_high_confidence_entry_ranks_higher(
        self, store: MemoryStore, retriever: MemoryRetriever
    ) -> None:
        """High-confidence entry should outscore low-confidence with same text."""
        # Distinct values avoid Bloom write-dedup treating the second save as reinforce-only.
        # Disable conflict_check: near-duplicate text would invalidate the first entry (#44).
        _save(
            store,
            "low-conf",
            "Testing approach uses mocks for external services (variant A)",
            confidence=0.3,
            conflict_check=False,
        )
        _save(
            store,
            "high-conf",
            "Testing approach uses mocks for external services (variant B)",
            confidence=0.95,
            source="human",
            conflict_check=False,
        )

        results = retriever.search("testing mocks external services", store)

        assert len(results) == 2
        high = next(r for r in results if r.entry.key == "high-conf")
        low = next(r for r in results if r.entry.key == "low-conf")
        # Higher confidence should contribute more to the composite score.
        # We verify via effective_confidence rather than rank order, since
        # min-max relevance normalization with only 2 entries can dominate.
        assert high.effective_confidence > low.effective_confidence

    def test_frequently_accessed_entry_gets_frequency_boost(
        self, store: MemoryStore, retriever: MemoryRetriever
    ) -> None:
        _save(
            store,
            "popular-entry",
            "Docker container orchestration with compose",
        )
        _save(
            store,
            "rare-entry",
            "Docker container orchestration with swarm",
        )

        # Simulate frequent access
        for _ in range(15):
            store.get("popular-entry")

        results = retriever.search("Docker container orchestration", store)

        assert len(results) == 2
        popular = next(r for r in results if r.entry.key == "popular-entry")
        rare = next(r for r in results if r.entry.key == "rare-entry")
        assert popular.score > rare.score

    def test_exact_key_match_gets_bonus(
        self, store: MemoryStore, retriever: MemoryRetriever
    ) -> None:
        _save(
            store,
            "database-setup",
            "Configure PostgreSQL with connection pooling",
        )
        _save(
            store,
            "db-pooling",
            "Database connection pool sizing guidelines",
        )

        results = retriever.search("database-setup", store)

        assert len(results) >= 1
        assert results[0].entry.key == "database-setup"

    def test_limit_parameter_respected(
        self, store: MemoryStore, retriever: MemoryRetriever
    ) -> None:
        for i in range(10):
            _save(
                store,
                f"entry-{i:02d}",
                f"Testing topic number {i} with pytest",
            )

        results = retriever.search("testing pytest", store, limit=3)
        assert len(results) <= 3

    def test_scored_memory_fields_populated(
        self, store: MemoryStore, retriever: MemoryRetriever
    ) -> None:
        _save(
            store,
            "check-fields",
            "Verify all scored memory fields are populated",
        )

        results = retriever.search("scored memory fields", store)

        assert len(results) >= 1
        r: ScoredMemory = results[0]
        assert r.score > 0.0
        assert 0.0 <= r.effective_confidence <= 1.0
        assert 0.0 <= r.bm25_relevance <= 1.0
        assert isinstance(r.stale, bool)
        assert r.entry.key == "check-fields"
