"""Additional evaluation metric tests (companion to test_evaluation.py)."""

from __future__ import annotations

from tapps_brain.evaluation import dcg_at_k, ideal_dcg_at_k, ndcg_at_k, recall_at_k


def test_ndcg_is_one_for_perfect_ranking() -> None:
    qrels = {"d1": 2, "d2": 1}
    ranked = ["d1", "d2"]
    assert ndcg_at_k(ranked, qrels, 2) == 1.0
    assert ideal_dcg_at_k(qrels, 2) > 0


def test_negative_k_does_not_use_python_negative_slice() -> None:
    """``ranked[:k]`` / ``gains[:k]`` with k<0 must not mean 'all but last'."""
    qrels = {"a": 2, "b": 1}
    ranked = ["a", "b", "c"]
    assert dcg_at_k(ranked, qrels, -1) == 0.0
    assert ideal_dcg_at_k(qrels, -1) == 0.0
    assert recall_at_k(ranked, qrels, -1) == 0.0
    assert ndcg_at_k(ranked, qrels, -1) == 0.0
    assert dcg_at_k(ranked, qrels, 0) == 0.0
