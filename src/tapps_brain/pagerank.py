"""PageRank scoring for memory relationship graphs.

Computes importance scores for memories based on their connections.
PR(i) = (1-d)/N + d * sum_j(PR(j)/L(j))
Pure Python, no external dependencies.
"""
from __future__ import annotations


def compute_pagerank(
    edges: list[tuple[str, str, float]],
    d: float = 0.85,
    iterations: int = 30,
    tol: float = 1e-6,
) -> dict[str, float]:
    """Compute PageRank scores from a list of weighted edges.

    Args:
        edges: list of (source_key, target_key, weight) tuples
        d: damping factor (default 0.85)
        iterations: max iterations
        tol: convergence tolerance

    Returns:
        {memory_key: pagerank_score} normalized to [0, 1]
    """
    if not edges:
        return {}

    # Build adjacency
    nodes: set[str] = set()
    outgoing: dict[str, list[tuple[str, float]]] = {}
    for src, tgt, w in edges:
        nodes.add(src)
        nodes.add(tgt)
        outgoing.setdefault(src, []).append((tgt, w))

    n = len(nodes)
    if n == 0:
        return {}

    # Initialize scores
    scores: dict[str, float] = {node: 1.0 / n for node in nodes}

    for _ in range(iterations):
        new_scores: dict[str, float] = {}
        for node in nodes:
            rank_sum = 0.0
            # Find all nodes that point TO this node
            for src in nodes:
                out_edges = outgoing.get(src, [])
                total_out_weight = sum(w for _, w in out_edges)
                if total_out_weight > 0:
                    for tgt, w in out_edges:
                        if tgt == node:
                            rank_sum += scores[src] * w / total_out_weight
            new_scores[node] = (1 - d) / n + d * rank_sum

        # Check convergence
        diff = sum(abs(new_scores[k] - scores[k]) for k in nodes)
        scores = new_scores
        if diff < tol:
            break

    # Normalize to [0, 1]
    max_score = max(scores.values()) if scores else 1.0
    if max_score > 0:
        scores = {k: v / max_score for k, v in scores.items()}

    return scores
