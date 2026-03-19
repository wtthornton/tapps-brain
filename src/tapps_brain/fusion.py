"""Reciprocal Rank Fusion (RRF) for hybrid search (Epic 65.8).

Merges ranked lists from BM25 and vector search into a single ranking
using the RRF formula: score = sum(1/(k + rank)) per document.
"""

from __future__ import annotations


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
        k: RRF constant (default 60 per Elasticsearch/Azure AI Search). Higher k
            reduces rank difference impact; typical range 10-100.

    Returns:
        List of (entry_key, fused_score) sorted by fused_score descending.
    """
    fused: dict[str, float] = {}

    for rank, key in enumerate(bm25_ranked, start=1):
        fused[key] = fused.get(key, 0.0) + 1.0 / (k + rank)

    for rank, key in enumerate(vector_ranked, start=1):
        fused[key] = fused.get(key, 0.0) + 1.0 / (k + rank)

    # Sort by fused score descending, then by key for stability
    sorted_items = sorted(
        fused.items(),
        key=lambda x: (-x[1], x[0]),
    )
    return sorted_items
