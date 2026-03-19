"""Tests for memory.bm25 — BM25 scoring engine."""

from __future__ import annotations

import math

from tapps_brain.bm25 import (
    _STOP_WORDS,
    BM25Scorer,
    preprocess,
    stem,
)

# ---------------------------------------------------------------------------
# stem()
# ---------------------------------------------------------------------------


class TestStem:
    def test_strips_ing(self) -> None:
        assert stem("running") == "runn"

    def test_strips_ed(self) -> None:
        assert stem("walked") == "walk"

    def test_strips_ly(self) -> None:
        assert stem("quickly") == "quick"

    def test_strips_tion(self) -> None:
        assert stem("creation") == "crea"

    def test_strips_ment(self) -> None:
        assert stem("development") == "develop"

    def test_strips_ness(self) -> None:
        assert stem("darkness") == "dark"

    def test_strips_er(self) -> None:
        assert stem("faster") == "fast"

    def test_strips_est(self) -> None:
        assert stem("fastest") == "fast"

    def test_strips_s(self) -> None:
        assert stem("tests") == "test"

    def test_no_strip_short_word(self) -> None:
        # "us" - stripping "s" would leave "u" (< 3 chars)
        assert stem("us") == "us"

    def test_no_strip_when_stem_too_short(self) -> None:
        # "bing" - stripping "ing" leaves "b" (< 3 chars)
        assert stem("bing") == "bing"

    def test_no_matching_suffix(self) -> None:
        assert stem("python") == "python"


# ---------------------------------------------------------------------------
# preprocess()
# ---------------------------------------------------------------------------


class TestPreprocess:
    def test_lowercases(self) -> None:
        result = preprocess("Hello World")
        assert "hello" in result
        assert "world" in result

    def test_removes_stop_words(self) -> None:
        result = preprocess("the quick brown fox")
        assert "the" not in result
        # "quick" stems to "quick" (stripping "ly" would leave "quick" but no "ly" suffix)
        assert "quick" in result

    def test_stems_tokens(self) -> None:
        result = preprocess("running tests quickly")
        assert "runn" in result
        assert "test" in result
        assert "quick" in result

    def test_empty_input(self) -> None:
        assert preprocess("") == []

    def test_only_stop_words(self) -> None:
        assert preprocess("the a an is") == []

    def test_strips_punctuation(self) -> None:
        result = preprocess("hello, world! foo-bar")
        assert result == ["hello", "world", "foo", "bar"]


# ---------------------------------------------------------------------------
# Stop words sanity
# ---------------------------------------------------------------------------


class TestStopWords:
    def test_contains_common_words(self) -> None:
        for word in ("the", "is", "and", "of", "to", "in", "a"):
            assert word in _STOP_WORDS

    def test_count_at_least_50(self) -> None:
        assert len(_STOP_WORDS) >= 50


# ---------------------------------------------------------------------------
# BM25Scorer
# ---------------------------------------------------------------------------


class TestBM25Scorer:
    def test_default_params(self) -> None:
        scorer = BM25Scorer()
        assert scorer.k1 == 1.2
        assert scorer.b == 0.75

    def test_build_index_sets_doc_count(self) -> None:
        scorer = BM25Scorer()
        scorer.build_index(["doc one", "doc two", "doc three"])
        assert scorer._doc_count == 3

    def test_score_returns_per_doc_scores(self) -> None:
        scorer = BM25Scorer()
        scorer.build_index(["python testing", "java testing", "python coding"])
        scores = scorer.score("python")
        assert len(scores) == 3
        # Docs 0 and 2 mention "python", doc 1 does not
        assert scores[0] > 0
        assert scores[1] == 0.0
        assert scores[2] > 0

    def test_idf_weighting(self) -> None:
        """A rare term should score higher than a common term."""
        scorer = BM25Scorer()
        docs = [
            "python programming language",
            "python web framework",
            "python data science",
            "rust programming language",
        ]
        scorer.build_index(docs)
        # "rust" appears in 1 doc, "python" in 3 → rust has higher IDF
        scores_rare = scorer.score("rust")
        scores_common = scorer.score("python")
        # Doc 3 (rust) should score higher for "rust" than any doc for "python"
        assert max(scores_rare) > max(scores_common)

    def test_tf_saturation(self) -> None:
        """Repeated terms should have diminishing returns."""
        scorer = BM25Scorer()
        docs = [
            "python",
            "python python python python python",
        ]
        scorer.build_index(docs)
        scores = scorer.score("python")
        # Doc 1 has more occurrences but score should not be 5x doc 0
        assert scores[1] > scores[0]
        assert scores[1] < scores[0] * 5

    def test_empty_query(self) -> None:
        scorer = BM25Scorer()
        scorer.build_index(["hello world"])
        assert scorer.score("") == [0.0]

    def test_empty_index(self) -> None:
        scorer = BM25Scorer()
        scorer.build_index([])
        assert scorer.score("hello") == []

    def test_query_with_only_stop_words(self) -> None:
        scorer = BM25Scorer()
        scorer.build_index(["hello world"])
        assert scorer.score("the a an") == [0.0]

    def test_score_batch(self) -> None:
        scorer = BM25Scorer()
        scorer.build_index(["python testing", "java testing"])
        results = scorer.score_batch(["python", "java"])
        assert len(results) == 2
        assert results[0][0] > 0  # "python" matches doc 0
        assert results[1][1] > 0  # "java" matches doc 1

    def test_longer_doc_normalization(self) -> None:
        """BM25 should normalize for document length."""
        scorer = BM25Scorer()
        short = "python"
        long_doc = "python " + " ".join(f"word{i}" for i in range(50))
        scorer.build_index([short, long_doc])
        scores = scorer.score("python")
        # Short doc should score higher (same tf, shorter length)
        assert scores[0] > scores[1]

    def test_idf_formula_correctness(self) -> None:
        """Verify IDF matches the expected formula."""
        scorer = BM25Scorer()
        scorer.build_index(["alpha beta", "alpha gamma", "delta epsilon"])
        n = 3
        # "alpha" appears in 2 docs
        expected_idf = math.log((n - 2 + 0.5) / (2 + 0.5) + 1.0)
        assert abs(scorer._idf["alpha"] - expected_idf) < 1e-10
        # "delta" appears in 1 doc
        expected_idf_rare = math.log((n - 1 + 0.5) / (1 + 0.5) + 1.0)
        assert abs(scorer._idf["delta"] - expected_idf_rare) < 1e-10

    def test_custom_k1_b(self) -> None:
        scorer = BM25Scorer(k1=2.0, b=0.5)
        scorer.build_index(["hello world", "hello there"])
        scores = scorer.score("hello")
        assert all(s >= 0 for s in scores)

    def test_multi_term_query(self) -> None:
        scorer = BM25Scorer()
        scorer.build_index(
            [
                "python testing framework",
                "java testing library",
                "python web development",
            ]
        )
        scores = scorer.score("python testing")
        # Doc 0 matches both terms, should score highest
        assert scores[0] > scores[1]
        assert scores[0] > scores[2]
