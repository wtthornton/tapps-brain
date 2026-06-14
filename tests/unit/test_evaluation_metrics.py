"""Additional evaluation metric tests (companion to test_evaluation.py)."""

from __future__ import annotations

from tapps_brain.evaluation import ideal_dcg_at_k, ndcg_at_k


def test_ndcg_is_one_for_perfect_ranking() -> None:
    qrels = {"d1": 2, "d2": 1}
    ranked = ["d1", "d2"]
    assert ndcg_at_k(ranked, qrels, 2) == 1.0
    assert ideal_dcg_at_k(qrels, 2) > 0
