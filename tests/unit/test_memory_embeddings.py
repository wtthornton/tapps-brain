"""Unit tests for memory embeddings (Epic 65.7)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from tapps_brain.embeddings import (
    EmbeddingProvider,
    NoopProvider,
    SentenceTransformerProvider,
    dequantize_embedding_int8,
    embedding_cosine_similarity,
    get_embedding_provider,
    quantize_embedding_int8,
    renormalize_embedding_l2,
)
from tapps_brain.models import MemoryEntry
from tapps_brain.store import MemoryStore

if TYPE_CHECKING:
    from pathlib import Path


class TestNoopProvider:
    """Tests for NoopProvider."""

    def test_embed_returns_zeros(self) -> None:
        provider = NoopProvider(dimension=4)
        result = provider.embed("hello world")
        assert result == [0.0, 0.0, 0.0, 0.0]

    def test_embed_batch_returns_zeros(self) -> None:
        provider = NoopProvider(dimension=384)
        texts = ["a", "b", "c"]
        results = provider.embed_batch(texts)
        assert len(results) == 3
        for r in results:
            assert r == [0.0] * 384

    def test_embed_batch_empty_returns_empty(self) -> None:
        provider = NoopProvider(dimension=4)
        assert provider.embed_batch([]) == []

    def test_dimension_property(self) -> None:
        provider = NoopProvider(dimension=128)
        assert provider.dimension == 128

    def test_model_id_optional(self) -> None:
        assert NoopProvider(dimension=4).model_id is None
        assert NoopProvider(dimension=4, model_id="m").model_id == "m"


class TestQuantizeEmbeddingInt8:
    """STORY-042.2 offline spike: int8 symmetric quantization helpers."""

    def test_empty_roundtrip(self) -> None:
        assert quantize_embedding_int8([]) == b""
        assert dequantize_embedding_int8(b"") == []

    def test_roundtrip_exact_powers(self) -> None:
        v = [1.0, -1.0, 0.0]
        blob = quantize_embedding_int8(v)
        assert len(blob) == 3
        back = dequantize_embedding_int8(blob, renormalize=False)
        assert back == pytest.approx([1.0, -1.0, 0.0])

    def test_clamp_out_of_range(self) -> None:
        blob = quantize_embedding_int8([2.0, -2.0])
        back = dequantize_embedding_int8(blob, renormalize=False)
        assert back == pytest.approx([1.0, -1.0])

    def test_cosine_self_high_after_renormalize(self) -> None:
        """Random unit vectors: cosine(u, rq(u)) should stay very high after int8 + L2 fixup."""
        import random

        rng = random.Random(42)
        dim = 384
        raw = [rng.gauss(0, 1) for _ in range(dim)]
        u = renormalize_embedding_l2(raw)
        blob = quantize_embedding_int8(u)
        rq = dequantize_embedding_int8(blob, renormalize=True)
        sim = embedding_cosine_similarity(u, rq)
        assert sim >= 0.998

    def test_pairwise_similarity_drift_bounded(self) -> None:
        """Cosine between two fixed unit vectors vs their quantized forms (deterministic)."""
        import random

        rng = random.Random(7)
        dim = 128

        def unit() -> list[float]:
            v = [rng.gauss(0, 1) for _ in range(dim)]
            return renormalize_embedding_l2(v)

        a, b = unit(), unit()
        c0 = embedding_cosine_similarity(a, b)
        aq = dequantize_embedding_int8(quantize_embedding_int8(a), renormalize=True)
        bq = dequantize_embedding_int8(quantize_embedding_int8(b), renormalize=True)
        c1 = embedding_cosine_similarity(aq, bq)
        assert abs(c0 - c1) < 0.02


class TestGetEmbeddingProvider:
    """Tests for get_embedding_provider factory."""

    def test_returns_none_when_disabled(self) -> None:
        result = get_embedding_provider(
            semantic_search_enabled=False,
            provider="sentence_transformers",
        )
        assert result is None

    def test_returns_none_when_unknown_provider(self) -> None:
        result = get_embedding_provider(
            semantic_search_enabled=True,
            provider="unknown",
        )
        assert result is None

    def test_protocol_compliance(self) -> None:
        """NoopProvider satisfies EmbeddingProvider protocol."""
        provider = NoopProvider(dimension=4)
        assert hasattr(provider, "dimension")
        assert hasattr(provider, "embed")
        assert hasattr(provider, "embed_batch")
        assert provider.embed("x") == [0.0, 0.0, 0.0, 0.0]


class TestStoreWithEmbeddingProvider:
    """Integration: MemoryStore with embedding provider stores embeddings."""

    def test_save_with_provider_stores_embedding(self, tmp_path: Path) -> None:
        provider = NoopProvider(dimension=384)
        store = MemoryStore(tmp_path, embedding_provider=provider)
        result = store.save("embed-key", "Some value", tier="pattern")
        assert isinstance(result, MemoryEntry)
        assert result.embedding is not None
        assert len(result.embedding) == 384
        assert result.embedding == [0.0] * 384
        loaded = store.get("embed-key")
        assert loaded is not None
        assert loaded.embedding is not None
        assert loaded.embedding == [0.0] * 384
        assert loaded.embedding_model_id is None
        store.close()

    def test_save_with_model_id_persisted(self, tmp_path: Path) -> None:
        provider = NoopProvider(dimension=384, model_id="noop-test-model")
        store = MemoryStore(tmp_path, embedding_provider=provider)
        result = store.save("mid-key", "text", tier="pattern")
        assert isinstance(result, MemoryEntry)
        assert result.embedding_model_id == "noop-test-model"
        loaded = store.get("mid-key")
        assert loaded is not None
        assert loaded.embedding_model_id == "noop-test-model"
        store.close()

    def test_save_without_provider_no_embedding(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        result = store.save("no-embed-key", "Value")
        assert isinstance(result, MemoryEntry)
        assert result.embedding is None
        loaded = store.get("no-embed-key")
        assert loaded is not None
        assert loaded.embedding is None
        store.close()


# ---------------------------------------------------------------------------
# SentenceTransformerProvider (mocked — no real model download)
# ---------------------------------------------------------------------------


class TestSentenceTransformerProvider:
    """Tests for SentenceTransformerProvider using mocked sentence_transformers."""

    def test_raises_when_st_unavailable(self) -> None:
        """SentenceTransformerProvider raises ImportError when ST is None."""
        with (
            patch("tapps_brain.embeddings.SentenceTransformer", None),
            pytest.raises(ImportError, match="sentence-transformers is required"),
        ):
            SentenceTransformerProvider()

    def test_init_stores_dimension(self) -> None:
        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = 384
        with patch("tapps_brain.embeddings.SentenceTransformer", return_value=mock_model):
            provider = SentenceTransformerProvider(model_name="test-model")
        assert provider.dimension == 384
        assert provider.model_id == "test-model"

    def test_init_falls_back_to_384_when_dimension_none(self) -> None:
        """When get_sentence_embedding_dimension() returns None, fall back to 384."""
        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = None
        with patch("tapps_brain.embeddings.SentenceTransformer", return_value=mock_model):
            provider = SentenceTransformerProvider(model_name="test-model")
        assert provider.dimension == 384

    def test_embed_returns_float_list(self) -> None:
        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = 4
        # Simulate ndarray-like: encode returns object with __iter__
        mock_vec = MagicMock()
        mock_vec.__iter__ = MagicMock(return_value=iter([0.1, 0.2, 0.3, 0.4]))
        mock_model.encode.return_value = mock_vec
        with patch("tapps_brain.embeddings.SentenceTransformer", return_value=mock_model):
            provider = SentenceTransformerProvider(model_name="test-model")
        result = provider.embed("hello")
        mock_model.encode.assert_called_once_with("hello", normalize_embeddings=True)
        assert len(result) == 4
        assert all(isinstance(x, float) for x in result)
        assert result == pytest.approx([0.1, 0.2, 0.3, 0.4])

    def test_embed_batch_returns_list_of_lists(self) -> None:
        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = 3
        # Simulate ndarray rows with .tolist()
        mock_row_a = MagicMock()
        mock_row_a.tolist.return_value = [0.1, 0.2, 0.3]
        mock_row_b = MagicMock()
        mock_row_b.tolist.return_value = [0.4, 0.5, 0.6]
        mock_model.encode.return_value = [mock_row_a, mock_row_b]
        with patch("tapps_brain.embeddings.SentenceTransformer", return_value=mock_model):
            provider = SentenceTransformerProvider(model_name="test-model")
        result = provider.embed_batch(["a", "b"])
        mock_model.encode.assert_called_once_with(
            ["a", "b"], normalize_embeddings=True, show_progress_bar=False
        )
        assert len(result) == 2
        assert result[0] == pytest.approx([0.1, 0.2, 0.3])
        assert result[1] == pytest.approx([0.4, 0.5, 0.6])

    def test_embed_batch_empty_returns_empty(self) -> None:
        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = 3
        with patch("tapps_brain.embeddings.SentenceTransformer", return_value=mock_model):
            provider = SentenceTransformerProvider(model_name="test-model")
        result = provider.embed_batch([])
        assert result == []

    def test_protocol_compliance(self) -> None:
        """SentenceTransformerProvider satisfies EmbeddingProvider protocol."""
        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = 4
        with patch("tapps_brain.embeddings.SentenceTransformer", return_value=mock_model):
            provider = SentenceTransformerProvider(model_name="test-model")
        assert isinstance(provider, EmbeddingProvider)


# ---------------------------------------------------------------------------
# get_embedding_provider — additional factory paths
# ---------------------------------------------------------------------------


class TestGetEmbeddingProviderFactoryPaths:
    """Cover remaining branches in get_embedding_provider."""

    def test_st_init_os_error_returns_none(self) -> None:
        """When SentenceTransformerProvider raises OSError, returns None."""
        with patch(
            "tapps_brain.embeddings.SentenceTransformerProvider",
            side_effect=OSError("disk error"),
        ):
            result = get_embedding_provider(
                semantic_search_enabled=True,
                provider="sentence_transformers",
            )
        assert result is None

    def test_st_init_runtime_error_returns_none(self) -> None:
        """When SentenceTransformerProvider raises RuntimeError, returns None."""
        with patch(
            "tapps_brain.embeddings.SentenceTransformerProvider",
            side_effect=RuntimeError("cuda fail"),
        ):
            result = get_embedding_provider(
                semantic_search_enabled=True,
                provider="sentence_transformers",
            )
        assert result is None

    def test_st_init_value_error_returns_none(self) -> None:
        """When SentenceTransformerProvider raises ValueError, returns None."""
        with patch(
            "tapps_brain.embeddings.SentenceTransformerProvider",
            side_effect=ValueError("bad model"),
        ):
            result = get_embedding_provider(
                semantic_search_enabled=True,
                provider="sentence_transformers",
            )
        assert result is None

    def test_st_success_returns_provider(self) -> None:
        """When ST is available and init succeeds, returns provider."""
        mock_provider = MagicMock()
        with patch(
            "tapps_brain.embeddings.SentenceTransformerProvider",
            return_value=mock_provider,
        ):
            result = get_embedding_provider(
                semantic_search_enabled=True,
                provider="sentence_transformers",
                model="custom-model",
            )
        assert result is mock_provider


# ---------------------------------------------------------------------------
# SentenceTransformerProvider with real library (skipped if not installed)
# ---------------------------------------------------------------------------


class TestSentenceTransformerProviderReal:
    """Tests using real sentence-transformers (skipped if not installed)."""

    @pytest.fixture(autouse=True)
    def _skip_if_no_st(self) -> None:
        pytest.importorskip("sentence_transformers")

    def test_embed_returns_correct_dimensions(self) -> None:
        provider = SentenceTransformerProvider()
        result = provider.embed("hello world")
        assert len(result) == provider.dimension
        assert all(isinstance(x, float) for x in result)

    def test_embed_batch_consistency(self) -> None:
        provider = SentenceTransformerProvider()
        single_a = provider.embed("test sentence A")
        single_b = provider.embed("test sentence B")
        batch = provider.embed_batch(["test sentence A", "test sentence B"])
        assert len(batch) == 2
        assert batch[0] == pytest.approx(single_a, abs=1e-5)
        assert batch[1] == pytest.approx(single_b, abs=1e-5)

    def test_embed_deterministic(self) -> None:
        provider = SentenceTransformerProvider()
        result1 = provider.embed("deterministic test")
        result2 = provider.embed("deterministic test")
        assert result1 == pytest.approx(result2, abs=1e-6)
