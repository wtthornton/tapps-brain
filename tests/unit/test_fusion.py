"""Additional boundary and branch tests for fusion.py (TAP-1820).

Complements tests/unit/test_memory_fusion.py with:

- Single-item list boundary cases
- All-tie score tie-breaking behaviour
- Hand-computed 5-element RRF fixture (k=60)
- ``hybrid_rrf_weights_for_query`` for short (1–2 token), vague (3–4 token),
  medium (5–7 token), and long (8+ token) queries
- Numeric / mixed alpha-numeric token boost path
- ``what's`` / ``how's`` question prefix branch
- ``similar to`` / ``related to`` semantic bias phrases
"""

from __future__ import annotations

import pytest

from tapps_brain.fusion import (
    _ALPHA_HI,
    _ALPHA_LO,
    hybrid_rrf_weights_for_query,
    reciprocal_rank_fusion,
    reciprocal_rank_fusion_weighted,
)

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _scores(result: list[tuple[str, float]]) -> dict[str, float]:
    return dict(result)


# ---------------------------------------------------------------------------
# Single-item list boundary cases
# ---------------------------------------------------------------------------


class TestRRFSingleItemLists:
    """Boundary cases where one or both ranked lists have exactly one item."""

    def test_single_item_bm25_only(self) -> None:
        """One BM25 item, empty vector list → score = 1/(k+1)."""
        result = reciprocal_rank_fusion(["solo"], [], k=60)
        assert len(result) == 1
        assert result[0][0] == "solo"
        assert result[0][1] == pytest.approx(1 / 61)

    def test_single_item_vector_only(self) -> None:
        """Empty BM25 list, one vector item → score = 1/(k+1)."""
        result = reciprocal_rank_fusion([], ["solo"], k=60)
        assert len(result) == 1
        assert result[0][0] == "solo"
        assert result[0][1] == pytest.approx(1 / 61)

    def test_single_item_same_key_both_lists(self) -> None:
        """Same key at rank-1 in both lists → scores accumulate to 2/(k+1)."""
        result = reciprocal_rank_fusion(["alpha"], ["alpha"], k=60)
        assert len(result) == 1
        assert result[0][0] == "alpha"
        assert result[0][1] == pytest.approx(2 / 61)

    def test_single_item_different_keys_tie_broken_alphabetically(self) -> None:
        """One distinct key per list, equal scores → tie broken by key string."""
        result = reciprocal_rank_fusion(["bravo"], ["alpha"], k=60)
        assert len(result) == 2
        s = _scores(result)
        assert s["alpha"] == pytest.approx(1 / 61)
        assert s["bravo"] == pytest.approx(1 / 61)
        # "alpha" < "bravo" alphabetically — must appear first.
        assert result[0][0] == "alpha"
        assert result[1][0] == "bravo"


# ---------------------------------------------------------------------------
# All-tie score tie-breaking
# ---------------------------------------------------------------------------


class TestRRFAllTieScores:
    """When multiple docs share the same fused score, alphabetical key order wins."""

    def test_two_keys_rank1_each_tie_broken_alphabetically(self) -> None:
        """'z' in BM25 and 'a' in vector are both rank-1 → scores equal → 'a' first."""
        result = reciprocal_rank_fusion(["z"], ["a"], k=60)
        s = _scores(result)
        assert s["a"] == pytest.approx(s["z"])
        assert result[0][0] == "a"
        assert result[1][0] == "z"

    def test_symmetric_pair_produces_equal_scores(self) -> None:
        """BM25=[a,b], vector=[b,a] → a and b get the same score."""
        result = reciprocal_rank_fusion(["a", "b"], ["b", "a"], k=60)
        s = _scores(result)
        assert s["a"] == pytest.approx(s["b"])
        # Tie-break: "a" < "b"
        assert result[0][0] == "a"

    def test_all_docs_at_equal_rank_in_one_list_differ_by_position(self) -> None:
        """Docs have *different* scores within a single list; nothing is a tie."""
        result = reciprocal_rank_fusion([], ["x", "y", "z"], k=60)
        s = _scores(result)
        # Each score is strictly decreasing: 1/61 > 1/62 > 1/63
        assert s["x"] > s["y"] > s["z"]


# ---------------------------------------------------------------------------
# Hand-computed 5-element fixture (k=60)
# ---------------------------------------------------------------------------


