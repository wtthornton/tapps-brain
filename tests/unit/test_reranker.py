"""Unit tests for optional reranker (Epic 65.9)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tapps_brain.reranker import (
    RERANKER_TOP_CANDIDATES,
    FlashRankReranker,
    NoopReranker,
    Reranker,
    _noop_fallback,
    get_reranker,
    reranker_provider_label,
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

    def test_protocol_compliance(self) -> None:
        """NoopReranker satisfies Reranker protocol."""
        reranker = NoopReranker()
        assert isinstance(reranker, Reranker)


# ---------------------------------------------------------------------------
# FlashRankReranker (mocked — no real flashrank needed)
# ---------------------------------------------------------------------------


class TestFlashRankReranker:
    """Tests for FlashRankReranker with mocked flashrank library."""

    def test_empty_candidates_returns_empty(self) -> None:
        reranker = FlashRankReranker()
        result = reranker.rerank("query", [], top_k=5)
        assert result == []

    def test_flashrank_success(self) -> None:
        reranker = FlashRankReranker()
        candidates = [("a", "val a"), ("b", "val b"), ("c", "val c")]

        mock_results = [
            {"id": 2, "text": "val c", "score": 0.95},
            {"id": 0, "text": "val a", "score": 0.80},
        ]

        mock_ranker = MagicMock()
        mock_ranker.rerank.return_value = mock_results

        mock_flashrank = MagicMock()
        mock_flashrank.Ranker.return_value = mock_ranker

        with patch.dict("sys.modules", {"flashrank": mock_flashrank}):
            result = reranker.rerank("query", candidates, top_k=2)

        assert len(result) == 2
        assert result[0] == ("c", 0.95)
        assert result[1] == ("a", 0.80)

    def test_top_k_limits_results(self) -> None:
        reranker = FlashRankReranker()
        candidates = [("a", "va"), ("b", "vb"), ("c", "vc")]

        mock_results = [
            {"id": 2, "text": "vc", "score": 0.9},
            {"id": 1, "text": "vb", "score": 0.8},
            {"id": 0, "text": "va", "score": 0.7},
        ]

        mock_ranker = MagicMock()
        mock_ranker.rerank.return_value = mock_results

        mock_flashrank = MagicMock()
        mock_flashrank.Ranker.return_value = mock_ranker

        with patch.dict("sys.modules", {"flashrank": mock_flashrank}):
            result = reranker.rerank("query", candidates, top_k=1)

        assert len(result) == 1
        assert result[0] == ("c", 0.9)

    def test_scores_clamped_to_unit_interval(self) -> None:
        """Scores outside [0.0, 1.0] are clamped."""
        reranker = FlashRankReranker()
        candidates = [("a", "val a"), ("b", "val b")]

        mock_results = [
            {"id": 0, "text": "val a", "score": 1.5},
            {"id": 1, "text": "val b", "score": -0.3},
        ]

        mock_ranker = MagicMock()
        mock_ranker.rerank.return_value = mock_results

        mock_flashrank = MagicMock()
        mock_flashrank.Ranker.return_value = mock_ranker

        with patch.dict("sys.modules", {"flashrank": mock_flashrank}):
            result = reranker.rerank("query", candidates, top_k=2)

        assert result[0] == ("a", 1.0)
        assert result[1] == ("b", 0.0)

    def test_out_of_range_id_skipped(self) -> None:
        """If flashrank returns an id out of range, it is skipped."""
        reranker = FlashRankReranker()
        candidates = [("a", "va")]

        mock_results = [
            {"id": 99, "text": "va", "score": 0.5},
            {"id": 0, "text": "va", "score": 0.8},
        ]

        mock_ranker = MagicMock()
        mock_ranker.rerank.return_value = mock_results

        mock_flashrank = MagicMock()
        mock_flashrank.Ranker.return_value = mock_ranker

        with patch.dict("sys.modules", {"flashrank": mock_flashrank}):
            result = reranker.rerank("query", candidates, top_k=5)

        assert len(result) == 1
        assert result[0] == ("a", 0.8)

    def test_rerank_error_falls_back_to_noop(self) -> None:
        reranker = FlashRankReranker()
        candidates = [("a", "val a"), ("b", "val b")]

        mock_flashrank = MagicMock()
        mock_flashrank.RerankRequest.side_effect = Exception("model failed")

        with patch.dict("sys.modules", {"flashrank": mock_flashrank}):
            result = reranker.rerank("query", candidates, top_k=2)

        assert len(result) == 2
        assert [r[0] for r in result] == ["a", "b"]

    def test_lazy_ranker_init(self) -> None:
        """Ranker is not created until first rerank() call."""
        reranker = FlashRankReranker()
        assert reranker._ranker is None

    def test_protocol_compliance(self) -> None:
        """FlashRankReranker satisfies Reranker protocol."""
        reranker = FlashRankReranker()
        assert isinstance(reranker, Reranker)


# ---------------------------------------------------------------------------
# get_reranker
# ---------------------------------------------------------------------------


class TestGetReranker:
    def test_disabled_returns_noop(self) -> None:
        r = get_reranker(enabled=False)
        assert isinstance(r, NoopReranker)

    def test_enabled_flashrank_available(self) -> None:
        with patch("tapps_brain.reranker._flashrank_available", return_value=True):
            r = get_reranker(enabled=True)
        assert isinstance(r, FlashRankReranker)

    def test_enabled_flashrank_unavailable_returns_noop(self) -> None:
        with patch("tapps_brain.reranker._flashrank_available", return_value=False):
            r = get_reranker(enabled=True)
        assert isinstance(r, NoopReranker)

    def test_model_override(self) -> None:
        with patch("tapps_brain.reranker._flashrank_available", return_value=True):
            r = get_reranker(enabled=True, model="ms-marco-MiniLM-L-12-v2")
        assert isinstance(r, FlashRankReranker)
        assert r._model_name == "ms-marco-MiniLM-L-12-v2"


# ---------------------------------------------------------------------------
# reranker_provider_label
# ---------------------------------------------------------------------------


class TestRerankerProviderLabel:
    def test_noop(self) -> None:
        assert reranker_provider_label(NoopReranker()) == "noop"

    def test_flashrank(self) -> None:
        assert reranker_provider_label(FlashRankReranker()) == "flashrank"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_reranker_top_candidates_constant() -> None:
    assert RERANKER_TOP_CANDIDATES == 20


# ---------------------------------------------------------------------------
# _noop_fallback
# ---------------------------------------------------------------------------


class TestNoopFallback:
    def test_preserves_order_and_limits(self) -> None:
        candidates = [("a", "va"), ("b", "vb"), ("c", "vc")]
        result = _noop_fallback(candidates, top_k=2)
        assert len(result) == 2
        assert [r[0] for r in result] == ["a", "b"]

    def test_empty_candidates(self) -> None:
        assert _noop_fallback([], top_k=5) == []


# ---------------------------------------------------------------------------
# FlashRankReranker with real library (skipped if not installed)
# ---------------------------------------------------------------------------


class TestFlashRankRerankerReal:
    """Tests using real flashrank library (skipped if not installed)."""

    @pytest.fixture(autouse=True)
    def _skip_if_no_flashrank(self) -> None:
        pytest.importorskip("flashrank")

    def test_reranker_can_be_created(self) -> None:
        reranker = FlashRankReranker()
        assert isinstance(reranker, Reranker)
        assert reranker._ranker is None  # lazy — not loaded yet
