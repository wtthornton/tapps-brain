"""Tests for Louvain community detection."""
from __future__ import annotations

import pytest
from tapps_brain.louvain import detect_communities, group_by_community, _modularity_gain


class TestModularityGain:
    def test_zero_total_weight_returns_zero(self):
        result = _modularity_gain(
            node=0,
            community=1,
            node_weights={0: {1: 0.5}, 1: {0: 0.5}},
            community_members={1: {1}},
            total_weight=0.0,
        )
        assert result == 0.0

    def test_positive_gain_for_connected_community(self):
        # Node 0 connected to nodes 1 and 2; nodes 1 and 2 are in same community
        node_weights = {
            0: {1: 0.8, 2: 0.7},
            1: {0: 0.8, 2: 0.9},
            2: {0: 0.7, 1: 0.9},
        }
        community_members = {1: {1, 2}}
        total_weight = (0.8 + 0.7 + 0.9) / 1  # sum of all directed / 2 = symmetric

        gain = _modularity_gain(
            node=0,
            community=1,
            node_weights=node_weights,
            community_members=community_members,
            total_weight=total_weight,
        )
        assert gain > 0.0

    def test_empty_community_returns_zero_or_negative(self):
        node_weights = {0: {1: 0.5}, 1: {0: 0.5}}
        community_members: dict = {}
        gain = _modularity_gain(
            node=0,
            community=99,
            node_weights=node_weights,
            community_members=community_members,
            total_weight=0.5,
        )
        assert gain == 0.0  # ki_in=0, sigma_tot=0 → 0


class TestDetectCommunities:
    def test_empty_matrix_returns_empty(self):
        result = detect_communities({})
        assert result == {}

    def test_single_node(self):
        result = detect_communities({0: {}})
        assert result == {0: 0}

    def test_disconnected_nodes_stay_separate(self):
        # No edges between nodes — all below threshold
        matrix = {
            0: {1: 0.1},
            1: {0: 0.1},
            2: {3: 0.1},
            3: {2: 0.1},
        }
        result = detect_communities(matrix, min_similarity=0.5)
        # Each node stays in its own community since no edges pass threshold
        assert len(set(result.values())) == 4

    def test_two_tight_clusters(self):
        # Nodes 0,1,2 strongly connected; nodes 3,4,5 strongly connected; cross-links weak
        matrix = {
            0: {1: 0.9, 2: 0.85, 3: 0.1, 4: 0.05},
            1: {0: 0.9, 2: 0.88, 3: 0.05, 4: 0.1},
            2: {0: 0.85, 1: 0.88, 3: 0.08, 5: 0.05},
            3: {0: 0.1, 1: 0.05, 4: 0.92, 5: 0.87},
            4: {0: 0.05, 1: 0.1, 3: 0.92, 5: 0.91},
            5: {2: 0.05, 3: 0.87, 4: 0.91},
        }
        result = detect_communities(matrix, min_similarity=0.3)
        # Nodes 0,1,2 should be in same community; nodes 3,4,5 in another
        assert result[0] == result[1] == result[2]
        assert result[3] == result[4] == result[5]
        assert result[0] != result[3]

    def test_all_nodes_assigned(self):
        matrix = {i: {j: 0.5 for j in range(5) if j != i} for i in range(5)}
        result = detect_communities(matrix, min_similarity=0.3)
        assert set(result.keys()) == set(range(5))

    def test_min_similarity_filters_weak_edges(self):
        # All edges are weak — no communities should form
        matrix = {
            0: {1: 0.1, 2: 0.2},
            1: {0: 0.1, 2: 0.15},
            2: {0: 0.2, 1: 0.15},
        }
        result = detect_communities(matrix, min_similarity=0.5)
        # Each node stays in its own community
        assert len(set(result.values())) == 3

    def test_returns_dict_int_to_int(self):
        matrix = {0: {1: 0.8}, 1: {0: 0.8}}
        result = detect_communities(matrix)
        assert isinstance(result, dict)
        for k, v in result.items():
            assert isinstance(k, int)
            assert isinstance(v, int)


class TestGroupByCommunity:
    def test_empty_mapping(self):
        result = group_by_community({})
        assert result == []

    def test_all_singletons_returns_empty(self):
        # Each node in own community → no groups of size >= 2
        result = group_by_community({0: 0, 1: 1, 2: 2})
        assert result == []

    def test_two_nodes_same_community(self):
        result = group_by_community({0: 0, 1: 0})
        assert len(result) == 1
        assert sorted(result[0]) == [0, 1]

    def test_multiple_communities(self):
        mapping = {0: 0, 1: 0, 2: 2, 3: 2, 4: 4}
        result = group_by_community(mapping)
        # Should have 2 groups; singleton 4 excluded
        assert len(result) == 2
        groups = [sorted(g) for g in result]
        assert [0, 1] in groups
        assert [2, 3] in groups

    def test_large_community(self):
        mapping = {i: 0 for i in range(10)}
        result = group_by_community(mapping)
        assert len(result) == 1
        assert sorted(result[0]) == list(range(10))

    def test_integration_detect_then_group(self):
        matrix = {
            0: {1: 0.9, 2: 0.85},
            1: {0: 0.9, 2: 0.88},
            2: {0: 0.85, 1: 0.88},
            3: {4: 0.92},
            4: {3: 0.92},
        }
        communities = detect_communities(matrix, min_similarity=0.3)
        groups = group_by_community(communities)
        assert len(groups) >= 1
        # All of 0,1,2 should be together
        for group in groups:
            if 0 in group:
                assert 1 in group
                assert 2 in group
