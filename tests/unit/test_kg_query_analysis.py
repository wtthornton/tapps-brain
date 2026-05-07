"""Unit tests for STORY-076.1 entity-mention extraction + resolver wiring.

TAP-1498 acceptance criteria:
  - analyze_query() returns QueryAnalysis(mentions=[...], unmatched=[...])
    where each mention has surface, entity_id, confidence, reason.
  - Extraction uses entity-name regex + alias-table lookup, not an LLM.
  - Resolver lookup batched per query (single SQL round-trip via batch_resolve_entities).
  - No regression when kg_backend is None (memory-only path).
  - Paths covered: empty-graph, exact-match, alias-match, ambiguous-match.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tapps_brain.kg_query_analysis import (
    EntityMention,
    QueryAnalysis,
    _extract_candidates,
    analyze_query,
)

# ---------------------------------------------------------------------------
# _extract_candidates unit tests
# ---------------------------------------------------------------------------


class TestExtractCandidates:
    def test_empty_query(self) -> None:
        assert _extract_candidates("") == []

    def test_short_words_excluded(self) -> None:
        # Words < 4 chars should be excluded
        result = _extract_candidates("the cat sat on a mat")
        assert not any(len(c) < 4 for c in result)

    def test_stopwords_excluded(self) -> None:
        result = _extract_candidates("what are the options for this task")
        low = {c.lower() for c in result}
        stopwords = {"what", "are", "the", "for", "this"}
        assert low.isdisjoint(stopwords)

    def test_title_case_phrase_captured(self) -> None:
        result = _extract_candidates("The Memory Retriever handles recall")
        low = {c.lower() for c in result}
        # The regex may include a leading "The" in the phrase; check membership
        assert any("memory retriever" in c for c in low)

    def test_single_meaningful_word(self) -> None:
        result = _extract_candidates("explain postgres")
        low = {c.lower() for c in result}
        assert "postgres" in low
        assert "explain" in low

    def test_deduplication(self) -> None:
        result = _extract_candidates("postgres postgres postgres")
        assert result.count("postgres") == 1

    def test_caps_at_max_candidates(self) -> None:
        # 80 unique words > _MAX_CANDIDATES (64)
        words = " ".join(f"Word{i:04d}" for i in range(80))
        result = _extract_candidates(words)
        assert len(result) <= 64

    def test_mixed_case_preserved_in_surface(self) -> None:
        result = _extract_candidates("PostgreSQL database")
        assert any("PostgreSQL" in c or "postgresql" == c.lower() for c in result)


# ---------------------------------------------------------------------------
# analyze_query — no-KG path
# ---------------------------------------------------------------------------


class TestAnalyzeQueryNoBackend:
    def test_returns_empty_analysis_when_no_backend(self) -> None:
        result = analyze_query("find all PostgreSQL entities", kg_backend=None)
        assert isinstance(result, QueryAnalysis)
        assert result.mentions == []
        assert result.unmatched == []
        assert result.matched_count == 0
        assert result.unmatched_count == 0

    def test_empty_query_no_backend(self) -> None:
        result = analyze_query("", kg_backend=None)
        assert result.mentions == []


# ---------------------------------------------------------------------------
# analyze_query — with a mocked KG backend
# ---------------------------------------------------------------------------


def _make_kg_backend(
    resolved: dict[str, tuple[str, float, str]],
) -> MagicMock:
    """Build a mock KnowledgeGraphBackend whose batch_resolve_entities returns *resolved*."""
    backend = MagicMock()
    backend.batch_resolve_entities.return_value = resolved
    return backend


class TestAnalyzeQueryEmptyGraph:
    """Empty KG → all candidates unmatched."""

    def test_all_unmatched_when_kg_empty(self) -> None:
        backend = _make_kg_backend({})
        result = analyze_query("find PostgreSQL entities", kg_backend=backend)

        assert result.mentions == []
        assert result.matched_count == 0
        assert result.unmatched_count > 0
        # batch_resolve_entities was called once
        backend.batch_resolve_entities.assert_called_once()

    def test_unmatched_list_contains_candidates(self) -> None:
        backend = _make_kg_backend({})
        result = analyze_query("PostgreSQL database migration", kg_backend=backend)
        low_unmatched = {s.lower() for s in result.unmatched}
        assert "postgresql" in low_unmatched or "database" in low_unmatched


class TestAnalyzeQueryExactMatch:
    """Exact canonical-name matches."""

    def test_exact_match_returns_mention(self) -> None:
        backend = _make_kg_backend(
            {"postgresql": ("uuid-pg-001", 0.95, "exact_match")}
        )
        result = analyze_query("How does PostgreSQL handle indexing?", kg_backend=backend)

        assert result.matched_count >= 1
        mention = next(m for m in result.mentions if m.entity_id == "uuid-pg-001")
        assert mention.surface.lower() == "postgresql"
        assert mention.confidence == pytest.approx(0.95)
        assert mention.reason == "exact_match"

    def test_multiple_exact_matches(self) -> None:
        backend = _make_kg_backend(
            {
                "postgresql": ("uuid-pg", 0.9, "exact_match"),
                "pgvector": ("uuid-pv", 0.85, "exact_match"),
            }
        )
        result = analyze_query("PostgreSQL uses pgvector for search", kg_backend=backend)

        entity_ids = {m.entity_id for m in result.mentions}
        assert "uuid-pg" in entity_ids
        assert "uuid-pv" in entity_ids

    def test_batch_called_with_candidates(self) -> None:
        backend = _make_kg_backend({"postgres": ("uuid-1", 0.9, "exact_match")})
        analyze_query("postgres backup", kg_backend=backend)

        call_args = backend.batch_resolve_entities.call_args[0][0]
        # Should pass a list of candidates
        assert isinstance(call_args, list)
        assert any("postgres" in c.lower() for c in call_args)


class TestAnalyzeQueryAliasMatch:
    """Alias-table matches."""

    def test_alias_match_returns_mention(self) -> None:
        backend = _make_kg_backend(
            {"psql": ("uuid-pg-001", 0.75, "alias_match")}
        )
        # "psql" is 4 chars and passes the _MIN_CANDIDATE_LEN filter
        result = analyze_query("migrate using psql connection", kg_backend=backend)

        assert result.matched_count >= 1
        mention = next(
            (m for m in result.mentions if m.reason == "alias_match"), None
        )
        assert mention is not None
        assert mention.entity_id == "uuid-pg-001"
        assert mention.confidence == pytest.approx(0.75)

    def test_alias_match_reason_preserved(self) -> None:
        backend = _make_kg_backend(
            {"psql": ("uuid-pg-001", 0.70, "alias_match")}
        )
        result = analyze_query("psql connection string", kg_backend=backend)
        reasons = {m.reason for m in result.mentions}
        assert "alias_match" in reasons


class TestAnalyzeQueryAmbiguousMatch:
    """Ambiguous alias (>1 entity per candidate)."""

    def test_ambiguous_alias_returns_highest_confidence(self) -> None:
        backend = _make_kg_backend(
            {"migration": ("uuid-mig-best", 0.80, "ambiguous_alias")}
        )
        result = analyze_query("run migration scripts", kg_backend=backend)

        # The batch resolver already resolved ambiguity; analyze_query just forwards.
        ambiguous = [m for m in result.mentions if m.reason == "ambiguous_alias"]
        assert len(ambiguous) >= 1
        assert ambiguous[0].entity_id == "uuid-mig-best"

    def test_ambiguous_still_counted_as_matched(self) -> None:
        backend = _make_kg_backend(
            {"postgres": ("uuid-a", 0.60, "ambiguous_alias")}
        )
        result = analyze_query("postgres configuration", kg_backend=backend)
        assert result.matched_count >= 1


# ---------------------------------------------------------------------------
# analyze_query — error handling
# ---------------------------------------------------------------------------


class TestAnalyzeQueryErrorHandling:
    """Backend errors must not propagate."""

    def test_backend_exception_returns_empty_analysis(self) -> None:
        backend = MagicMock()
        backend.batch_resolve_entities.side_effect = RuntimeError("DB down")

        result = analyze_query("postgres query", kg_backend=backend)

        assert isinstance(result, QueryAnalysis)
        assert result.mentions == []
        # unmatched is also empty — we couldn't even confirm the candidates
        assert result.unmatched == []

    def test_empty_candidate_list_skips_db_call(self) -> None:
        """Short / stopword-only queries should not hit the DB."""
        backend = MagicMock()
        result = analyze_query("the for are", kg_backend=backend)

        backend.batch_resolve_entities.assert_not_called()
        assert result.mentions == []


# ---------------------------------------------------------------------------
# QueryAnalysis helpers
# ---------------------------------------------------------------------------


class TestQueryAnalysisModel:
    def test_matched_count(self) -> None:
        qa = QueryAnalysis(
            mentions=[
                EntityMention("pg", "uuid-1", 0.9, "exact_match"),
                EntityMention("psql", "uuid-2", 0.7, "alias_match"),
            ],
            unmatched=["random"],
        )
        assert qa.matched_count == 2
        assert qa.unmatched_count == 1

    def test_empty_analysis(self) -> None:
        qa = QueryAnalysis()
        assert qa.matched_count == 0
        assert qa.unmatched_count == 0
