"""Unit tests for optional reranker (Epic 65.9)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tapps_brain.reranker import (
    RERANKER_TOP_CANDIDATES,
    NoopReranker,
    get_reranker,
)

# ---------------------------------------------------------------------------
# NoopReranker
# ---------------------------------------------------------------------------


class TestNoopReranker:
    def test_empty_candidates(self) -> None:
        reranker = NoopReranker()
        result = reranker.rerank("query", [], top_k=5)
        assert result == []

    def test_passthrough_order(self) -> None:
        reranker = NoopReranker()
        candidates = [
            ("key-a", "value a"),
            ("key-b", "value b"),
            ("key-c", "value c"),
        ]
        result = reranker.rerank("query", candidates, top_k=3)
        assert [r[0] for r in result] == ["key-a", "key-b", "key-c"]
        assert all(isinstance(r[1], float) for r in result)

    def test_top_k_limits_results(self) -> None:
        reranker = NoopReranker()
        candidates = [
            ("key-1", "v1"),
            ("key-2", "v2"),
            ("key-3", "v3"),
            ("key-4", "v4"),
        ]
        result = reranker.rerank("query", candidates, top_k=2)
        assert len(result) == 2
        assert [r[0] for r in result] == ["key-1", "key-2"]

    def test_scores_decrease_with_position(self) -> None:
        reranker = NoopReranker()
        candidates = [("k1", "v1"), ("k2", "v2"), ("k3", "v3")]
        result = reranker.rerank("query", candidates, top_k=3)
        scores = [r[1] for r in result]
        assert scores[0] > scores[1] > scores[2]


# ---------------------------------------------------------------------------
# get_reranker
# ---------------------------------------------------------------------------


class TestGetReranker:
    def test_disabled_returns_noop(self) -> None:
        r = get_reranker(enabled=False, provider="cohere", api_key="x")
        assert isinstance(r, NoopReranker)

    def test_provider_noop_returns_noop(self) -> None:
        r = get_reranker(enabled=True, provider="noop", top_k=10)
        assert isinstance(r, NoopReranker)

    def test_provider_cohere_no_key_returns_noop(self) -> None:
        r = get_reranker(enabled=True, provider="cohere", api_key=None)
        assert isinstance(r, NoopReranker)

    def test_provider_cohere_unavailable_returns_noop(self) -> None:
        with patch(
            "tapps_brain.reranker._create_cohere_reranker",
            return_value=None,
        ):
            r = get_reranker(
                enabled=True,
                provider="cohere",
                api_key="sk-test",
            )
        assert isinstance(r, NoopReranker)

    def test_provider_cohere_with_key_and_package_returns_cohere(self) -> None:
        try:
            import cohere  # noqa: F401
        except ImportError:
            pytest.skip("cohere not installed")
        r = get_reranker(
            enabled=True,
            provider="cohere",
            api_key="sk-test",
        )
        from tapps_brain.reranker import CohereReranker

        assert isinstance(r, CohereReranker)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_reranker_top_candidates_constant() -> None:
    assert RERANKER_TOP_CANDIDATES == 20
