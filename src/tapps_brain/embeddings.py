"""Embedding utilities for semantic search (Epic 65.7).

Provides ``SentenceTransformerProvider`` backed by sentence-transformers
(core dependency) and pure-Python helpers for quantization, normalization,
and cosine similarity.

Used by Epic 65.8 hybrid search (BM25 + vector).

Operator-facing defaults (model id, dimension, license, upgrade notes):
``docs/guides/embedding-model-card.md`` (STORY-042.2).
"""

from __future__ import annotations

import math
import os
import struct
from typing import Any, cast

import structlog

logger = structlog.get_logger(__name__)

# sentence-transformers is a core dependency.
try:
    from sentence_transformers import SentenceTransformer
except ImportError:  # pragma: no cover — should not happen with correct install
    SentenceTransformer = None  # type: ignore[assignment, misc]

_DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"

# Pinned git revision (commit SHA) for BAAI/bge-small-en-v1.5 on HuggingFace Hub.
# Prevents silent supply-chain / model-swap risk on cache-cold container starts.
# To update: verify the new SHA at https://huggingface.co/BAAI/bge-small-en-v1.5/commits/main
# then run the benchmark suite to confirm recall parity before committing the change.
_DEFAULT_MODEL_REVISION = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"

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


class SentenceTransformerProvider:
    """Embedding provider backed by sentence-transformers (core dependency).

    Args:
        model_name: HuggingFace model identifier (default: ``BAAI/bge-small-en-v1.5``).
        revision: Exact git revision (commit SHA or tag) to load from the Hub.
            Defaults to :data:`_DEFAULT_MODEL_REVISION` so that every fresh container
            pull loads the same weights.  Pass ``None`` to disable pinning (not
            recommended in production — you lose supply-chain guarantees).

    Environment:
        ``TAPPS_BRAIN_EMBEDDING_MODEL_OFFLINE=1`` — sets ``HF_HUB_OFFLINE=1`` before
        any Hub contact so the model is loaded entirely from the local cache.  If the
        cached model is absent or does not match *revision*, sentence-transformers will
        raise an error (fail-loud, no silent fallback).
    """

    def __init__(
        self,
        model_name: str = _DEFAULT_MODEL,
        *,
        revision: str | None = _DEFAULT_MODEL_REVISION,
    ) -> None:
        if SentenceTransformer is None:
            msg = (
                "sentence-transformers is required but not installed. "
                "Install with: pip install 'tapps-brain[all]'"
            )
            raise ImportError(msg)

        # Honour offline-mode flag *before* any Hub contact attempt.
        if os.environ.get("TAPPS_BRAIN_EMBEDDING_MODEL_OFFLINE", "0") == "1":
            os.environ["HF_HUB_OFFLINE"] = "1"
            logger.info(
                "embedding_offline_mode_active",
                model_name=model_name,
                revision=revision,
            )

        self._model_name = model_name
        self._revision = revision

        st_kwargs: dict[str, Any] = {}
        if revision is not None:
            st_kwargs["revision"] = revision

        self._model = SentenceTransformer(model_name, **st_kwargs)
        raw_dim = self._model.get_sentence_embedding_dimension()
        self._dim: int = int(raw_dim) if raw_dim is not None else 384

    @property
    def model_id(self) -> str:
        """Composite model identity string (``name@revision`` or just ``name``).

        Stored alongside embeddings in ``embedding_model_id`` so a revision mismatch
        on cold-start can be detected and a re-index triggered.
        """
        if self._revision:
            return f"{self._model_name}@{self._revision}"
        return self._model_name

    @property
    def model_revision(self) -> str | None:
        """Pinned revision SHA, or ``None`` when pinning is disabled."""
        return self._revision

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
    model: str = _DEFAULT_MODEL,
    *,
    revision: str | None = _DEFAULT_MODEL_REVISION,
) -> SentenceTransformerProvider | None:
    """Return a ``SentenceTransformerProvider``, or None if unavailable.

    Args:
        model: HuggingFace model identifier.
        revision: Pinned git revision forwarded to :class:`SentenceTransformerProvider`.
            Defaults to :data:`_DEFAULT_MODEL_REVISION` for supply-chain safety.

    Returns None (with a warning) when sentence-transformers is not installed
    or the model fails to load — e.g. in test environments.

    A missing dependency is logged at WARNING (not DEBUG) so operators see the
    degradation at default log levels. Semantic recall silently falls back to
    BM25-only when this returns None; the WARNING makes that observable without
    requiring DEBUG logging.
    """
    try:
        return SentenceTransformerProvider(model_name=model, revision=revision)
    except ImportError:
        logger.warning(
            "embedding_provider_unavailable",
            reason="sentence-transformers not installed",
            install_hint="pip install 'tapps-brain[all]'",
            embedding_degraded=True,
        )
        return None
    except (OSError, RuntimeError, ValueError) as e:
        logger.warning("embedding_provider_init_failed", error=str(e), embedding_degraded=True)
        return None
