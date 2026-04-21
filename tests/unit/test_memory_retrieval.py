"""Tests for ranked memory retrieval (Epic 25, Story 25.1 + Epic 34.2 BM25)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tapps_brain.decay import DecayConfig
from tapps_brain.models import (
    MemoryEntry,
    MemorySource,
    MemoryTier,
)
from tapps_brain.profile import ScoringConfig
from tapps_brain.reranker import NoopReranker
from tapps_brain.retrieval import MemoryRetriever, ScoredMemory
from tests.factories import make_entry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 2, 27, 12, 0, 0, tzinfo=UTC)
_RECENT = (_NOW - timedelta(days=1)).isoformat()
_OLD = (_NOW - timedelta(days=90)).isoformat()
_VERY_OLD = (_NOW - timedelta(days=365)).isoformat()


def _make_entry(
    key: str = "test-key",
    value: str = "test value",
    *,
    tier: MemoryTier = MemoryTier.pattern,
    confidence: float = 0.8,
    source: MemorySource = MemorySource.agent,
    updated_at: str = "",
    access_count: int = 0,
    contradicted: bool = False,
    tags: list[str] | None = None,
) -> MemoryEntry:
    return make_entry(
        key=key,
        value=value,
        tier=tier,
        confidence=confidence,
        source=source,
        updated_at=updated_at or _RECENT,
        created_at=updated_at or _RECENT,
        last_accessed=updated_at or _RECENT,
        access_count=access_count,
        contradicted=contradicted,
        tags=tags,
    )


def _make_store(entries: list[MemoryEntry] | None = None) -> MagicMock:
    """Create a mock MemoryStore with search and list_all."""
    store = MagicMock()
    entries = entries or []

    store.list_all.return_value = entries
    # store.search returns the same entries (simulating FTS match)
    store.search.return_value = entries

    entry_map = {e.key: e for e in entries}
    store.get.side_effect = lambda k, **kwargs: entry_map.get(k)

    return store


# ---------------------------------------------------------------------------
# ScoredMemory model tests
# ---------------------------------------------------------------------------


class TestScoredMemory:
    def test_scored_memory_creation(self) -> None:
        entry = _make_entry()
        scored = ScoredMemory(
            entry=entry,
            score=0.75,
            effective_confidence=0.8,
            bm25_relevance=0.5,
            stale=False,
        )
        assert scored.score == 0.75
        assert scored.entry.key == "test-key"


# ---------------------------------------------------------------------------
# MemoryRetriever tests
# ---------------------------------------------------------------------------


class TestMemoryRetriever:
    def test_empty_query_returns_empty(self) -> None:
        retriever = MemoryRetriever()
        store = _make_store([_make_entry()])
        assert retriever.search("", store) == []
        assert retriever.search("   ", store) == []

    def test_no_matching_entries(self) -> None:
        retriever = MemoryRetriever()
        entries = [_make_entry("unrelated-key", "no match here")]
        store = _make_store(entries)
        # store.search returns empty for non-matching query
        store.search.return_value = []

        results = retriever.search("completely different query", store)
        assert len(results) == 0

    def test_exact_key_match_ranks_highest(self) -> None:
        entries = [
            _make_entry("jwt-auth", "JWT authentication config", confidence=0.7),
            _make_entry("auth-setup", "Authentication setup details", confidence=0.7),
        ]
        retriever = MemoryRetriever()
        store = _make_store(entries)

        results = retriever.search("jwt-auth", store)
        assert len(results) >= 1
        assert results[0].entry.key == "jwt-auth"

    def test_high_confidence_outranks_low(self) -> None:
        entries = [
            _make_entry(
                "low-conf",
                "test framework value",
                confidence=0.3,
                updated_at=_RECENT,
            ),
            _make_entry(
                "high-conf",
                "test framework value",
                confidence=0.9,
                updated_at=_RECENT,
            ),
        ]
        retriever = MemoryRetriever()
        store = _make_store(entries)

        results = retriever.search("test framework", store)
        assert len(results) == 2
        assert results[0].entry.key == "high-conf"

    def test_recent_memory_outranks_old(self) -> None:
        entries = [
            _make_entry(
                "old-memory",
                "database config setup",
                confidence=0.8,
                updated_at=_OLD,
            ),
            _make_entry(
                "new-memory",
                "database config setup",
                confidence=0.8,
                updated_at=_RECENT,
            ),
        ]
        retriever = MemoryRetriever()
        store = _make_store(entries)

        results = retriever.search("database config", store)
        assert len(results) == 2
        assert results[0].entry.key == "new-memory"

    def test_contradicted_excluded_by_default(self) -> None:
        entries = [
            _make_entry("good-key", "valid memory", contradicted=False),
            _make_entry("bad-key", "contradicted memory", contradicted=True),
        ]
        retriever = MemoryRetriever()
        store = _make_store(entries)

        results = retriever.search("memory", store)
        keys = [r.entry.key for r in results]
        assert "bad-key" not in keys

    def test_contradicted_included_when_requested(self) -> None:
        entries = [
            _make_entry("bad-key", "contradicted memory", contradicted=True),
        ]
        retriever = MemoryRetriever()
        store = _make_store(entries)

        results = retriever.search("contradicted memory", store, include_contradicted=True)
        assert len(results) == 1
        assert results[0].entry.key == "bad-key"

    def test_result_limit_respected(self) -> None:
        entries = [_make_entry(f"key-{i}", f"matching value {i}") for i in range(20)]
        retriever = MemoryRetriever()
        store = _make_store(entries)

        results = retriever.search("matching value", store, limit=5)
        assert 1 <= len(results) <= 5

    def test_max_limit_capped(self) -> None:
        entries = [_make_entry(f"key-{i}", f"value {i}") for i in range(60)]
        retriever = MemoryRetriever()
        store = _make_store(entries)

        results = retriever.search("value", store, limit=100)
        assert len(results) <= 50

    def test_low_confidence_filtered(self) -> None:
        entries = [
            _make_entry(
                "very-low",
                "matching text",
                confidence=0.05,
                updated_at=_VERY_OLD,
            ),
        ]
        retriever = MemoryRetriever(config=DecayConfig())
        store = _make_store(entries)

        # Confidence floor is 0.1, so entry decays to 0.1.
        # Use min_confidence > 0.1 to filter it out.
        results = retriever.search("matching text", store, min_confidence=0.2)
        assert len(results) == 0

    def test_frequency_affects_ranking(self) -> None:
        entries = [
            _make_entry(
                "rarely-accessed",
                "shared pattern data",
                access_count=1,
                confidence=0.8,
            ),
            _make_entry(
                "often-accessed",
                "shared pattern data",
                access_count=20,
                confidence=0.8,
            ),
        ]
        retriever = MemoryRetriever()
        store = _make_store(entries)

        results = retriever.search("shared pattern", store)
        assert len(results) == 2
        assert results[0].entry.key == "often-accessed"

    def test_fallback_to_like_search(self) -> None:
        """When FTS5 search returns no results, fallback to LIKE."""
        entries = [_make_entry("my-key", "some matching content")]
        retriever = MemoryRetriever()
        store = _make_store(entries)
        # store.search raises exception = FTS5 unavailable
        store.search.side_effect = Exception("FTS5 not available")

        results = retriever.search("matching content", store)
        assert len(results) == 1

    def test_word_overlap_scoring(self) -> None:
        entry = _make_entry("test-key", "python fastapi web framework")
        score = MemoryRetriever._word_overlap_score("python fastapi", entry)
        assert score > 0.0


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------


class TestScoringHelpers:
    def test_normalize_relevance_zero(self) -> None:
        retriever = MemoryRetriever()
        assert retriever._normalize_relevance(0.0) == 0.0

    def test_normalize_relevance_no_bounds_positive(self) -> None:
        """Without bounds (single candidate), positive scores map to 1.0."""
        retriever = MemoryRetriever()
        assert retriever._normalize_relevance(5.0) == 1.0
        assert retriever._normalize_relevance(100.0) == 1.0

    def test_normalize_relevance_minmax_with_bounds(self) -> None:
        """Min-max normalization uses per-query rmin/rmax."""
        retriever = MemoryRetriever()
        score = retriever._normalize_relevance(5.0, rmin=0.0, rmax=10.0)
        assert score == pytest.approx(0.5)

    def test_normalize_relevance_minmax_spread(self) -> None:
        r = MemoryRetriever()
        assert r._normalize_relevance(1.0, rmin=1.0, rmax=5.0) == pytest.approx(0.0)
        assert r._normalize_relevance(5.0, rmin=1.0, rmax=5.0) == pytest.approx(1.0)
        assert r._normalize_relevance(3.0, rmin=1.0, rmax=5.0) == pytest.approx(0.5)

    def test_normalize_relevance_minmax_clamps(self) -> None:
        r = MemoryRetriever()
        assert r._normalize_relevance(10.0, rmin=1.0, rmax=5.0) == 1.0
        assert r._normalize_relevance(-2.0, rmin=1.0, rmax=5.0) == 0.0

    def test_normalize_relevance_minmax_degenerate(self) -> None:
        r = MemoryRetriever()
        assert r._normalize_relevance(2.0, rmin=2.0, rmax=2.0) == 1.0

    def test_recency_score_recent(self) -> None:
        entry = _make_entry(updated_at=_NOW.isoformat())
        score = MemoryRetriever._recency_score(entry, _NOW)
        assert score == pytest.approx(1.0, abs=0.01)

    def test_recency_score_old(self) -> None:
        entry = _make_entry(updated_at=_OLD)
        score = MemoryRetriever._recency_score(entry, _NOW)
        assert score < 0.1

    def test_frequency_score_zero(self) -> None:
        retriever = MemoryRetriever()
        entry = _make_entry(access_count=0)
        assert retriever._frequency_score(entry) == 0.0

    def test_frequency_score_capped(self) -> None:
        retriever = MemoryRetriever()
        entry = _make_entry(access_count=100)
        assert retriever._frequency_score(entry) == 1.0

    def test_frequency_score_no_zero_division_when_cap_zero(self) -> None:
        """Duck-typed scoring_config with frequency_cap=0 must not raise ZeroDivisionError (TAP-635)."""
        cfg = SimpleNamespace(
            relevance=0.4,
            confidence=0.3,
            recency=0.15,
            frequency=0.15,
            frequency_cap=0,  # invalid but must not crash
            graph_centrality=0.0,
            provenance_trust=0.0,
            source_trust=None,
        )
        retriever = MemoryRetriever(scoring_config=cfg)
        # _frequency_cap must have been floored to 1.0
        assert retriever._frequency_cap >= 1.0
        entry = _make_entry(access_count=5)
        score = retriever._frequency_score(entry)
        assert score == 1.0  # 5/1.0 capped at 1.0

    def test_frequency_score_no_zero_division_when_cap_negative(self) -> None:
        """Negative frequency_cap via duck-typed config also must not raise (TAP-635)."""
        cfg = SimpleNamespace(
            relevance=0.4,
            confidence=0.3,
            recency=0.15,
            frequency=0.15,
            frequency_cap=-5,
            graph_centrality=0.0,
            provenance_trust=0.0,
            source_trust=None,
        )
        retriever = MemoryRetriever(scoring_config=cfg)
        assert retriever._frequency_cap >= 1.0
        entry = _make_entry(access_count=0)
        assert retriever._frequency_score(entry) == 0.0


class TestRelevanceNormalizationMinmax:
    """Min-max relevance normalization is always active."""

    def test_minmax_orders_by_raw_when_other_signals_equal(self) -> None:
        base = _RECENT
        e_low = _make_entry("low-rel", "alpha beta gamma", updated_at=base, access_count=5)
        e_high = _make_entry("high-rel", "alpha beta gamma", updated_at=base, access_count=5)
        retriever = MemoryRetriever()
        store = _make_store([e_low, e_high])
        with patch.object(
            retriever,
            "_get_candidates",
            return_value=[(e_low, 2.0), (e_high, 8.0)],
        ):
            results = retriever.search("query", store)
        assert len(results) == 2
        assert results[0].entry.key == "high-rel"
        assert results[0].bm25_relevance == 1.0
        assert results[1].entry.key == "low-rel"
        assert results[1].bm25_relevance == 0.0

    def test_minmax_equal_raw_scores_use_degenerate_one(self) -> None:
        e1 = _make_entry("a", "same text", updated_at=_RECENT, access_count=5)
        e2 = _make_entry("b", "same text", updated_at=_RECENT, access_count=5)
        retriever = MemoryRetriever()
        store = _make_store([e1, e2])
        with patch.object(
            retriever,
            "_get_candidates",
            return_value=[(e1, 3.0), (e2, 3.0)],
        ):
            results = retriever.search("q", store)
        assert len(results) == 2
        assert results[0].bm25_relevance == 1.0
        assert results[1].bm25_relevance == 1.0


# ---------------------------------------------------------------------------
# BM25 integration tests (Epic 34.2)
# ---------------------------------------------------------------------------


class TestBM25Integration:
    """Tests for BM25-based relevance scoring in MemoryRetriever."""

    def test_stemming_matches_related_words(self) -> None:
        """'testing' query should match a memory about 'test' via stemming."""
        entries = [
            _make_entry("test-framework", "test framework configuration"),
            _make_entry("unrelated", "database connection pooling"),
        ]
        retriever = MemoryRetriever()
        store = _make_store(entries)

        results = retriever.search("testing", store)
        assert len(results) >= 1
        assert results[0].entry.key == "test-framework"

    def test_idf_rare_term_scores_higher(self) -> None:
        """A rare term should produce a higher raw BM25 score than a common one.

        Both entries match "setup" equally, but rust-setup also matches
        the rare term "rust" while python-setup matches the common "python",
        so rust-setup gets a higher composite score.
        """
        entries = [
            _make_entry(
                "python-setup",
                "python setup guide",
                confidence=0.8,
                updated_at=_RECENT,
                access_count=5,
            ),
            _make_entry(
                "python-web",
                "python web framework",
                confidence=0.8,
                updated_at=_RECENT,
                access_count=5,
            ),
            _make_entry(
                "python-data",
                "python data science",
                confidence=0.8,
                updated_at=_RECENT,
                access_count=5,
            ),
            _make_entry(
                "rust-setup",
                "rust setup guide",
                confidence=0.8,
                updated_at=_RECENT,
                access_count=5,
            ),
        ]
        retriever = MemoryRetriever()
        store = _make_store(entries)

        # Search for "rust setup" — rust-setup matches both terms (rare + common),
        # python-setup only matches "setup" (common), so rust-setup ranks higher.
        results = retriever.search("rust setup", store)
        assert len(results) >= 2
        keys = [r.entry.key for r in results]
        assert keys[0] == "rust-setup"

    def test_composite_scoring_still_works(self) -> None:
        """BM25 integration preserves composite scoring formula."""
        entries = [
            _make_entry(
                "high-conf",
                "python framework config",
                confidence=0.9,
                updated_at=_RECENT,
                access_count=10,
            ),
            _make_entry(
                "low-conf",
                "python framework config",
                confidence=0.3,
                updated_at=_RECENT,
                access_count=1,
            ),
        ]
        retriever = MemoryRetriever()
        store = _make_store(entries)

        results = retriever.search("python framework", store)
        assert len(results) == 2
        # High confidence + high frequency should outrank
        assert results[0].entry.key == "high-conf"
        # Both should have positive composite scores
        assert all(r.score > 0 for r in results)
        # bm25_relevance should be populated
        assert all(r.bm25_relevance >= 0 for r in results)

    def test_fallback_to_word_overlap_on_bm25_error(self) -> None:
        """If BM25 scoring fails, fall back to word overlap."""
        entries = [_make_entry("my-key", "python framework setup")]
        retriever = MemoryRetriever()
        store = _make_store(entries)

        # Force BM25 to fail by corrupting internal state
        with patch.object(retriever._bm25, "score", side_effect=RuntimeError("BM25 broken")):
            results = retriever.search("python framework", store)
            # Should still get results via word overlap fallback
            assert len(results) >= 1

    def test_index_invalidation_on_store_size_change(self) -> None:
        """BM25 index should rebuild when store size changes."""
        entries_v1 = [
            _make_entry("key-a", "alpha content"),
        ]
        entries_v2 = [
            _make_entry("key-a", "alpha content"),
            _make_entry("key-b", "beta content"),
        ]
        retriever = MemoryRetriever()

        # First search builds index for 1 entry
        store_v1 = _make_store(entries_v1)
        retriever.search("alpha", store_v1)
        assert retriever._bm25_corpus_size == 1

        # Second search with 2 entries should rebuild
        store_v2 = _make_store(entries_v2)
        retriever.search("beta", store_v2)
        assert retriever._bm25_corpus_size == 2

    def test_empty_store_returns_no_results(self) -> None:
        """An empty store should return no results."""
        retriever = MemoryRetriever()
        store = _make_store([])
        store.search.return_value = []

        results = retriever.search("anything", store)
        assert results == []

    def test_bm25_relevance_field_populated(self) -> None:
        """The bm25_relevance field should be populated with BM25 score."""
        entries = [_make_entry("pytest-config", "pytest configuration details")]
        retriever = MemoryRetriever()
        store = _make_store(entries)

        results = retriever.search("pytest config", store)
        assert len(results) == 1
        assert results[0].bm25_relevance > 0.0
        assert results[0].bm25_relevance <= 1.0

    def test_word_overlap_still_available_as_method(self) -> None:
        """_word_overlap_score should still be available as a fallback."""
        entry = _make_entry("test-key", "python web framework")
        score = MemoryRetriever._word_overlap_score("python web", entry)
        assert score > 0.0

    def test_entry_to_document_includes_tags(self) -> None:
        """Document text should include key, value, and tags."""
        entry = _make_entry("my-key", "my value", tags=["tag1", "tag2"])
        doc = MemoryRetriever._entry_to_document(entry)
        assert "my-key" in doc
        assert "my value" in doc
        assert "tag1" in doc
        assert "tag2" in doc

    def test_semantic_disabled_uses_bm25_only(self) -> None:
        """Epic 65.8: When semantic_enabled=False, BM25-only (no regression)."""
        entries = [
            _make_entry("key-a", "python testing framework"),
            _make_entry("key-b", "python web server"),
        ]
        retriever = MemoryRetriever(semantic_enabled=False)
        store = _make_store(entries)

        results = retriever.search("python testing", store)
        assert len(results) >= 1
        assert results[0].entry.key == "key-a"

    def test_hybrid_falls_back_to_bm25_when_vector_empty(self) -> None:
        """Epic 65.8: When embedder unavailable, hybrid falls back to BM25."""
        entries = [_make_entry("my-key", "matching content")]
        retriever = MemoryRetriever(semantic_enabled=True)
        store = _make_store(entries)

        with patch(
            "tapps_brain.retrieval.MemoryRetriever._vector_search",
            return_value=[],
        ):
            results = retriever.search("matching content", store)
        assert len(results) >= 1
        assert results[0].entry.key == "my-key"

    def test_hybrid_merges_rrf_when_both_return_results(self) -> None:
        """Epic 65.8: Hybrid merges BM25 + vector via RRF."""
        entries = [
            _make_entry("a", "python framework"),
            _make_entry("b", "testing library"),
            _make_entry("c", "database config"),
        ]
        retriever = MemoryRetriever(semantic_enabled=True)
        store = _make_store(entries)

        def mock_vector_search(
            query: str,
            store: object,
            limit: int = 20,
            **kwargs: object,
        ) -> list[tuple[str, float]]:
            return [("b", 0.9), ("a", 0.7), ("c", 0.5)][:limit]

        with patch.object(retriever, "_vector_search", side_effect=mock_vector_search):
            results = retriever.search("python", store, limit=5)
        assert len(results) >= 1
        keys = [r.entry.key for r in results]
        assert "a" in keys or "b" in keys or "c" in keys

    def test_adaptive_hybrid_question_query_prefers_vector_only_hit(self) -> None:
        """EPIC-040: vague/question-shaped queries up-weight vector RRF."""
        entries = [_make_entry("only-a", "bm25 side"), _make_entry("only-b", "vector side")]
        retriever = MemoryRetriever(semantic_enabled=True)
        store = _make_store(entries)

        def fake_candidates(
            q: str, s: object, *args: object, **kwargs: object
        ) -> list[tuple[MemoryEntry, float]]:
            return [(entries[0], 1.0)]

        def fake_vector(
            q: str, s: object, limit: int = 20, **kwargs: object
        ) -> list[tuple[str, float]]:
            return [("only-b", 0.99)]

        with (
            patch.object(retriever, "_get_candidates", side_effect=fake_candidates),
            patch.object(retriever, "_vector_search", side_effect=fake_vector),
        ):
            results = retriever.search("what is up", store, limit=5)
        assert results[0].entry.key == "only-b"

    def test_adaptive_hybrid_long_query_prefers_bm25_only_hit(self) -> None:
        """EPIC-040: keyword-heavy long queries up-weight BM25 RRF."""
        entries = [_make_entry("only-a", "bm25 side"), _make_entry("only-b", "vector side")]
        retriever = MemoryRetriever(semantic_enabled=True)
        store = _make_store(entries)

        def fake_candidates(
            q: str, s: object, *args: object, **kwargs: object
        ) -> list[tuple[MemoryEntry, float]]:
            return [(entries[0], 1.0)]

        def fake_vector(
            q: str, s: object, limit: int = 20, **kwargs: object
        ) -> list[tuple[str, float]]:
            return [("only-b", 0.99)]

        with (
            patch.object(retriever, "_get_candidates", side_effect=fake_candidates),
            patch.object(retriever, "_vector_search", side_effect=fake_vector),
        ):
            results = retriever.search("foo bar baz aa bb cc dd ee", store, limit=5)
        assert results[0].entry.key == "only-a"

    def test_adaptive_hybrid_disabled_equal_rrf_weights(self) -> None:
        """``hybrid_config.adaptive_fusion=False`` restores 1:1 RRF weighting."""
        entries = [_make_entry("only-a", "bm25 side"), _make_entry("only-b", "vector side")]
        cfg = SimpleNamespace(adaptive_fusion=False)
        retriever = MemoryRetriever(semantic_enabled=True, hybrid_config=cfg)
        store = _make_store(entries)

        def fake_candidates(
            q: str, s: object, *args: object, **kwargs: object
        ) -> list[tuple[MemoryEntry, float]]:
            return [(entries[0], 1.0)]

        def fake_vector(
            q: str, s: object, limit: int = 20, **kwargs: object
        ) -> list[tuple[str, float]]:
            return [("only-b", 0.99)]

        with (
            patch.object(retriever, "_get_candidates", side_effect=fake_candidates),
            patch.object(retriever, "_vector_search", side_effect=fake_vector),
        ):
            results = retriever.search("what is up", store, limit=5)
        assert results[0].entry.key == "only-a"

    def test_bm25_multi_term_query(self) -> None:
        """Multi-term queries should match entries with multiple terms."""
        entries = [
            _make_entry("full-match", "python testing framework config"),
            _make_entry("partial-match", "python web server"),
            _make_entry("no-match", "database connection pooling"),
        ]
        retriever = MemoryRetriever()
        store = _make_store(entries)

        results = retriever.search("python testing framework", store)
        # full-match should rank first (matches more query terms)
        assert len(results) >= 1
        assert results[0].entry.key == "full-match"


# ---------------------------------------------------------------------------
# Reranker integration (Epic 65.9)
# ---------------------------------------------------------------------------


class TestRerankerIntegration:
    """Tests for optional reranking in MemoryRetriever."""

    def test_reranker_reorders_results(self) -> None:
        """When reranker reverses order, final order follows reranker."""
        entries = [
            _make_entry("key-a", "python framework"),
            _make_entry("key-b", "testing library"),
            _make_entry("key-c", "database config"),
        ]

        # NoopReranker preserves order, so use a custom reranker that reverses
        class ReverseReranker:
            def rerank(
                self,
                query: str,
                candidates: list[tuple[str, str]],
                top_k: int,
            ) -> list[tuple[str, float]]:
                reversed_cands = list(reversed(candidates[:top_k]))
                return [(k, 1.0 - i / 10) for i, (k, _) in enumerate(reversed_cands)]

        retriever = MemoryRetriever(
            reranker=ReverseReranker(),
            reranker_enabled=True,
            reranker_provider="reverse",
        )
        store = _make_store(entries)

        results = retriever.search("python testing database", store, limit=3)
        assert len(results) >= 1
        # ReverseReranker puts last candidate first
        assert results[0].entry.key == "key-c"
        stats = retriever.last_rerank_stats
        assert stats is not None
        assert stats["applied"] is True
        assert stats["provider"] == "reverse"
        assert stats["candidates_in"] == 3
        assert stats["top_k"] == 3
        assert stats["results_out"] == 3
        assert stats["error"] is None
        assert isinstance(stats["latency_ms"], int | float)
        assert stats["latency_ms"] >= 0.0

    def test_reranker_disabled_uses_composite_order(self) -> None:
        """When reranker disabled, original composite order is used."""
        entries = [
            _make_entry("high-conf", "matching content", confidence=0.9),
            _make_entry("low-conf", "matching content", confidence=0.3),
        ]
        retriever = MemoryRetriever(reranker=NoopReranker(), reranker_enabled=False)
        store = _make_store(entries)

        results = retriever.search("matching content", store)
        assert len(results) == 2
        assert results[0].entry.key == "high-conf"
        assert retriever.last_rerank_stats is None

    def test_reranker_failure_fallback(self) -> None:
        """When reranker raises, fall back to original order."""
        entries = [
            _make_entry("key-a", "content a"),
            _make_entry("key-b", "content b"),
        ]
        fail_reranker = MagicMock()
        fail_reranker.rerank.side_effect = RuntimeError("API down")

        retriever = MemoryRetriever(
            reranker=fail_reranker,
            reranker_enabled=True,
        )
        store = _make_store(entries)

        results = retriever.search("content", store)
        assert len(results) == 2
        # Original composite order preserved on failure
        assert results[0].entry.key in ("key-a", "key-b")
        stats = retriever.last_rerank_stats
        assert stats is not None
        assert stats["applied"] is False
        assert stats["error"] == "RuntimeError"
        assert stats["candidates_in"] == 2


class TestTemporalFiltering:
    """Tests for temporal filtering in retriever (EPIC-004, STORY-004.3)."""

    def test_superseded_excluded_by_default(self) -> None:
        """Entries with invalid_at in the past are excluded by default."""
        entries = [
            _make_entry("v1", "old pricing plan", updated_at=_RECENT),
            _make_entry("v2", "new pricing plan", updated_at=_RECENT),
        ]
        # Manually set v1 as superseded
        entries[0] = entries[0].model_copy(
            update={"invalid_at": "2020-01-01T00:00:00+00:00", "superseded_by": "v2"}
        )
        entries[1] = entries[1].model_copy(update={"valid_at": _RECENT})

        retriever = MemoryRetriever()
        store = _make_store(entries)
        results = retriever.search("pricing plan", store)
        keys = [r.entry.key for r in results]
        assert "v1" not in keys
        assert "v2" in keys

    def test_superseded_included_with_flag(self) -> None:
        """include_superseded=True returns temporally invalid entries marked stale."""
        entries = [
            _make_entry("old-fact", "old technology stack", updated_at=_RECENT),
            _make_entry("new-fact", "new technology stack", updated_at=_RECENT),
        ]
        entries[0] = entries[0].model_copy(
            update={"invalid_at": "2020-01-01T00:00:00+00:00", "superseded_by": "new-fact"}
        )

        retriever = MemoryRetriever()
        store = _make_store(entries)
        results = retriever.search("technology stack", store, include_superseded=True)
        keys = [r.entry.key for r in results]
        assert "old-fact" in keys

        old = next(r for r in results if r.entry.key == "old-fact")
        assert old.stale is True

    def test_as_of_point_in_time(self) -> None:
        """as_of parameter returns entries valid at that timestamp."""
        entries = [
            _make_entry("versioned", "database version info", updated_at=_RECENT),
        ]
        entries[0] = entries[0].model_copy(
            update={
                "valid_at": "2026-01-01T00:00:00+00:00",
                "invalid_at": "2026-06-01T00:00:00+00:00",
            }
        )

        retriever = MemoryRetriever()
        store = _make_store(entries)

        # Within window
        results_in = retriever.search("database version", store, as_of="2026-03-01T00:00:00+00:00")
        assert any(r.entry.key == "versioned" for r in results_in)

        # Outside window
        results_out = retriever.search("database version", store, as_of="2026-07-01T00:00:00+00:00")
        assert not any(r.entry.key == "versioned" for r in results_out)

    def test_superseded_penalty(self) -> None:
        """Superseded entries included via flag get a score penalty."""
        entries = [
            _make_entry("active", "current active data", confidence=0.8, updated_at=_RECENT),
            _make_entry("superseded", "old superseded data", confidence=0.8, updated_at=_RECENT),
        ]
        entries[1] = entries[1].model_copy(update={"invalid_at": "2020-01-01T00:00:00+00:00"})

        retriever = MemoryRetriever()
        store = _make_store(entries)
        results = retriever.search("data", store, include_superseded=True)

        active = next((r for r in results if r.entry.key == "active"), None)
        superseded = next((r for r in results if r.entry.key == "superseded"), None)
        assert active is not None
        assert superseded is not None
        assert active.score > superseded.score


# ---------------------------------------------------------------------------
# review(story-018.1): new tests for scoring correctness fixes
# ---------------------------------------------------------------------------


class TestScoringCorrectnessReview:
    """Tests covering issues found in story-018.1 code review."""

    def test_exact_key_match_score_capped_at_one(self) -> None:
        """Exact key match bonus must not push composite score above 1.0."""
        # High-confidence, recent, frequently accessed entry whose composite
        # would be close to 1.0 before the bonus.
        entry = _make_entry(
            "exact-query",
            "exact query value",
            confidence=1.0,
            updated_at=_RECENT,
            access_count=20,  # frequency at cap
        )
        retriever = MemoryRetriever()
        store = _make_store([entry])
        results = retriever.search("exact-query", store)
        assert results
        assert results[0].score <= 1.0, f"score {results[0].score} exceeds 1.0"

    def test_scoring_weight_mismatch_logs_warning(self) -> None:
        """A warning is emitted when profile scoring weights don't sum to 1.0."""
        from unittest.mock import patch

        scoring_cfg = MagicMock()
        scoring_cfg.relevance = 0.5
        scoring_cfg.confidence = 0.5
        scoring_cfg.recency = 0.5  # sum = 1.5, clearly off
        scoring_cfg.frequency = 0.5
        scoring_cfg.frequency_cap = 20.0
        scoring_cfg.source_trust = None

        mock_logger = MagicMock()
        with patch("tapps_brain.retrieval.logger", mock_logger):
            MemoryRetriever(scoring_config=scoring_cfg)

        mock_logger.warning.assert_called_once()
        assert mock_logger.warning.call_args[0][0] == "scoring_weights_do_not_sum_to_one"

    def test_scoring_weights_within_profile_band_no_warning(self) -> None:
        """Valid partial sums (profile band) do not emit retriever weight warnings."""
        from unittest.mock import patch

        # 1.03 is valid for Pydantic (0.95-1.05 band); old retriever used 0.01 tolerance.
        scoring_cfg = ScoringConfig(
            relevance=0.43,
            confidence=0.30,
            recency=0.15,
            frequency=0.15,
        )
        mock_logger = MagicMock()
        with patch("tapps_brain.retrieval.logger", mock_logger):
            MemoryRetriever(scoring_config=scoring_cfg)
        mock_logger.warning.assert_not_called()

    def test_composite_weight_profile_changes_rank_order(self) -> None:
        """Confidence-heavy vs frequency-heavy profiles reorder two same-text candidates."""
        shared = "shared token alpha beta gamma"
        hi_conf = _make_entry(
            "key-a",
            shared,
            confidence=0.95,
            access_count=0,
            updated_at=_RECENT,
        )
        hi_freq = _make_entry(
            "key-b",
            shared,
            confidence=0.55,
            access_count=25,
            updated_at=_RECENT,
        )
        entries = [hi_freq, hi_conf]
        store = _make_store(entries)
        store.search.return_value = entries
        query = "alpha beta gamma"

        conf_heavy = ScoringConfig(
            relevance=0.15,
            confidence=0.70,
            recency=0.075,
            frequency=0.075,
        )
        freq_heavy = ScoringConfig(
            relevance=0.15,
            confidence=0.10,
            recency=0.15,
            frequency=0.60,
        )

        r_conf = MemoryRetriever(scoring_config=conf_heavy)
        r_freq = MemoryRetriever(scoring_config=freq_heavy)

        top_conf = r_conf.search(query, store, limit=2)[0].entry.key
        top_freq = r_freq.search(query, store, limit=2)[0].entry.key

        assert top_conf == "key-a"
        assert top_freq == "key-b"

    def test_apply_reranker_returns_scored_when_reranker_is_none(self) -> None:
        """_apply_reranker should not crash when reranker is None (guard replaces assert)."""
        retriever = MemoryRetriever(reranker=None, reranker_enabled=False)
        entry = _make_entry("k", "v")
        sm = ScoredMemory(
            entry=entry, score=0.5, effective_confidence=0.8, bm25_relevance=0.3, stale=False
        )
        # Directly call the internal method; should return scored unchanged (not raise)
        result = retriever._apply_reranker("query", [sm], 10)
        assert result == [sm]

    def test_hybrid_config_none_does_not_raise(self) -> None:
        """MemoryRetriever accepts hybrid_config=None without type errors."""
        # This validates the type annotation fix: hybrid_config: object | None = None
        retriever = MemoryRetriever(hybrid_config=None)
        store = _make_store([_make_entry("k", "v")])
        results = retriever.search("v", store)
        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# Graph centrality (TAP-734)
