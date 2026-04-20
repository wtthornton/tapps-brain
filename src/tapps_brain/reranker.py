"""Optional reranker for memory retrieval (Epic 65.9).

Cross-encoder reranking improves precision after BM25/hybrid retrieval.
Pass top-20 candidates to reranker and return reranked top_k.

Providers: noop (passthrough), flashrank (local cross-encoder, recommended).

Install: ``pip install tapps-brain[reranker]`` for FlashRank.

Observability (EPIC-042.6): ``MemoryRetriever._apply_reranker`` logs
``memory_rerank`` (latency, provider label, candidate counts). FlashRank
runs entirely on-device — no data leaves the machine.
"""

from __future__ import annotations

import importlib.util
from typing import Any, Protocol, runtime_checkable

import structlog

logger = structlog.get_logger(__name__)

# Top candidates to pass to reranker (Epic 65.9)
RERANKER_TOP_CANDIDATES = 20

# Default FlashRank model — ~4MB, fast on CPU.
_DEFAULT_FLASHRANK_MODEL = "ms-marco-TinyBERT-L-2-v2"


@runtime_checkable
class Reranker(Protocol):
    """Protocol for memory reranking.

    Input: query, list of (entry_key, value) pairs.
    Output: top_k (entry_key, relevance_score) pairs, ordered by relevance.
    """

    def rerank(
        self,
        query: str,
        candidates: list[tuple[str, str]],
        top_k: int,
    ) -> list[tuple[str, float]]:
        """Rerank candidates by relevance to the query.

        Args:
            query: Search query.
            candidates: List of (entry_key, value) pairs.
            top_k: Maximum number of results to return.

        Returns:
            List of (entry_key, score) pairs, ordered by relevance descending.
        """
        ...


class NoopReranker:
    """Passthrough reranker that preserves order and assigns scores from position."""

    def rerank(
        self,
        query: str,
        candidates: list[tuple[str, str]],
        top_k: int,
    ) -> list[tuple[str, float]]:
        """Return top_k candidates in order with position-based scores.

        Score = 1.0 - (index / max(1, len(candidates))) to preserve ranking.
        """
        result: list[tuple[str, float]] = []
        n = max(1, len(candidates))
        for i, (key, _) in enumerate(candidates[:top_k]):
            score = 1.0 - (i / n)
            result.append((key, round(score, 4)))
        return result


class FlashRankReranker:
    """Local cross-encoder reranker using FlashRank.

    Requires: ``pip install tapps-brain[reranker]``
    No API key needed. Runs entirely on CPU.
    Default model: ``ms-marco-TinyBERT-L-2-v2`` (~4MB).
    """

    def __init__(self, model: str = _DEFAULT_FLASHRANK_MODEL) -> None:
        self._model_name = model
        self._ranker: Any = None  # lazy init — avoid model load at factory time

    def _get_ranker(self) -> Any:  # noqa: ANN401
        """Lazy-initialize the FlashRank Ranker on first use."""
        if self._ranker is None:
            from flashrank import Ranker

            self._ranker = Ranker(model_name=self._model_name)
            logger.info("flashrank_ranker_loaded", model=self._model_name)
        return self._ranker

    def rerank(
        self,
        query: str,
        candidates: list[tuple[str, str]],
        top_k: int,
    ) -> list[tuple[str, float]]:
        """Rerank candidates using FlashRank local cross-encoder."""
        if not candidates:
            return []

        keys = [k for k, _ in candidates]
        passages = [{"id": i, "text": v} for i, (_, v) in enumerate(candidates)]
        effective_top_k = min(top_k, len(candidates))

        try:
            from flashrank import RerankRequest

            ranker = self._get_ranker()
            request = RerankRequest(query=query, passages=passages)
            results = ranker.rerank(request)
        except Exception as e:  # noqa: BLE001 — flashrank raises heterogeneous errors (model load, tokenizer); fallback to noop ranking
            logger.warning(
                "flashrank_reranker_failed",
                reason=str(e),
                fallback="noop",
            )
            return _noop_fallback(candidates, top_k)

        result: list[tuple[str, float]] = []
        for item in results:
            idx = int(item["id"])
            if 0 <= idx < len(keys):
                raw_score = float(item["score"])
                score = max(0.0, min(1.0, raw_score))
                result.append((keys[idx], round(score, 4)))
                if len(result) >= effective_top_k:
                    break
        return result


def _noop_fallback(
    candidates: list[tuple[str, str]],
    top_k: int,
) -> list[tuple[str, float]]:
    """Fallback when reranker fails: preserve order, assign position scores."""
    return NoopReranker().rerank("", candidates, top_k)


def _flashrank_available() -> bool:
    """Return True if flashrank is importable."""
    return importlib.util.find_spec("flashrank") is not None


def get_reranker(
    enabled: bool,
    model: str | None = None,
) -> Reranker:
    """Create a Reranker based on availability.

    Args:
        enabled: Whether reranking is enabled.
        model: Optional FlashRank model name override.

    Returns:
        FlashRankReranker when enabled and flashrank installed;
        NoopReranker otherwise.
    """
    if not enabled:
        return NoopReranker()

    if _flashrank_available():
        return FlashRankReranker(model=model or _DEFAULT_FLASHRANK_MODEL)

    logger.debug("flashrank_not_installed", fallback="noop")
    return NoopReranker()


def reranker_provider_label(reranker: Reranker) -> str:
    """Return a short label for observability logging."""
    if isinstance(reranker, FlashRankReranker):
        return "flashrank"
    return "noop"
