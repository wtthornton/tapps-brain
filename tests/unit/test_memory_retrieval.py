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

    def test_normalize_relevance_positive(self) -> None:
        # BM25 normalization: score / (score + 5.0)
        retriever = MemoryRetriever()
        score = retriever._normalize_relevance(5.0)
        assert 0.0 < score < 1.0
        assert score == pytest.approx(0.5)

    def test_normalize_relevance_large(self) -> None:
        retriever = MemoryRetriever()
        score = retriever._normalize_relevance(100.0)
        assert score > 0.9

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
        """A rare term should produce higher BM25 relevance than a common one."""
        entries = [
            _make_entry("python-setup", "python setup guide", confidence=0.8),
            _make_entry("python-web", "python web framework", confidence=0.8),
            _make_entry("python-data", "python data science", confidence=0.8),
            _make_entry("rust-setup", "rust setup guide", confidence=0.8),
        ]
        retriever = MemoryRetriever()
        store = _make_store(entries)

        # "rust" is rare (1 doc), "python" is common (3 docs)
        rust_results = retriever.search("rust", store)
        python_results = retriever.search("python", store)

        # The rust match should have higher BM25 relevance
        assert len(rust_results) >= 1
        assert len(python_results) >= 1
        assert rust_results[0].bm25_relevance > python_results[0].bm25_relevance

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
        )
        store = _make_store(entries)

        results = retriever.search("python testing database", store, limit=3)
        assert len(results) >= 1
        # ReverseReranker puts last candidate first
        assert results[0].entry.key == "key-c"

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
        scoring_cfg.bm25_norm_k = 5.0
        scoring_cfg.source_trust = None

        mock_logger = MagicMock()
        with patch("tapps_brain.retrieval.logger", mock_logger):
            MemoryRetriever(scoring_config=scoring_cfg)

        mock_logger.warning.assert_called_once()
        assert mock_logger.warning.call_args[0][0] == "scoring_weights_do_not_sum_to_one"

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
