"""Unit tests for KG neighbourhood edge scoring (STORY-076.2).

Tests score_edge() and MemoryRetriever.search_neighborhood() without a DB.
DB-dependent tests live in tests/integration/test_kg_neighborhood.py.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from tapps_brain.retrieval import MemoryRetriever, ScoredEdge, score_edge

# ---------------------------------------------------------------------------
# score_edge() unit tests
# ---------------------------------------------------------------------------


def _edge(**kw: Any) -> dict[str, Any]:
    """Build a minimal edge dict with sensible defaults."""
    defaults: dict[str, Any] = {
        "edge_confidence": 0.8,
        "last_reinforced": None,
        "useful_access_count": 5,
        "access_count": 10,
        "source": "agent",
        "evidence_count": 2,
        "edge_status": "active",
        "contradicted": False,
    }
    return {**defaults, **kw}


class TestScoreEdge:
    def test_active_high_confidence_edge_scores_high(self) -> None:
        e = _edge(edge_confidence=1.0, source="human", evidence_count=10)
        s = score_edge(e)
        assert s >= 0.80, f"Expected high score, got {s:.3f}"

    def test_contradicted_edge_scores_lower(self) -> None:
        active = score_edge(_edge(edge_status="active", contradicted=False))
        contradicted = score_edge(_edge(edge_status="stale", contradicted=True))
        assert contradicted < active

    def test_score_range_0_to_1(self) -> None:
        for conf in [0.0, 0.3, 0.5, 0.8, 1.0]:
            s = score_edge(_edge(edge_confidence=conf))
            assert 0.0 <= s <= 1.0, f"score={s} out of range for confidence={conf}"

    def test_low_confidence_scores_below_high(self) -> None:
        low = score_edge(_edge(edge_confidence=0.1))
        high = score_edge(_edge(edge_confidence=0.9))
        assert low < high

    def test_human_source_scores_higher_than_inferred(self) -> None:
        human = score_edge(_edge(source="human"))
        inferred = score_edge(_edge(source="inferred"))
        assert human > inferred

    def test_more_evidence_scores_higher(self) -> None:
        no_ev = score_edge(_edge(evidence_count=0))
        many_ev = score_edge(_edge(evidence_count=10))
        assert many_ev > no_ev

    def test_missing_fields_do_not_raise(self) -> None:
        # Sparse edge dict — only edge_status is set
        s = score_edge({"edge_status": "active"})
        assert 0.0 <= s <= 1.0

    def test_usefulness_ratio_improves_score(self) -> None:
        useless = score_edge(_edge(useful_access_count=0, access_count=10))
        useful = score_edge(_edge(useful_access_count=10, access_count=10))
        assert useful > useless


# ---------------------------------------------------------------------------
# MemoryRetriever.search_neighborhood() unit tests
# ---------------------------------------------------------------------------


def _make_retriever(graph_weight: float = 0.10) -> MemoryRetriever:
    sc = MagicMock()
    sc.graph_weight = graph_weight
    sc.relevance = 0.40
    sc.confidence = 0.30
    sc.recency = 0.15
    sc.frequency = 0.15
    sc.frequency_cap = 20
    sc.graph_centrality = 0.0
    sc.provenance_trust = 0.0
    sc.source_trust = {"human": 1.0, "agent": 0.7}
    return MemoryRetriever(scoring_config=sc)


def _make_edge_row(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "edge_id": "e-001",
        "predicate": "uses",
        "neighbor_id": "n-001",
        "entity_type": "concept",
        "canonical_name": "Postgres",
        "hop": 1,
        "edge_confidence": 0.8,
        "useful_access_count": 3,
        "access_count": 5,
        "source": "agent",
        "evidence_count": 1,
        "edge_status": "active",
        "contradicted": False,
        "last_reinforced": None,
    }
    return {**base, **kw}


class TestSearchNeighborhood:
    def test_returns_scored_edges(self) -> None:
        backend = MagicMock()
        backend.get_neighbors_multi.return_value = [_make_edge_row()]

        retriever = _make_retriever(graph_weight=0.10)
        results = retriever.search_neighborhood(["entity-uuid"], backend)

        assert len(results) == 1
        r = results[0]
        assert isinstance(r, ScoredEdge)
        assert r.edge_id == "e-001"
        assert 0.0 <= r.score <= 1.0
        assert r.blended_score == pytest.approx(r.score * 0.10, abs=1e-6)

    def test_empty_entity_ids_returns_empty_no_db_call(self) -> None:
        backend = MagicMock()
        retriever = _make_retriever()
        result = retriever.search_neighborhood([], backend)
        assert result == []
        backend.get_neighbors_multi.assert_not_called()

    def test_backend_error_returns_empty(self) -> None:
        backend = MagicMock()
        backend.get_neighbors_multi.side_effect = RuntimeError("DB down")
        retriever = _make_retriever()
        result = retriever.search_neighborhood(["some-id"], backend)
        assert result == []

    def test_blended_score_is_score_times_graph_weight(self) -> None:
        backend = MagicMock()
        backend.get_neighbors_multi.return_value = [_make_edge_row()]

        gw = 0.25
        retriever = _make_retriever(graph_weight=gw)
        results = retriever.search_neighborhood(["x"], backend)

        assert results[0].blended_score == pytest.approx(results[0].score * gw, abs=1e-6)

    def test_higher_graph_weight_yields_higher_blended_score(self) -> None:
        row = _make_edge_row()
        backend_low = MagicMock()
        backend_low.get_neighbors_multi.return_value = [dict(row)]
        backend_high = MagicMock()
        backend_high.get_neighbors_multi.return_value = [dict(row)]

        r_low = _make_retriever(graph_weight=0.05).search_neighborhood(["x"], backend_low)
        r_high = _make_retriever(graph_weight=0.50).search_neighborhood(["x"], backend_high)

        assert r_low[0].blended_score < r_high[0].blended_score

    def test_results_sorted_by_blended_score_descending(self) -> None:
        backend = MagicMock()
        backend.get_neighbors_multi.return_value = [
            _make_edge_row(edge_id=f"e-{i:03d}", edge_confidence=i / 10.0)
            for i in range(10)
        ]
        results = _make_retriever().search_neighborhood(["some-entity"], backend)
        scores = [r.blended_score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_multiple_edges_all_returned(self) -> None:
        backend = MagicMock()
        backend.get_neighbors_multi.return_value = [
            _make_edge_row(edge_id=f"e-{i:03d}") for i in range(5)
        ]
        results = _make_retriever().search_neighborhood(["x"], backend)
        assert len(results) == 5

    def test_scored_edge_has_hop_field(self) -> None:
        backend = MagicMock()
        backend.get_neighbors_multi.return_value = [_make_edge_row(hop=2)]
        results = _make_retriever().search_neighborhood(["x"], backend)
        assert results[0].hop == 2