class TestRRFHandComputedFixture:
    """Verify the exact k=60 RRF formula against hand-computed expected values.

    BM25  : [a, b, c, d, e]  → 1-based ranks 1…5
    Vector: [e, d, c, b, a]  → 1-based ranks 1…5 (reversed)

    Hand-computed fused scores (bm25_weight = vector_weight = 1):
        a: 1/61 + 1/65  ≈ 0.031778
        b: 1/62 + 1/64  ≈ 0.031754
        c: 2/63         ≈ 0.031746
        d: 1/64 + 1/62  ≈ 0.031754  (same as b)
        e: 1/65 + 1/61  ≈ 0.031778  (same as a)

    Score ordering: a = e > b = d > c
    Tie-break by key: a before e, b before d → final order [a, e, b, d, c].
    """

    def test_scores_match_formula(self) -> None:
        bm25 = ["a", "b", "c", "d", "e"]
        vector = ["e", "d", "c", "b", "a"]
        result = reciprocal_rank_fusion(bm25, vector, k=60)
        s = _scores(result)

        assert s["a"] == pytest.approx(1 / 61 + 1 / 65)
        assert s["e"] == pytest.approx(1 / 65 + 1 / 61)
        assert s["b"] == pytest.approx(1 / 62 + 1 / 64)
        assert s["d"] == pytest.approx(1 / 64 + 1 / 62)
        assert s["c"] == pytest.approx(2 / 63)

    def test_score_ordering_group_by_group(self) -> None:
        bm25 = ["a", "b", "c", "d", "e"]
        vector = ["e", "d", "c", "b", "a"]
        result = reciprocal_rank_fusion(bm25, vector, k=60)
        s = _scores(result)

        # a=e group beats b=d group which beats c
        assert s["a"] > s["b"] > s["c"]
        assert s["e"] > s["d"] > s["c"]
        assert s["a"] == pytest.approx(s["e"])
        assert s["b"] == pytest.approx(s["d"])

    def test_final_order_with_tie_breaking(self) -> None:
        bm25 = ["a", "b", "c", "d", "e"]
        vector = ["e", "d", "c", "b", "a"]
        result = reciprocal_rank_fusion(bm25, vector, k=60)
        assert len(result) == 5
        keys = [k for k, _ in result]

        # Within the high-score group {a, e}: a < e alphabetically
        assert keys.index("a") < keys.index("e")
        # Within the mid-score group {b, d}: b < d alphabetically
        assert keys.index("b") < keys.index("d")
        # High group precedes mid group precedes c
        assert max(keys.index("a"), keys.index("e")) < min(keys.index("b"), keys.index("d"))
        assert keys.index("c") == 4

    def test_k60_rank1_score(self) -> None:
        """Rank-1 in one list with k=60 → 1/(60+1) = 1/61."""
        result = reciprocal_rank_fusion(["first"], [], k=60)
        assert result[0][1] == pytest.approx(1 / 61)

    def test_k60_rank1_both_lists_score(self) -> None:
        """Rank-1 in both lists with k=60 → 2/61."""
        result = reciprocal_rank_fusion(["top"], ["top"], k=60)
        assert result[0][1] == pytest.approx(2 / 61)


# ---------------------------------------------------------------------------
# hybrid_rrf_weights_for_query — short / vague / medium / long query lengths
# ---------------------------------------------------------------------------


