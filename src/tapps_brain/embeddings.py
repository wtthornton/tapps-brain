"""Pluggable embedding provider for optional semantic search (Epic 65.7).

Defines EmbeddingProvider protocol and implementations:
- NoopProvider: returns zeros when semantic search is disabled (testing/placeholder).
- SentenceTransformerProvider: optional sentence-transformers backend (behind feature_flags).

Used by Epic 65.8 hybrid search (BM25 + vector).

Operator-facing defaults (model id, dimension, license, upgrade notes):
``docs/guides/embedding-model-card.md`` (STORY-042.2).
"""

from __future__ import annotations

import math
import struct
from typing import Any, Protocol, cast, runtime_checkable

import structlog

logger = structlog.get_logger(__name__)

# sentence-transformers is a core dependency.
try:
    from sentence_transformers import SentenceTransformer
except ImportError:  # pragma: no cover — should not happen with correct install
    SentenceTransformer = None  # type: ignore[assignment, misc]

_DEFAULT_MODEL = "all-MiniLM-L6-v2"

# Symmetric int8 scale for components in [-1, 1] (L2-normalized sentence embeddings).
_INT8_QUANT_SCALE = 127.0


def quantize_embedding_int8(embedding: list[float]) -> bytes:
    """Lossy symmetric int8 quantization for spike / offline experiments (STORY-042.2).

    Each float is clamped to ``[-1, 1]``, scaled by 127, rounded, then clamped to
    ``[-127, 127]`` and packed as signed bytes. **Not** used for sqlite-vec or on-disk
    JSON floats today — product storage remains float32 JSON arrays.

    Args:
        embedding: Dense vector (typically L2-normalized).

    Returns:
        Packed signed bytes, length ``len(embedding)``.
    """
    if not embedding:
        return b""
    packed: list[int] = []
    for x in embedding:
        xf = float(x)
        if xf > 1.0:
            xf = 1.0
        elif xf < -1.0:
            xf = -1.0
        q = round(xf * _INT8_QUANT_SCALE)
        q = max(-127, min(127, q))
        packed.append(q)
    return struct.pack(f"{len(packed)}b", *packed)


def dequantize_embedding_int8(blob: bytes, *, renormalize: bool = False) -> list[float]:
    """Decode :func:`quantize_embedding_int8` output to float32 components in ``[-1, 1]``.

    Args:
        blob: Packed int8 bytes from :func:`quantize_embedding_int8`.
        renormalize: When True, L2-normalize after dequantization (recommended before
            cosine similarity vs freshly normalized query vectors).
    """
    if not blob:
        return []
    vals = struct.unpack(f"{len(blob)}b", blob)
    out = [v / _INT8_QUANT_SCALE for v in vals]
    if renormalize:
        return renormalize_embedding_l2(out)
    return out


def renormalize_embedding_l2(embedding: list[float]) -> list[float]:
    """Return a unit L2-norm copy of *embedding* (or zeros if norm is zero)."""
    if not embedding:
        return []
    s = math.sqrt(sum(x * x for x in embedding))
    if s <= 0.0:
        return [0.0] * len(embedding)
    inv = 1.0 / s
    return [x * inv for x in embedding]


def embedding_cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length dense vectors."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (na * nb)


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

    def __init__(self, dimension: int = 384, *, model_id: str | None = None) -> None:
        self._dimension = dimension
        self._model_id = model_id

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_id(self) -> str | None:
        """Optional Hugging Face / sentence-transformers model id for provenance."""
        return self._model_id

    def embed(self, text: str) -> list[float]:
        """Return a zero vector of dimension length."""
        return [0.0] * self._dimension

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Return zero vectors for each text."""
        return [[0.0] * self._dimension for _ in texts]


class SentenceTransformerProvider:
    """Embedding provider backed by sentence-transformers.

    Requires sentence-transformers package (core dependency).
    """

    def __init__(self, model_name: str = _DEFAULT_MODEL) -> None:
        if SentenceTransformer is None:
            msg = (
                "sentence-transformers is required but not installed. "
                "Install with: pip install tapps-brain"
            )
            raise ImportError(msg)
        self._model_name = model_name
        self._model = SentenceTransformer(model_name)
        raw_dim = self._model.get_sentence_embedding_dimension()
        self._dim: int = int(raw_dim) if raw_dim is not None else 384

    @property
    def model_id(self) -> str:
        """Sentence-transformers model name (stored with embeddings for reindex planning)."""
        return self._model_name

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
    semantic_search_enabled: bool = True,
    provider: str = "sentence_transformers",
    model: str = _DEFAULT_MODEL,
) -> EmbeddingProvider | None:
    """Return an EmbeddingProvider when semantic search is enabled, else None.

    Semantic search is enabled by default since sqlite-vec is a core
    dependency.  Pass ``semantic_search_enabled=False`` to disable.

    Args:
        semantic_search_enabled: Whether semantic search is turned on (config).
            Defaults to True.
        provider: Provider name; only "sentence_transformers" supported.
        model: Model name for sentence-transformers (e.g. all-MiniLM-L6-v2).

    Returns:
        EmbeddingProvider when enabled and provider available, else None.
    """
    if not semantic_search_enabled:
        return None

    if provider == "sentence_transformers":
        try:
            return SentenceTransformerProvider(model_name=model)
        except (OSError, RuntimeError, ValueError) as e:
            logger.warning("embedding_provider_init_failed", error=str(e))
            return None

    logger.debug("embedding_provider_unknown", provider=provider)
    return None
