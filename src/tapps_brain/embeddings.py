"""Pluggable embedding provider for optional semantic search (Epic 65.7).

Defines EmbeddingProvider protocol and implementations:
- NoopProvider: returns zeros when semantic search is disabled (testing/placeholder).
- SentenceTransformerProvider: optional sentence-transformers backend (behind feature_flags).

Used by Epic 65.8 hybrid search (BM25 + vector).
"""

from __future__ import annotations

from typing import Protocol, cast, runtime_checkable

import structlog

from tapps_brain._feature_flags import feature_flags

logger = structlog.get_logger(__name__)

# Optional dependency — sentence-transformers
if feature_flags.sentence_transformers:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        SentenceTransformer = None
else:
    SentenceTransformer = None

_DEFAULT_MODEL = "all-MiniLM-L6-v2"


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Protocol for pluggable embedding providers.

    Used for optional semantic search. Implementations must provide
    single-text and batch embedding, plus dimension.
    """

    @property
    def dimension(self) -> int:
        """Embedding vector dimension."""
        ...

    def embed(self, text: str) -> list[float]:
        """Embed a single text into a vector."""
        ...

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts into vectors."""
        ...


class NoopProvider:
    """Placeholder provider that returns zero vectors.

    Used when semantic search is disabled or for testing. Dimension
    matches all-MiniLM-L6-v2 (384) for schema consistency.
    """

    def __init__(self, dimension: int = 384) -> None:
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, text: str) -> list[float]:
        """Return a zero vector of dimension length."""
        return [0.0] * self._dimension

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Return zero vectors for each text."""
        return [[0.0] * self._dimension for _ in texts]


class SentenceTransformerProvider:
    """Embedding provider backed by sentence-transformers.

    Requires sentence-transformers package. Use feature_flags.sentence_transformers
    to check availability before instantiation.
    """

    def __init__(self, model_name: str = _DEFAULT_MODEL) -> None:
        if SentenceTransformer is None:
            msg = (
                "sentence-transformers is required for vector semantic search. "
                "Install with: pip install tapps-brain[vector]"
            )
            raise ImportError(msg)

        self._model_name = model_name
        self._model = SentenceTransformer(model_name)
        raw_dim = self._model.get_sentence_embedding_dimension()
        self._dim: int = int(raw_dim) if raw_dim is not None else 384

    @property
    def dimension(self) -> int:
        return self._dim

    def embed(self, text: str) -> list[float]:
        """Embed a single text using the sentence-transformers model."""
        vec = self._model.encode(text, normalize_embeddings=True)
        return [float(x) for x in vec]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts in one batch."""
        if not texts:
            return []
        embeddings = self._model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return [cast("list[float]", e.tolist()) for e in embeddings]


def get_embedding_provider(
    semantic_search_enabled: bool,
    provider: str = "sentence_transformers",
    model: str = _DEFAULT_MODEL,
) -> EmbeddingProvider | None:
    """Return an EmbeddingProvider when semantic search is enabled, else None.

    Args:
        semantic_search_enabled: Whether semantic search is turned on (config).
        provider: Provider name; only "sentence_transformers" supported.
        model: Model name for sentence-transformers (e.g. all-MiniLM-L6-v2).

    Returns:
        EmbeddingProvider when enabled and provider available, else None.
    """
    if not semantic_search_enabled:
        return None

    if provider == "sentence_transformers":
        if not feature_flags.sentence_transformers:
            logger.debug(
                "embedding_provider_unavailable",
                reason="sentence_transformers not installed",
            )
            return None
        try:
            return SentenceTransformerProvider(model_name=model)
        except (ImportError, OSError, RuntimeError, ValueError) as e:
            logger.warning("embedding_provider_init_failed", error=str(e))
            return None

    # Unknown provider or future "openai" etc — return None
    logger.debug("embedding_provider_unknown", provider=provider)
    return None