class TestHybridWeightsQueryLength:
    """Branch coverage for the n-based length heuristic."""

    def test_single_word_favors_vector(self) -> None:
        """n=1 ≤ _LEN_SHORT_QUERY(2) → bias −=2 → vector weight wins."""
        bm25_w, vec_w = hybrid_rrf_weights_for_query("authentication")
        assert vec_w > bm25_w
        assert bm25_w + vec_w == pytest.approx(1.0)

    def test_two_word_favors_vector(self) -> None:
        """n=2 == _LEN_SHORT_QUERY → bias −=2."""
        bm25_w, vec_w = hybrid_rrf_weights_for_query("memory recall")
        assert vec_w > bm25_w
        assert bm25_w + vec_w == pytest.approx(1.0)

    def test_three_word_slightly_favors_vector(self) -> None:
        """n=3 falls into _LEN_VAGUE_QUERY branch (3 ≤ 4) → bias −=1."""
        bm25_w, vec_w = hybrid_rrf_weights_for_query("store recall entries")
        assert vec_w > bm25_w

    def test_four_word_slightly_favors_vector(self) -> None:
        """n=4 == _LEN_VAGUE_QUERY → bias −=1."""
        bm25_w, vec_w = hybrid_rrf_weights_for_query("store recall all entries")
        assert vec_w > bm25_w

    def test_five_word_leans_bm25(self) -> None:
        """n=5 == _LEN_MEDIUM_QUERY → bias +=1 → BM25 weight wins."""
        bm25_w, vec_w = hybrid_rrf_weights_for_query("store recall entries by key")
        assert bm25_w > vec_w

    def test_six_word_leans_bm25(self) -> None:
        """n=6, between MEDIUM and LONG → bias +=1."""
        bm25_w, vec_w = hybrid_rrf_weights_for_query("store recall entries by project key")
        assert bm25_w > vec_w

    def test_eight_word_strongly_favors_bm25(self) -> None:
        """n=8 == _LEN_LONG_QUERY → bias +=2."""
        bm25_w, vec_w = hybrid_rrf_weights_for_query(
            "memory store recall entries by project agent key"
        )
        assert bm25_w > vec_w

    def test_weights_sum_to_one_for_all_lengths(self) -> None:
        """bm25_w + vec_w == 1.0 regardless of query length."""
        for n in range(0, 12):
            query = " ".join(f"word{i}" for i in range(n))
            bm25_w, vec_w = hybrid_rrf_weights_for_query(query)
            assert bm25_w + vec_w == pytest.approx(1.0), f"Failed for n={n}: {query!r}"


# ---------------------------------------------------------------------------
# hybrid_rrf_weights_for_query — numeric / mixed alpha-numeric tokens
# ---------------------------------------------------------------------------


class TestHybridWeightsNumericTokens:
    """Branch: ≥2 tokens with both digit and alpha chars → bias +=1."""

    def test_two_mixed_alnum_tokens_boost_bm25(self) -> None:
        """'v1' and 'x3' are both mixed alpha-numeric → count ≥ 2 → bias +=1."""
        # 6 tokens (medium, bias +1) + numeric boost (+1) → total +2 → BM25 lean
        bm25_w, vec_w = hybrid_rrf_weights_for_query("bug fix v1 crash x3 report")
        assert bm25_w > vec_w

    def test_one_mixed_alnum_token_no_boost(self) -> None:
        """Only 'v2' is mixed → count=1 < 2 → no numeric boost.

        Short query (n=2) dominates → vector still wins.
        """
        bm25_w, vec_w = hybrid_rrf_weights_for_query("release v2")
        assert vec_w > bm25_w

    def test_digit_only_tokens_do_not_count(self) -> None:
        """Pure digit tokens ('123', '456') have no alpha → not counted as mixed."""
        # 5 tokens → medium (+1), '123'/'456' are digits-only (no alpha) → no boost
        bm25_w, vec_w = hybrid_rrf_weights_for_query("fix 123 crash 456 now")
        assert bm25_w > vec_w

    def test_alpha_only_tokens_do_not_count(self) -> None:
        """Pure alpha tokens have no digits → not counted as mixed."""
        # 5 tokens → medium (+1), 'abc'/'xyz' are alpha-only → no boost
        bm25_w, vec_w = hybrid_rrf_weights_for_query("store recall abc entries xyz")
        assert bm25_w > vec_w


# ---------------------------------------------------------------------------
# hybrid_rrf_weights_for_query — question prefixes and semantic phrases
# ---------------------------------------------------------------------------


