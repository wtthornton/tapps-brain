"""Coverage for recall empty-reason constants (agent contract)."""

from __future__ import annotations

from tapps_brain.recall_diagnostics import (
    RECALL_EMPTY_BELOW_SCORE_THRESHOLD,
    RECALL_EMPTY_ENGAGEMENT_LOW,
    RECALL_EMPTY_GROUP_EMPTY,
    RECALL_EMPTY_NO_RANKED_MATCHES,
    RECALL_EMPTY_POST_FILTER,
    RECALL_EMPTY_RAG_BLOCKED,
    RECALL_EMPTY_SEARCH_FAILED,
    RECALL_EMPTY_STORE_EMPTY,
    RECALL_EMPTY_TOKEN_BUDGET,
)


def test_recall_empty_codes_are_unique_and_non_empty() -> None:
    codes = {
        RECALL_EMPTY_ENGAGEMENT_LOW,
        RECALL_EMPTY_SEARCH_FAILED,
        RECALL_EMPTY_STORE_EMPTY,
        RECALL_EMPTY_GROUP_EMPTY,
        RECALL_EMPTY_NO_RANKED_MATCHES,
        RECALL_EMPTY_BELOW_SCORE_THRESHOLD,
        RECALL_EMPTY_RAG_BLOCKED,
        RECALL_EMPTY_POST_FILTER,
        RECALL_EMPTY_TOKEN_BUDGET,
    }
    assert len(codes) == 9
    for c in codes:
        assert isinstance(c, str)
        assert c
