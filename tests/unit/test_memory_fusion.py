"""Tests for Reciprocal Rank Fusion (Epic 65.8)."""

from __future__ import annotations

import pytest

from tapps_brain.fusion import (
    hybrid_rrf_weights_for_query,
    reciprocal_rank_fusion,
    reciprocal_rank_fusion_weighted,
)


class TestReciprocalRankFusion:
    def test_empty_both_returns_empty(self) -> None:
        assert reciprocal_rank_fusion([], []) == []

    def test_empty_bm25_returns_vector_order(self) -> None:
        result = reciprocal_rank_fusion([], ["a", "b", "c"], k=60)
        assert result == [
            ("a", 1 / 61),
            ("b", 1 / 62),
            ("c", 1 / 63),
        ]

    def test_empty_vector_returns_bm25_order(self) -> None:
        result = reciprocal_rank_fusion(["x", "y", "z"], [], k=60)
        assert result == [
            ("x", 1 / 61),
            ("y", 1 / 62),
            ("z", 1 / 63),
        ]

    def test_overlap_aggregates_scores(self) -> None:
        bm25 = ["a", "b", "c"]
        vector = ["b", "a", "d"]
        result = reciprocal_rank_fusion(bm25, vector, k=60)
        # a: 1/61 + 1/62, b: 1/62 + 1/61, c: 1/63, d: 1/63
        assert len(result) == 4
        scores = dict(result)
        assert scores["a"] == pytest.approx(1 / 61 + 1 / 62)
        assert scores["b"] == pytest.approx(1 / 61 + 1 / 62)
        assert scores["c"] == pytest.approx(1 / 63)
        assert scores["d"] == pytest.approx(1 / 63)
        # a and b tied, b may come first (1/61 from vector)
        assert result[0][0] in ("a", "b")
        assert result[1][0] in ("a", "b")
        assert {r[0] for r in result[:2]} == {"a", "b"}

    def test_configurable_k(self) -> None:
        result = reciprocal_rank_fusion(["a"], ["a"], k=10)
        assert result == [("a", 1 / 11 + 1 / 11)]
        assert result[0][1] == pytest.approx(2 / 11)

    def test_default_k_60(self) -> None:
        result = reciprocal_rank_fusion(["a"], [])
        assert result[0][1] == pytest.approx(1 / 61)

    def test_deduplication(self) -> None:
        bm25 = ["a", "b", "a"]  # duplicate in same list
        vector = ["b", "a"]
        result = reciprocal_rank_fusion(bm25, vector, k=60)
        keys = [r[0] for r in result]
        assert keys.count("a") == 1
        assert keys.count("b") == 1
        assert len(result) == 2

    def test_sort_descending_by_score(self) -> None:
        bm25 = ["x", "y", "z"]
        vector = ["z", "y", "x"]  # reverse order
        result = reciprocal_rank_fusion(bm25, vector, k=60)
        # x and z tie: 1/61 + 1/63 (tie-break by key)
        # y: 1/62 + 1/62
        scores = dict(result)
        assert scores["x"] == scores["z"]
        assert scores["y"] == pytest.approx(2 / 62)
        assert len(result) == 3
        assert result[0][1] >= result[1][1] >= result[2][1]

    def test_weighted_matches_unweighted_when_weights_are_one(self) -> None:
        bm25 = ["a", "b"]
        vector = ["b", "c"]
        u = reciprocal_rank_fusion(bm25, vector, k=60)
        w = reciprocal_rank_fusion_weighted(bm25, vector, bm25_weight=1.0, vector_weight=1.0, k=60)
        assert u == w

    def test_weighted_skews_toward_bm25(self) -> None:
        # Only BM25 lists "a"; only vector lists "b". Equal weights tie on key order.
        u = reciprocal_rank_fusion_weighted(["a"], ["b"], bm25_weight=2.0, vector_weight=1.0, k=60)
        assert u[0][0] == "a"

    def test_weighted_skews_toward_vector(self) -> None:
        u = reciprocal_rank_fusion_weighted(["a"], ["b"], bm25_weight=1.0, vector_weight=2.0, k=60)
        assert u[0][0] == "b"

    def test_weighted_rejects_negative_weights(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            reciprocal_rank_fusion_weighted(["a"], ["b"], bm25_weight=-1.0, vector_weight=1.0)

    def test_hybrid_weights_sum_to_one(self) -> None:
        a, b = hybrid_rrf_weights_for_query("any query here")
        assert a == pytest.approx(1.0 - b)

    def test_hybrid_weights_question_query_favors_vector(self) -> None:
        q_alpha, _ = hybrid_rrf_weights_for_query("what is the deployment process")
        k_alpha, _ = hybrid_rrf_weights_for_query(
            "FooBarClass deploy k8s manifest.yaml error line 42 stack trace dump"
        )
        assert q_alpha < k_alpha

    def test_hybrid_weights_empty_query_is_balanced(self) -> None:
        a, b = hybrid_rrf_weights_for_query("   ")
        assert a == pytest.approx(0.5)
        assert b == pytest.approx(0.5)

    def test_hybrid_weights_phrase_and_code_signals(self) -> None:
        """Exercise vague-phrase and keyword/code heuristics (coverage)."""
        vague, _ = hybrid_rrf_weights_for_query("anything about widgets")
        assert vague < 0.5

        codeish, _ = hybrid_rrf_weights_for_query(
            "FooBar::deploy->prod (fix) file.txt v1 x2 extra words here"
        )
        assert codeish > 0.5