class TestHybridWeightsQuestionAndSemanticBranches:
    """Branches for question-phrased prefixes and semantic proximity phrases."""

    def test_whats_prefix_question_bias(self) -> None:
        """'what's ' prefix → bias −=1 (question heuristic)."""
        # n=5 (medium +1) + "what's" (−1) → net 0 → balanced (vec_w >= bm25_w)
        bm25_w, vec_w = hybrid_rrf_weights_for_query("what's the memory store timeout")
        assert vec_w >= bm25_w
        assert bm25_w + vec_w == pytest.approx(1.0)

    def test_hows_prefix_question_bias(self) -> None:
        """'how's ' prefix → bias −=1 (question heuristic)."""
        bm25_w, vec_w = hybrid_rrf_weights_for_query("how's the consolidation working now")
        assert vec_w >= bm25_w
        assert bm25_w + vec_w == pytest.approx(1.0)

    def test_similar_to_phrase_biases_vector(self) -> None:
        """'similar to' substring → bias −=1 (semantic proximity)."""
        # n=4 (vague −1) + "similar to" (−1) → net −2 → strong vector lean
        bm25_w, vec_w = hybrid_rrf_weights_for_query("find similar to concept")
        assert vec_w > bm25_w

    def test_related_to_phrase_biases_vector(self) -> None:
        """'related to' substring → bias −=1."""
        # n=4 (vague −1) + "related to" (−1) → net −2 → vector lean
        bm25_w, vec_w = hybrid_rrf_weights_for_query("memories related to migration")
        assert vec_w > bm25_w

    def test_all_question_prefixes_favor_vector(self) -> None:
        """Each question-style prefix individually biases toward vector search.

        Uses 3-token queries so the vague-length bias (−1) and the question-prefix
        bias (−1) both apply, giving a clear net −2 → vector lean rather than a
        coin-flip when the medium-length bias cancels the question bias.
        """
        prefixes = [
            "how do I",  # vague(−1) + how (−1) = −2
            "why is this",  # vague(−1) + why (−1) = −2
            "when should we",  # vague(−1) + when(−1) = −2
            "where is config",  # vague(−1) + where(−1) = −2
            "who owns this",  # vague(−1) + who (−1) = −2
            "which backend is",  # vague(−1) + which(−1) = −2
            "explain the logic",  # vague(−1) + explain(−1) = −2
            "describe the arch",  # vague(−1) + describe(−1) = −2
            "tell me about",  # vague(−1) + tell me(−1) = −2
            "find anything now",  # vague(−1) + "find anything "(−1) = −2
        ]
        for q in prefixes:
            bm25_w, vec_w = hybrid_rrf_weights_for_query(q)
            assert vec_w > bm25_w, f"Expected vector lean for {q!r}, got bm25={bm25_w:.4f}"

    def test_weights_always_within_alpha_bounds(self) -> None:
        """BM25 weight is always clamped to [_ALPHA_LO, _ALPHA_HI]."""
        extreme_queries = [
            "",
            "a",
            "what is this",
            "CamelCase::method->field; v1 x2 file.py extra long words query token",
            "what's how's similar to related to anything about store recall",
        ]
        for q in extreme_queries:
            bm25_w, vec_w = hybrid_rrf_weights_for_query(q)
            assert _ALPHA_LO <= bm25_w <= _ALPHA_HI, (
                f"bm25_w={bm25_w:.4f} out of [{_ALPHA_LO}, {_ALPHA_HI}] for {q!r}"
            )
            assert bm25_w + vec_w == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# reciprocal_rank_fusion_weighted — zero-weight edge cases
# ---------------------------------------------------------------------------


class TestRRFWeightedZeroWeights:
    """Items in a zero-weight list still appear in results but contribute 0 score."""

    def test_zero_bm25_weight_only_vector_contributes(self) -> None:
        """bm25_weight=0 → BM25-only docs score 0; vector-only docs score 1/61."""
        result = reciprocal_rank_fusion_weighted(
            ["bm25only", "shared"],
            ["veconly", "shared"],
            bm25_weight=0.0,
            vector_weight=1.0,
            k=60,
        )
        s = _scores(result)
        # bm25only appears only in BM25 list with weight 0 → score 0
        assert s.get("bm25only", 0.0) == pytest.approx(0.0)
        # veconly appears only in vector list with weight 1 → score 1/61
        assert s["veconly"] == pytest.approx(1 / 61)

    def test_zero_vector_weight_only_bm25_contributes(self) -> None:
        """vector_weight=0 → vector-only docs score 0; BM25-only doc scores 1/61."""
        result = reciprocal_rank_fusion_weighted(
            ["bm25only"],
            ["veconly"],
            bm25_weight=1.0,
            vector_weight=0.0,
            k=60,
        )
        s = _scores(result)
        assert s["bm25only"] == pytest.approx(1 / 61)
        assert s.get("veconly", 0.0) == pytest.approx(0.0)
