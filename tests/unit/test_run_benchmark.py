"""Unit tests for benchmark CLI helpers."""

from __future__ import annotations

from scripts.run_benchmark import _overlap_score, _question_token_set


def test_question_token_set_filters_short_tokens() -> None:
    tokens = _question_token_set("a an the python data")
    assert tokens == {"the", "python", "data"}


def test_overlap_score_counts_shared_tokens() -> None:
    q = _question_token_set("python memory store")
    assert _overlap_score(q, "python memory patterns") == 2
