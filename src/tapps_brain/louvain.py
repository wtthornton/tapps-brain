"""Louvain-style community detection for memory consolidation.

Simplified Louvain algorithm (Blondel et al. 2008) for grouping
similar memories. Pure Python, no external dependencies.
"""

from __future__ import annotations

_MIN_GROUP_SIZE = 2


def _modularity_gain(
    node: int,
    community: int,
    node_weights: dict[int, dict[int, float]],
    community_members: dict[int, set[int]],
    total_weight: float,
) -> float:
    """Calculate modularity gain from moving node to community."""
    if total_weight == 0:
        return 0.0
    # Sum of weights to community members
    ki_in = sum(node_weights[node].get(m, 0.0) for m in community_members.get(community, set()))
    # Sum of all weights of node
    ki = sum(node_weights[node].values())
    # Sum of all weights in community
    sigma_tot = sum(sum(node_weights[m].values()) for m in community_members.get(community, set()))
    return ki_in / total_weight - (sigma_tot * ki) / (2 * total_weight * total_weight)


def detect_communities(
    similarity_matrix: dict[int, dict[int, float]],
    min_similarity: float = 0.3,
    max_iterations: int = 10,
) -> dict[int, int]:
    """Detect communities using simplified Louvain algorithm.

    Args:
        similarity_matrix: {node_id: {neighbor_id: similarity_score}}
        min_similarity: minimum edge weight to consider
        max_iterations: max passes over all nodes

    Returns:
        {node_id: community_id} mapping
    """
    nodes = list(similarity_matrix.keys())
    if not nodes:
        return {}

    # Initialize: each node in its own community
    node_to_community: dict[int, int] = {n: n for n in nodes}
    community_members: dict[int, set[int]] = {n: {n} for n in nodes}

    # Filter edges by min_similarity
    filtered: dict[int, dict[int, float]] = {}
    for n in nodes:
        filtered[n] = {
            m: w for m, w in similarity_matrix[n].items() if w >= min_similarity and m != n
        }

    total_weight = sum(sum(edges.values()) for edges in filtered.values()) / 2
    if total_weight == 0:
        return node_to_community

    for _ in range(max_iterations):
        moved = False
        for node in nodes:
            current_comm = node_to_community[node]
            # Remove node from current community
            community_members[current_comm].discard(node)

            # Try neighboring communities
            best_comm = current_comm
            best_gain = 0.0

            neighbor_comms = {node_to_community[m] for m in filtered[node]}
            neighbor_comms.add(current_comm)

            for comm in neighbor_comms:
                gain = _modularity_gain(node, comm, filtered, community_members, total_weight)
                if gain > best_gain:
                    best_gain = gain
                    best_comm = comm

            # Move to best community
            node_to_community[node] = best_comm
            if best_comm not in community_members:
                community_members[best_comm] = set()
            community_members[best_comm].add(node)

            if best_comm != current_comm:
                moved = True

        if not moved:
            break

    # Clean up empty communities
    return node_to_community


def group_by_community(node_to_community: dict[int, int]) -> list[list[int]]:
    """Convert node→community mapping to list of groups."""
    communities: dict[int, list[int]] = {}
    for node, comm in node_to_community.items():
        communities.setdefault(comm, []).append(node)
    return [group for group in communities.values() if len(group) >= _MIN_GROUP_SIZE]
