"""Reciprocal Rank Fusion (RRF) for hybrid search (Epic 65.8).

Merges ranked lists from BM25 and vector search into a single ranking.

**RRF score (multi-list).** For document *d*, let *r_i(d)* be the 1-based rank of *d*
in ranked list *i* if *d* appears, else treat that list as contributing 0. The fused
score is:

    RRF(d) = sum_i  c_i / (k + r_i(d))

where *k > 0* is a smoothing constant (default **60**) and *c_i* is a non-negative
weight for list *i*. Equal weighting (*c_i = 1*) is the common baseline; this
package uses *(c_bm25, c_vector)* from ``hybrid_rrf_weights_for_query`` when
adaptive fusion is enabled (EPIC-040 / GitHub #40).

**Reference:** Cormack, Clarke, and Buettcher, *Reciprocal Rank Fusion outperforms
Condorcet and individual Rank Learning Algorithms* (SIGIR / CEAS literature, 2009).
The same *k* in the denominator is widely adopted in production search (e.g.
Elasticsearch RRF, Azure AI Search RRF) with *k = 60* as a common default.

EPIC-040 / GitHub #40: query-aware BM25 vs vector weights bias fusion toward
keyword-heavy vs semantic-style queries without LLM calls.
"""

from __future__ import annotations

import re
from typing import Final

# BM25 share bounds for adaptive hybrid (vector share is 1 - bm25_share).
_ALPHA_LO: Final[float] = 0.35
_ALPHA_HI: Final[float] = 0.75
_ALPHA_MID: Final[float] = 0.5
_LEN_LONG_QUERY: Final[int] = 8
_LEN_MEDIUM_QUERY: Final[int] = 5
_LEN_SHORT_QUERY: Final[int] = 2
_LEN_VAGUE_QUERY: Final[int] = 4
_MIN_ALNUM_MIXED_TOKENS: Final[int] = 2
_BIAS_CLAMP: Final[int] = 4


def hybrid_rrf_weights_for_query(query: str) -> tuple[float, float]:
    """Return ``(bm25_weight, vector_weight)`` for weighted RRF from *query* text.

    Deterministic heuristics (no network, no ML): long / code-like queries skew
    toward BM25; short or question-like phrasing skew toward vector similarity.

    Returns:
        Positive weights that sum to 1.0 (first value is BM25 share).
    """
    q = query.strip()
    if not q:
        return (_ALPHA_MID, _ALPHA_MID)

    tokens = [t for t in re.split(r"\s+", q) if t]
    n = len(tokens)
    lower = q.lower()

    bias = 0  # positive → keyword/BM25, negative → semantic/vector

    if n >= _LEN_LONG_QUERY:
        bias += 2
    elif n >= _LEN_MEDIUM_QUERY:
        bias += 1
    elif n <= _LEN_SHORT_QUERY:
        bias -= 2
    elif n <= _LEN_VAGUE_QUERY:
        bias -= 1

    if lower.startswith(
        (
            "what ",
            "how ",
            "why ",
            "when ",
            "where ",
            "who ",
            "which ",
            "explain ",
            "describe ",
            "tell me ",
            "find anything ",
        )
    ) or lower.startswith(("what's ", "how's ")):
        bias -= 1
    if any(p in lower for p in ("similar to", "anything about", "related to")):
        bias -= 1

    if re.search(r"[(){}\[\];]", q):
        bias += 1
    if "::" in q or "->" in q:
        bias += 1
    if re.search(r"\.\w{2,6}\b", q):
        bias += 1
    if any(re.match(r"^[A-Za-z]+[A-Z][a-zA-Z0-9]*", t) for t in tokens):
        bias += 1
    if (
        sum(1 for t in tokens if any(c.isdigit() for c in t) and any(c.isalpha() for c in t))
        >= _MIN_ALNUM_MIXED_TOKENS
    ):
        bias += 1

    bias = max(-_BIAS_CLAMP, min(_BIAS_CLAMP, bias))
    if bias > 0:
        alpha = _ALPHA_MID + (bias / float(_BIAS_CLAMP)) * (_ALPHA_HI - _ALPHA_MID)
    elif bias < 0:
        alpha = _ALPHA_MID + (bias / float(_BIAS_CLAMP)) * (_ALPHA_MID - _ALPHA_LO)
    else:
        alpha = _ALPHA_MID

    alpha = max(_ALPHA_LO, min(_ALPHA_HI, alpha))
    return (alpha, 1.0 - alpha)


def reciprocal_rank_fusion_weighted(
    bm25_ranked: list[str],
    vector_ranked: list[str],
    *,
    bm25_weight: float = 1.0,
    vector_weight: float = 1.0,
    k: int = 60,
) -> list[tuple[str, float]]:
    """Fuse ranked lists with per-source RRF weights.

    For each distinct key *d*, fused score is::

        bm25_weight / (k + rank_bm25(d)) + vector_weight / (k + rank_vector(d))

    with the term omitted when *d* is absent from that list. Ranks are **1-based**
    (best document has rank 1). See module docstring for citation and notation.

    Args:
        bm25_ranked: Entry keys ordered by BM25 relevance (best first).
        vector_ranked: Entry keys ordered by vector similarity (best first).
        bm25_weight: Non-negative multiplier for BM25 rank contributions.
        vector_weight: Non-negative multiplier for vector rank contributions.
        k: RRF constant (default 60).

    Returns:
        ``(entry_key, fused_score)`` sorted by fused_score descending, then key.
    """
    if bm25_weight < 0.0 or vector_weight < 0.0:
        msg = "bm25_weight and vector_weight must be non-negative"
        raise ValueError(msg)

    fused: dict[str, float] = {}

    for rank, key in enumerate(bm25_ranked, start=1):
        fused[key] = fused.get(key, 0.0) + bm25_weight / (k + rank)

    for rank, key in enumerate(vector_ranked, start=1):
        fused[key] = fused.get(key, 0.0) + vector_weight / (k + rank)

    return sorted(
        fused.items(),
        key=lambda x: (-x[1], x[0]),
    )


def reciprocal_rank_fusion(
    bm25_ranked: list[str],
    vector_ranked: list[str],
    k: int = 60,
) -> list[tuple[str, float]]:
    """Fuse BM25 and vector ranked lists using Reciprocal Rank Fusion.

    For each document: score = sum(1/(k + rank)) across all lists
    where it appears. Deduplicates and returns sorted by fused score descending.

    Args:
        bm25_ranked: List of entry keys ordered by BM25 relevance (best first).
        vector_ranked: List of entry keys ordered by vector similarity (best first).
        k: RRF smoothing constant (default 60; see module docstring for references).
            Larger *k* dampens differences between ranks; typical tuning range ~10-100.

    Returns:
        List of (entry_key, fused_score) sorted by fused_score descending.
    """
    return reciprocal_rank_fusion_weighted(
        bm25_ranked,
        vector_ranked,
        bm25_weight=1.0,
        vector_weight=1.0,
        k=k,
    )
