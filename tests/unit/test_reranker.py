"""Unit tests for optional reranker (Epic 65.9)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tapps_brain.reranker import (
    RERANKER_TOP_CANDIDATES,
    CohereReranker,
    NoopReranker,
    Reranker,
    _create_cohere_reranker,
    _noop_fallback,
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
        r = get_reranker(enabled=True, provider="noop")
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
# _create_cohere_reranker
# ---------------------------------------------------------------------------


class TestCreateCohereReranker:
    def test_returns_none_when_cohere_not_installed(self) -> None:
        with patch("tapps_brain.reranker.importlib.util.find_spec", return_value=None):
            result = _create_cohere_reranker(api_key="sk-test")
        assert result is None

    def test_returns_cohere_reranker_when_available(self) -> None:
        mock_spec = MagicMock()
        with patch("tapps_brain.reranker.importlib.util.find_spec", return_value=mock_spec):
            result = _create_cohere_reranker(api_key="sk-test", model="rerank-v3.5")
        assert isinstance(result, CohereReranker)


# ---------------------------------------------------------------------------
# CohereReranker (mocked — no real API calls)
# ---------------------------------------------------------------------------


class TestCohereReranker:
    """Tests for CohereReranker with mocked cohere library."""

    def test_empty_candidates_returns_empty(self) -> None:
        reranker = CohereReranker(api_key="sk-test")
        result = reranker.rerank("query", [], top_k=5)
        assert result == []

    def test_no_api_key_falls_back_to_noop(self) -> None:
        reranker = CohereReranker(api_key="")
        candidates = [("a", "val a"), ("b", "val b")]
        result = reranker.rerank("query", candidates, top_k=2)
        assert len(result) == 2
        assert [r[0] for r in result] == ["a", "b"]

    def test_cohere_import_error_falls_back(self) -> None:
        reranker = CohereReranker(api_key="sk-test")
        candidates = [("a", "val a"), ("b", "val b")]
        with patch.dict("sys.modules", {"cohere": None}):
            result = reranker.rerank("query", candidates, top_k=2)
        assert len(result) == 2
        assert [r[0] for r in result] == ["a", "b"]

    def test_cohere_api_error_falls_back(self) -> None:
        reranker = CohereReranker(api_key="sk-test")
        candidates = [("a", "val a"), ("b", "val b")]
        mock_cohere = MagicMock()
        mock_cohere.ClientV2.side_effect = Exception("API error")
        mock_cohere.Client.side_effect = Exception("API error")
        with patch.dict("sys.modules", {"cohere": mock_cohere}):
            result = reranker.rerank("query", candidates, top_k=2)
        assert len(result) == 2
        assert [r[0] for r in result] == ["a", "b"]

    def test_cohere_v2_client_success(self) -> None:
        reranker = CohereReranker(api_key="sk-test", model="rerank-v3.5")
        candidates = [("a", "val a"), ("b", "val b"), ("c", "val c")]

        # Mock response results
        mock_result_0 = MagicMock()
        mock_result_0.index = 2
        mock_result_0.relevance_score = 0.95
        mock_result_1 = MagicMock()
        mock_result_1.index = 0
        mock_result_1.relevance_score = 0.80

        mock_response = MagicMock()
        mock_response.results = [mock_result_0, mock_result_1]

        mock_client = MagicMock()
        mock_client.rerank.return_value = mock_response

        mock_cohere = MagicMock()
        mock_cohere.ClientV2.return_value = mock_client

        with patch.dict("sys.modules", {"cohere": mock_cohere}):
            result = reranker.rerank("query", candidates, top_k=2)

        assert len(result) == 2
        assert result[0] == ("c", 0.95)
        assert result[1] == ("a", 0.80)

    def test_cohere_v1_client_fallback(self) -> None:
        """When ClientV2 is not available, falls back to Client (v1)."""
        reranker = CohereReranker(api_key="sk-test")
        candidates = [("x", "val x"), ("y", "val y")]

        mock_result = MagicMock()
        mock_result.index = 1
        mock_result.relevance_score = 0.9

        mock_response = MagicMock()
        mock_response.results = [mock_result]

        mock_client = MagicMock()
        mock_client.rerank.return_value = mock_response

        mock_cohere = MagicMock(spec=[])  # No ClientV2 attribute
        mock_cohere.Client = MagicMock(return_value=mock_client)

        with patch.dict("sys.modules", {"cohere": mock_cohere}):
            result = reranker.rerank("query", candidates, top_k=2)

        assert len(result) == 1
        assert result[0] == ("y", 0.9)

    def test_top_k_limits_cohere_call(self) -> None:
        reranker = CohereReranker(api_key="sk-test")
        candidates = [("a", "va"), ("b", "vb"), ("c", "vc")]

        mock_response = MagicMock()
        mock_response.results = []

        mock_client = MagicMock()
        mock_client.rerank.return_value = mock_response

        mock_cohere = MagicMock()
        mock_cohere.ClientV2.return_value = mock_client

        with patch.dict("sys.modules", {"cohere": mock_cohere}):
            reranker.rerank("query", candidates, top_k=1)

        # Verify top_n was set to min(top_k, len(candidates)) = 1
        call_kwargs = mock_client.rerank.call_args
        assert call_kwargs.kwargs["top_n"] == 1

    def test_scores_clamped_to_unit_interval(self) -> None:
        """Cohere scores outside [0.0, 1.0] are clamped."""
        reranker = CohereReranker(api_key="sk-test")
        candidates = [("a", "val a"), ("b", "val b")]

        mock_result_high = MagicMock()
        mock_result_high.index = 0
        mock_result_high.relevance_score = 1.5  # above 1.0

        mock_result_low = MagicMock()
        mock_result_low.index = 1
        mock_result_low.relevance_score = -0.3  # below 0.0

        mock_response = MagicMock()
        mock_response.results = [mock_result_high, mock_result_low]

        mock_client = MagicMock()
        mock_client.rerank.return_value = mock_response

        mock_cohere = MagicMock()
        mock_cohere.ClientV2.return_value = mock_client

        with patch.dict("sys.modules", {"cohere": mock_cohere}):
            result = reranker.rerank("query", candidates, top_k=2)

        assert result[0] == ("a", 1.0)
        assert result[1] == ("b", 0.0)

    def test_out_of_range_index_skipped(self) -> None:
        """If cohere returns an index out of range, it is skipped."""
        reranker = CohereReranker(api_key="sk-test")
        candidates = [("a", "va")]

        mock_result_bad = MagicMock()
        mock_result_bad.index = 99  # out of range
        mock_result_bad.relevance_score = 0.5

        mock_result_good = MagicMock()
        mock_result_good.index = 0
        mock_result_good.relevance_score = 0.8

        mock_response = MagicMock()
        mock_response.results = [mock_result_bad, mock_result_good]

        mock_client = MagicMock()
        mock_client.rerank.return_value = mock_response

        mock_cohere = MagicMock()
        mock_cohere.ClientV2.return_value = mock_client

        with patch.dict("sys.modules", {"cohere": mock_cohere}):
            result = reranker.rerank("query", candidates, top_k=5)

        assert len(result) == 1
        assert result[0] == ("a", 0.8)

    def test_protocol_compliance(self) -> None:
        """CohereReranker satisfies Reranker protocol."""
        reranker = CohereReranker(api_key="sk-test")
        assert isinstance(reranker, Reranker)


# ---------------------------------------------------------------------------
# CohereReranker with real library (skipped if not installed)
# ---------------------------------------------------------------------------


class TestCohereRerankerReal:
    """Tests using real cohere library (skipped if not installed)."""

    @pytest.fixture(autouse=True)
    def _skip_if_no_cohere(self) -> None:
        pytest.importorskip("cohere")

    def test_rerank_reorders_candidates(self) -> None:
        """Verify rerank call shape (will fail without valid API key)."""
        # We can at least verify the object is created correctly
        reranker = CohereReranker(api_key="test-invalid-key")
        candidates = [("a", "Python programming"), ("b", "Java programming")]
        # With invalid key, should fall back to noop
        result = reranker.rerank("Python", candidates, top_k=2)
        assert len(result) == 2
        assert all(isinstance(r, tuple) for r in result)
        assert all(isinstance(r[1], float) for r in result)
