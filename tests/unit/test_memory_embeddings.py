"""Unit tests for memory embeddings (Epic 65.7)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tapps_brain.embeddings import (
    NoopProvider,
    get_embedding_provider,
)
from tapps_brain.models import MemoryEntry, MemoryTier
from tapps_brain.store import MemoryStore


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
