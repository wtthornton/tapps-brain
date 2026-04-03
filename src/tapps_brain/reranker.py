"""Optional reranker for memory retrieval (Epic 65.9).

Cross-encoder reranking improves precision after BM25/hybrid retrieval.
Pass top-20 candidates to reranker and return reranked top_k.

Providers: noop (passthrough), cohere (API, optional dependency).

Observability (EPIC-042.6): ``MemoryRetriever._apply_reranker`` logs
``memory_rerank`` (latency, provider label, candidate counts); cloud
rerank sends snippets to the vendor — see profile/docs before enabling
``cohere`` in production.
"""

from __future__ import annotations

import importlib.util
from typing import Protocol, runtime_checkable

import structlog

logger = structlog.get_logger(__name__)

# Top candidates to pass to reranker (Epic 65.9)
RERANKER_TOP_CANDIDATES = 20


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


def _create_cohere_reranker(
    api_key: str,
    model: str = "rerank-v3.5",
) -> CohereReranker | None:
    """Create CohereReranker if cohere is available."""
    if importlib.util.find_spec("cohere") is None:
        logger.debug("cohere_reranker_unavailable", reason="cohere package not installed")
        return None
    return CohereReranker(api_key=api_key, model=model)


class CohereReranker:
    """Cohere API reranker for memory retrieval.

    Requires: pip install cohere
    API key: memory.reranker.api_key or COHERE_API_KEY env.
    """

    def __init__(self, api_key: str, model: str = "rerank-v3.5") -> None:
        self._api_key = api_key
        self._model = model

    def rerank(
        self,
        query: str,
        candidates: list[tuple[str, str]],
        top_k: int,
    ) -> list[tuple[str, float]]:
        """Rerank candidates using Cohere Rerank API."""
        if not candidates:
            return []
        if not self._api_key:
            logger.debug("cohere_reranker_skipped", reason="no_api_key")
            return _noop_fallback(candidates, top_k)

        try:
            import cohere
        except ImportError:
            logger.debug("cohere_reranker_fallback", reason="cohere not installed")
            return _noop_fallback(candidates, top_k)

        keys = [k for k, _ in candidates]
        documents = [v for _, v in candidates]
        effective_top_k = min(top_k, len(candidates))

        try:
            # Support both Client (v1) and ClientV2
            if hasattr(cohere, "ClientV2"):
                client = cohere.ClientV2(api_key=self._api_key)
                response = client.rerank(
                    model=self._model,
                    query=query,
                    documents=documents,
                    top_n=effective_top_k,
                )
            else:
                client = cohere.Client(api_key=self._api_key)
                response = client.rerank(
                    model=self._model,
                    query=query,
                    documents=documents,
                    top_n=effective_top_k,
                )
        except Exception as e:
            logger.warning(
                "cohere_reranker_failed",
                reason=str(e),
                fallback="noop",
            )
            return _noop_fallback(candidates, top_k)

        # Cohere returns results with index and relevance_score.
        # Clamp scores to [0.0, 1.0] — Cohere scores are nominally in this range
        # but clamping guards against unexpected values from future API changes.
        result: list[tuple[str, float]] = []
        for item in response.results:
            idx = item.index
            if 0 <= idx < len(keys):
                raw_score = float(getattr(item, "relevance_score", 1.0))
                score = max(0.0, min(1.0, raw_score))
                result.append((keys[idx], round(score, 4)))
        return result


def _noop_fallback(
    candidates: list[tuple[str, str]],
    top_k: int,
) -> list[tuple[str, float]]:
    """Fallback when reranker fails: preserve order, assign position scores."""
    return NoopReranker().rerank("", candidates, top_k)


def get_reranker(
    enabled: bool,
    provider: str,
    api_key: str | None = None,
) -> Reranker:
    """Create a Reranker from config.

    Args:
        enabled: Whether reranking is enabled.
        provider: "noop" or "cohere".
        api_key: API key for Cohere (required when provider=cohere).

    Returns:
        NoopReranker when disabled or provider=noop; CohereReranker when
        provider=cohere and api_key set; otherwise NoopReranker.
    """
    if not enabled:
        return NoopReranker()

    if provider == "cohere" and api_key:
        cohere_reranker = _create_cohere_reranker(api_key)
        if cohere_reranker is not None:
            return cohere_reranker
        logger.debug("cohere_reranker_fallback_to_noop", reason="unavailable")

    return NoopReranker()