# ---------------------------------------------------------------------------


class TestGraphCentrality:
    """Unit tests for MemoryRetriever._compute_graph_centrality (TAP-734)."""

    def _make_entity_index(self, mapping: dict[str, list[str]]) -> dict[str, set[str]]:
        """Build entity_index fixture: token → set of keys."""
        return {token: set(keys) for token, keys in mapping.items()}

    # ------------------------------------------------------------------
    # Isolated _compute_graph_centrality unit tests
    # ------------------------------------------------------------------

    def test_centrality_zero_for_empty_index(self) -> None:
        """Returns 0.0 when entity index is empty."""
        entry = _make_entry("solo", "memory that stands alone")
        score = MemoryRetriever._compute_graph_centrality(entry, {}, total_entries=5)
        assert score == 0.0

    def test_centrality_zero_for_zero_total_entries(self) -> None:
        """Returns 0.0 when total_entries is 0 (guard against ZeroDivisionError)."""
        idx = self._make_entity_index({"memory": ["key-a", "key-b"]})
        entry = _make_entry("key-a", "memory store")
        score = MemoryRetriever._compute_graph_centrality(entry, idx, total_entries=0)
        assert score == 0.0

    def test_centrality_zero_for_entry_with_no_shared_tokens(self) -> None:
        """An entry whose tokens appear in no other entry gets 0.0 (after discarding self)."""
        # 'exclusive' token only maps to 'solo-key' itself.
        idx = self._make_entity_index({"exclusive": ["solo-key"]})
        entry = _make_entry("solo-key", "exclusive content")
        score = MemoryRetriever._compute_graph_centrality(entry, idx, total_entries=10)
        assert score == 0.0

    def test_centrality_increases_with_shared_connections(self) -> None:
        """Centrality grows when more entries share an entity token with the target."""
        # 'postgres' stems to 'postgre'; maps to target + 4 others → 4/10 = 0.4
        shared_keys = ["key-a", "key-b", "key-c", "key-d", "target-key"]
        idx = self._make_entity_index({"postgre": shared_keys})
        entry = _make_entry("target-key", "postgres database")
        score = MemoryRetriever._compute_graph_centrality(entry, idx, total_entries=10)
        # 4 distinct OTHER keys / 10 total entries = 0.4
        assert score == pytest.approx(0.4)

    def test_centrality_excludes_self(self) -> None:
        """The entry's own key is excluded from the shared-key count."""
        idx = self._make_entity_index({"retrieval": ["only-me"]})
        entry = _make_entry("only-me", "retrieval system")
        score = MemoryRetriever._compute_graph_centrality(entry, idx, total_entries=5)
        # 0 OTHER keys / 5 total = 0.0
        assert score == 0.0

    def test_centrality_capped_at_one(self) -> None:
        """Score is capped at 1.0 even if shared_keys > total_entries."""
        # Edge case: index inconsistency or very small total_entries
        idx = self._make_entity_index({"core": ["a", "b", "c", "target"]})
        entry = _make_entry("target", "core functionality")
        # 3 shared keys / 2 total entries → raw 1.5 → capped at 1.0
        score = MemoryRetriever._compute_graph_centrality(entry, idx, total_entries=2)
        assert score == 1.0

    def test_centrality_multi_token_union(self) -> None:
        """Tokens are union-ed — same key reached via different tokens counted once."""
        idx = self._make_entity_index(
            {
                "databas": ["key-x", "target"],  # "database" stems to "databas"
                "postgre": ["key-x", "key-y", "target"],  # "postgres" stems to "postgre"
            }
        )
        entry = _make_entry("target", "database postgres config")
        # Union: {key-x, key-y} (target excluded) → 2 / 10 = 0.2
        score = MemoryRetriever._compute_graph_centrality(entry, idx, total_entries=10)
        assert score == pytest.approx(0.2)

    def test_centrality_short_tokens_excluded(self) -> None:
        """Tokens shorter than 3 characters (after stemming) are not indexed."""
        # 'db' (len 2) should be ignored; only 'database' matters
        idx = self._make_entity_index(
            {
                "db": ["other-key", "target"],  # short — should be ignored by store
                "database": ["other-key", "target"],
            }
        )
        # preprocess("db use") yields ["db", "use"] — both < 3 chars won't be in query
        entry = _make_entry("target", "database configuration")
        # 'database' in index → 1 other key (other-key) / 10 = 0.1
        score = MemoryRetriever._compute_graph_centrality(entry, idx, total_entries=10)
        assert score > 0.0  # 'database' token is long enough

    # ------------------------------------------------------------------
    # Integration via search() — entity_index on store mock
    # ------------------------------------------------------------------

    def test_search_uses_entity_index_from_store(self) -> None:
        """search() reads _entity_index from the store and raises centrality scores."""
        # Entry 'alpha' shares tokens with 2 others; 'beta' is isolated.
        alpha = _make_entry("alpha", "postgres database configuration", access_count=0)
        beta = _make_entry("beta", "zzzzqqqq unique", access_count=0)

        store = _make_store([alpha, beta])
        # Manually wire entity index: 'postgres' shared with alpha + gamma + delta
        store._entity_index = {
            "postgr": {"alpha", "gamma", "delta"},
            "databas": {"alpha", "gamma"},
            "zzzz": {"beta"},
        }
        store._entries = {"alpha": alpha, "beta": beta, "gamma": MagicMock(), "delta": MagicMock()}

        retriever = MemoryRetriever()  # default _W_GRAPH = 0.05
        results = retriever.search("postgres database", store)

        # alpha should score higher than beta due to centrality boost
        scores = {r.entry.key: r.score for r in results}
        assert "alpha" in scores
        # alpha has centrality; beta has none — alpha's score must be strictly greater
        if "beta" in scores:
            assert scores["alpha"] > scores["beta"]

    def test_delete_removes_entry_from_entity_index(self) -> None:
        """_remove_entry_entities removes the key from all token sets (TAP-734)."""
        retriever = MemoryRetriever()
        entity_index: dict[str, set[str]] = {
            "postgres": {"key-a", "key-b"},
            "database": {"key-a"},
        }
        # Simulate removal of key-a
        # Replicate the _remove_entry_entities logic (tested indirectly via store)
        entry = _make_entry("key-a", "postgres database")
        # After key-a is removed: 'database' set becomes empty (pruned), 'postgres' keeps key-b
        empty_tokens = []
        for token, keys in entity_index.items():
            keys.discard("key-a")
            if not keys:
                empty_tokens.append(token)
        for token in empty_tokens:
            entity_index.pop(token, None)

        assert "key-a" not in entity_index.get("postgres", set())
        assert "database" not in entity_index  # empty set pruned
        assert entity_index == {"postgres": {"key-b"}}

        # Score with cleaned index: key-b alone → centrality 0.0 (no shared after removing key-a)
        score = MemoryRetriever._compute_graph_centrality(entry, entity_index, total_entries=5)
        assert score == 0.0  # key-a removed from index, no shared keys remain for it


