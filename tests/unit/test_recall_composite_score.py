"""VAL-01 (TAP-6696): brain_recall must serialize a numeric composite score.

``store.search()`` returns entries pre-ordered by the backend's relevance
signal without exposing its magnitude. ``brain_recall`` now scores every
candidate with ``MemoryRetriever.score_by_rank`` (relevance derived from rank
position + confidence/recency/frequency) and sorts the winners desc before
truncating to ``max_results`` — see retrieval.py::MemoryRetriever.score_by_rank
and services/memory_service.py::brain_recall.
"""

from __future__ import annotations

from typing import Any

from tapps_brain.services import memory_service
from tests.factories import make_entry


class _MockStore:
    """Minimal store stub — mirrors test_memory_status.py's ``_MockStore``."""

    def __init__(self, entries: list[Any]) -> None:
        self._entries = list(entries)

    def search(self, query: str, **_kwargs: Any) -> list[Any]:
        return list(self._entries)


class TestBrainRecallCompositeScore:
    def test_every_result_has_numeric_score(self) -> None:
        entries = [
            make_entry(key="a", confidence=0.9),
            make_entry(key="b", confidence=0.5),
            make_entry(key="c", confidence=0.2),
        ]
        results = memory_service.brain_recall(
            _MockStore(entries), "proj", "agent", query="test", max_results=10
        )
        assert len(results) == 3
        for item in results:
            assert isinstance(item["score"], float)

    def test_results_sorted_descending_by_score(self) -> None:
        entries = [
            make_entry(key="low", confidence=0.1, access_count=0),
            make_entry(key="high", confidence=0.99, access_count=50),
            make_entry(key="mid", confidence=0.5, access_count=5),
        ]
        results = memory_service.brain_recall(
            _MockStore(entries), "proj", "agent", query="test", max_results=10
        )
        scores = [item["score"] for item in results]
        assert scores == sorted(scores, reverse=True)

    def test_five_results_show_at_least_two_distinct_scores(self) -> None:
        entries = [
            make_entry(key=f"k{i}", confidence=c, access_count=i * 3)
            for i, c in enumerate([0.9, 0.7, 0.5, 0.3, 0.1])
        ]
        results = memory_service.brain_recall(
            _MockStore(entries), "proj", "agent", query="test", max_results=5
        )
        assert len(results) == 5
        distinct_scores = {item["score"] for item in results}
        assert len(distinct_scores) >= 2

    def test_score_ranks_ahead_of_max_results_cut(self) -> None:
        """Scoring must consider the FULL candidate set, not just a naive first-N scan.

        ``winner`` sits mid-pack in search order (rank 5 of 10) but dominates
        on confidence + access frequency; with the old break-after-max_results
        loop, ``max_results=1`` would return the rank-0 filler and never even
        look at ``winner``.
        """
        fillers = [make_entry(key=f"filler{i}", confidence=0.05) for i in range(9)]
        winner = make_entry(key="winner", confidence=1.0, access_count=100)
        entries = [*fillers[:5], winner, *fillers[5:]]
        results = memory_service.brain_recall(
            _MockStore(entries), "proj", "agent", query="test", max_results=1
        )
        assert len(results) == 1
        assert results[0]["key"] == "winner"
