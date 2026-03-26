"""Tests for PageRank scoring of memory relationship graphs."""

from __future__ import annotations

import pytest

from tapps_brain.pagerank import compute_pagerank


class TestComputePagerank:
    def test_empty_edges_returns_empty(self):
        result = compute_pagerank([])
        assert result == {}

    def test_single_edge(self):
        result = compute_pagerank([("a", "b", 1.0)])
        assert set(result.keys()) == {"a", "b"}
        # All scores in [0, 1]
        for score in result.values():
            assert 0.0 <= score <= 1.0
        # b is pointed to, so b should have higher or equal rank than a
        assert result["b"] >= result["a"]

    def test_scores_normalized_to_1(self):
        edges = [("a", "b", 1.0), ("b", "c", 1.0), ("c", "a", 1.0)]
        result = compute_pagerank(edges)
        assert max(result.values()) == pytest.approx(1.0, abs=1e-6)

    def test_hub_node_ranks_highest(self):
        """A node pointed to by many others should rank highest."""
        # a, b, c, d all point to hub
        edges = [
            ("a", "hub", 1.0),
            ("b", "hub", 1.0),
            ("c", "hub", 1.0),
            ("d", "hub", 1.0),
        ]
        result = compute_pagerank(edges)
        assert result["hub"] == pytest.approx(1.0, abs=1e-6)
        for node in ["a", "b", "c", "d"]:
            assert result[node] < result["hub"]

    def test_symmetric_graph_equal_scores(self):
        """In a symmetric mutual-link graph, all nodes should have equal rank."""
        edges = [
            ("a", "b", 1.0),
            ("b", "a", 1.0),
            ("b", "c", 1.0),
            ("c", "b", 1.0),
            ("a", "c", 1.0),
            ("c", "a", 1.0),
        ]
        result = compute_pagerank(edges)
        scores = list(result.values())
        # All should be equal (normalized to 1.0)
        for score in scores:
            assert score == pytest.approx(scores[0], abs=1e-4)

    def test_weighted_edges(self):
        """Higher weight edges should boost target node rank more."""
        edges = [
            ("src", "high_weight", 10.0),
            ("src", "low_weight", 1.0),
        ]
        result = compute_pagerank(edges)
        assert result["high_weight"] > result["low_weight"]

    def test_all_scores_in_range(self):
        edges = [
            ("mem:1", "mem:2", 0.9),
            ("mem:2", "mem:3", 0.7),
            ("mem:3", "mem:1", 0.5),
            ("mem:1", "mem:3", 0.3),
        ]
        result = compute_pagerank(edges)
        for key, score in result.items():
            assert 0.0 <= score <= 1.0, f"Score for {key} out of range: {score}"

    def test_custom_damping_factor(self):
        edges = [("a", "b", 1.0), ("b", "c", 1.0)]
        result_default = compute_pagerank(edges, d=0.85)
        result_low_d = compute_pagerank(edges, d=0.1)
        # Both should produce valid results
        assert set(result_default.keys()) == {"a", "b", "c"}
        assert set(result_low_d.keys()) == {"a", "b", "c"}
        # With low damping, scores are more uniform
        default_scores = sorted(result_default.values())
        low_d_scores = sorted(result_low_d.values())
        # Lowest with low d should be higher relative (more uniform distribution)
        assert low_d_scores[0] >= default_scores[0] - 1e-6  # at least as uniform

    def test_single_node_self_loop(self):
        """Self-loop: node points to itself."""
        result = compute_pagerank([("a", "a", 1.0)])
        assert "a" in result
        assert result["a"] == pytest.approx(1.0, abs=1e-6)

    def test_disconnected_components(self):
        """Two disconnected components — all nodes still get scores."""
        edges = [
            ("a", "b", 1.0),
            ("x", "y", 1.0),
        ]
        result = compute_pagerank(edges)
        assert set(result.keys()) == {"a", "b", "x", "y"}
        for score in result.values():
            assert 0.0 <= score <= 1.0

    def test_memory_key_format(self):
        """Test with realistic memory key formats."""
        edges = [
            ("mem:abc123", "mem:def456", 0.8),
            ("mem:def456", "mem:ghi789", 0.6),
            ("mem:ghi789", "mem:abc123", 0.4),
        ]
        result = compute_pagerank(edges)
        assert set(result.keys()) == {"mem:abc123", "mem:def456", "mem:ghi789"}
        assert max(result.values()) == pytest.approx(1.0, abs=1e-6)

    def test_convergence_within_iterations(self):
        """Algorithm should converge for simple graphs."""
        # Simple chain: a -> b -> c -> d
        edges = [("a", "b", 1.0), ("b", "c", 1.0), ("c", "d", 1.0)]
        # With enough iterations, result should be stable
        result_30 = compute_pagerank(edges, iterations=30)
        result_100 = compute_pagerank(edges, iterations=100)
        for k in result_30:
            assert result_30[k] == pytest.approx(result_100[k], abs=1e-4)