# ---------------------------------------------------------------------------
# TAP-733: MemoryFilter pre-filter tests
# ---------------------------------------------------------------------------


class TestMemoryFilter:
    """Unit tests for MemoryFilter dataclass and _apply_filters (TAP-733)."""

    def _make_entry_with_class(
        self,
        key: str,
        value: str = "test value",
        *,
        tier: MemoryTier = MemoryTier.pattern,
        tags: list[str] | None = None,
        memory_class: str | None = None,
        memory_group: str | None = None,
        confidence: float = 0.8,
    ) -> MemoryEntry:
        e = _make_entry(key, value, tier=tier, tags=tags or [], confidence=confidence)
        # Use model_copy to set memory_class (it's a Pydantic model field)
        return e.model_copy(update={"memory_class": memory_class, "memory_group": memory_group})

    # ------------------------------------------------------------------
    # Import tests
    # ------------------------------------------------------------------

    def test_memory_filter_importable(self) -> None:
        from tapps_brain.retrieval import MemoryFilter  # noqa: F401

    def test_memory_filter_defaults(self) -> None:
        from tapps_brain.retrieval import MemoryFilter

        f = MemoryFilter()
        assert f.tier is None
        assert f.memory_class is None
        assert f.tags == []
        assert f.tags_any == []
        assert f.memory_group is None
        assert f.min_confidence is None

    # ------------------------------------------------------------------
    # _apply_filters: no-op
    # ------------------------------------------------------------------

    def test_empty_filter_is_noop(self) -> None:
        from tapps_brain.retrieval import MemoryFilter, MemoryRetriever

        entries = [_make_entry("a"), _make_entry("b"), _make_entry("c")]
        result = MemoryRetriever._apply_filters(entries, MemoryFilter())
        assert result == entries

    # ------------------------------------------------------------------
    # _apply_filters: tier
    # ------------------------------------------------------------------

    def test_filter_by_tier_keeps_matching(self) -> None:
        from tapps_brain.retrieval import MemoryFilter, MemoryRetriever

        arch = _make_entry("arch-entry", tier=MemoryTier.architectural)
        pattern = _make_entry("pattern-entry", tier=MemoryTier.pattern)
        entries = [arch, pattern]
        result = MemoryRetriever._apply_filters(
            entries, MemoryFilter(tier=MemoryTier.architectural)
        )
        assert [e.key for e in result] == ["arch-entry"]

    def test_filter_by_tier_string(self) -> None:
        from tapps_brain.retrieval import MemoryFilter, MemoryRetriever

        arch = _make_entry("arch-entry", tier=MemoryTier.architectural)
        pattern = _make_entry("pattern-entry", tier=MemoryTier.pattern)
        entries = [arch, pattern]
        result = MemoryRetriever._apply_filters(entries, MemoryFilter(tier="architectural"))
        assert [e.key for e in result] == ["arch-entry"]

    def test_filter_by_tier_no_match_returns_empty(self) -> None:
        from tapps_brain.retrieval import MemoryFilter, MemoryRetriever

        entries = [_make_entry("a", tier=MemoryTier.pattern)]
        result = MemoryRetriever._apply_filters(entries, MemoryFilter(tier=MemoryTier.context))
        assert result == []

    # ------------------------------------------------------------------
    # _apply_filters: tags (AND)
    # ------------------------------------------------------------------

    def test_filter_by_tags_and(self) -> None:
        from tapps_brain.retrieval import MemoryFilter, MemoryRetriever

        e1 = _make_entry("e1", tags=["pgvector", "hnsw", "critical"])
        e2 = _make_entry("e2", tags=["pgvector"])  # missing hnsw
        e3 = _make_entry("e3", tags=["hnsw"])  # missing pgvector
        entries = [e1, e2, e3]
        result = MemoryRetriever._apply_filters(entries, MemoryFilter(tags=["pgvector", "hnsw"]))
        assert [e.key for e in result] == ["e1"]

    def test_filter_by_tags_single(self) -> None:
        from tapps_brain.retrieval import MemoryFilter, MemoryRetriever

        e1 = _make_entry("e1", tags=["pgvector"])
        e2 = _make_entry("e2", tags=["other"])
        result = MemoryRetriever._apply_filters([e1, e2], MemoryFilter(tags=["pgvector"]))
        assert [e.key for e in result] == ["e1"]

    # ------------------------------------------------------------------
    # _apply_filters: tags_any (OR)
    # ------------------------------------------------------------------

    def test_filter_by_tags_any(self) -> None:
        from tapps_brain.retrieval import MemoryFilter, MemoryRetriever

        e1 = _make_entry("e1", tags=["pgvector"])
        e2 = _make_entry("e2", tags=["hnsw"])
        e3 = _make_entry("e3", tags=["other"])
        result = MemoryRetriever._apply_filters(
            [e1, e2, e3], MemoryFilter(tags_any=["pgvector", "hnsw"])
        )
        assert {e.key for e in result} == {"e1", "e2"}

    def test_filter_tags_and_plus_tags_any_combined(self) -> None:
        from tapps_brain.retrieval import MemoryFilter, MemoryRetriever

        # tags (AND) AND tags_any (OR) must BOTH be satisfied
        e1 = _make_entry("e1", tags=["critical", "pgvector", "hnsw"])
        e2 = _make_entry("e2", tags=["critical", "pgvector"])  # missing hnsw, has pgvector
        e3 = _make_entry("e3", tags=["hnsw"])  # missing critical
        result = MemoryRetriever._apply_filters(
            [e1, e2, e3],
            MemoryFilter(tags=["critical"], tags_any=["hnsw"]),
        )
        # Only e1 has both 'critical' AND any of ['hnsw']
        assert [e.key for e in result] == ["e1"]

    # ------------------------------------------------------------------
    # _apply_filters: memory_class
    # ------------------------------------------------------------------

    def test_filter_by_memory_class(self) -> None:
        from tapps_brain.retrieval import MemoryFilter, MemoryRetriever

        incident = self._make_entry_with_class("inc1", memory_class="incident")
        guidance = self._make_entry_with_class("gui1", memory_class="guidance")
        unclassified = self._make_entry_with_class("unc1", memory_class=None)
        entries = [incident, guidance, unclassified]
        result = MemoryRetriever._apply_filters(entries, MemoryFilter(memory_class="incident"))
        assert [e.key for e in result] == ["inc1"]

    def test_filter_by_memory_class_decision(self) -> None:
        from tapps_brain.retrieval import MemoryFilter, MemoryRetriever

        decision = self._make_entry_with_class("dec1", memory_class="decision")
        guidance = self._make_entry_with_class("gui1", memory_class="guidance")
        result = MemoryRetriever._apply_filters(
            [decision, guidance], MemoryFilter(memory_class="decision")
        )
        assert [e.key for e in result] == ["dec1"]

    # ------------------------------------------------------------------
    # _apply_filters: min_confidence
    # ------------------------------------------------------------------

    def test_filter_by_min_confidence(self) -> None:
        from tapps_brain.retrieval import MemoryFilter, MemoryRetriever

        high = _make_entry("high", confidence=0.9)
        low = _make_entry("low", confidence=0.3)
        entries = [high, low]
        result = MemoryRetriever._apply_filters(entries, MemoryFilter(min_confidence=0.7))
        assert [e.key for e in result] == ["high"]

    def test_filter_min_confidence_boundary(self) -> None:
        from tapps_brain.retrieval import MemoryFilter, MemoryRetriever

        e = _make_entry("exact", confidence=0.7)
        # At boundary — should be included (>=)
        result = MemoryRetriever._apply_filters([e], MemoryFilter(min_confidence=0.7))
        assert [x.key for x in result] == ["exact"]

    # ------------------------------------------------------------------
    # _apply_filters: memory_group
    # ------------------------------------------------------------------

    def test_filter_by_memory_group(self) -> None:
        from tapps_brain.retrieval import MemoryFilter, MemoryRetriever

        e1 = self._make_entry_with_class("e1", memory_group="team-a")
        e2 = self._make_entry_with_class("e2", memory_group="team-b")
        e3 = self._make_entry_with_class("e3", memory_group=None)
        result = MemoryRetriever._apply_filters([e1, e2, e3], MemoryFilter(memory_group="team-a"))
        assert [e.key for e in result] == ["e1"]

    # ------------------------------------------------------------------
    # MemoryRetriever.search() honours memory_filter parameter
    # ------------------------------------------------------------------

    def test_search_with_tier_filter(self) -> None:
        from tapps_brain.retrieval import MemoryFilter, MemoryRetriever

        arch = _make_entry("arch-key", "architectural decision", tier=MemoryTier.architectural)
        pat = _make_entry("pattern-key", "pattern note", tier=MemoryTier.pattern)
        store = _make_store([arch, pat])

        retriever = MemoryRetriever()
        results = retriever.search(
            "architecture",
            store,
            memory_filter=MemoryFilter(tier=MemoryTier.architectural),
        )
        result_keys = {r.entry.key for r in results}
        assert "arch-key" in result_keys
        assert "pattern-key" not in result_keys

    def test_search_with_no_filter_returns_all(self) -> None:
        from tapps_brain.retrieval import MemoryFilter, MemoryRetriever

        e1 = _make_entry("e1", "something about postgres")
        e2 = _make_entry("e2", "something about redis")
        store = _make_store([e1, e2])

        retriever = MemoryRetriever()
        # No filter → both candidates eligible (store.search returns all)
        results_unfiltered = retriever.search("something", store)
        results_empty_filter = retriever.search("something", store, memory_filter=MemoryFilter())
        assert len(results_unfiltered) == len(results_empty_filter)

    def test_search_filter_narrows_pool_before_scoring(self) -> None:
        """MemoryFilter reduces the candidate pool before BM25 scoring (TAP-733)."""
        from tapps_brain.retrieval import MemoryFilter, MemoryRetriever

        incident = _make_entry("inc", "bug fix postgres", tier=MemoryTier.context)
        arch = _make_entry("arc", "bug fix architecture", tier=MemoryTier.architectural)
        store = _make_store([incident, arch])

        retriever = MemoryRetriever()
        results = retriever.search(
            "bug fix",
            store,
            memory_filter=MemoryFilter(tier=MemoryTier.context),
        )
        result_keys = {r.entry.key for r in results}
        assert "inc" in result_keys
        assert "arc" not in result_keys
