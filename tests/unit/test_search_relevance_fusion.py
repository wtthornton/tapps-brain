"""TAP-7338: brain_recall must fuse FTS + vector, not FTS-then-fallback.

Covers:
  * ``QueryMixin.search`` (``_store_query.py``) — a row the FTS pass misses
    but the vector branch finds must surface even when FTS already returned
    other rows (VAL-13). ``store.last_search_relevance`` must carry the raw
    magnitude the fused row was found with.
  * ``MemoryRetriever.score_by_rank`` (``retrieval.py``) — when a raw
    magnitude is supplied, the emitted score tracks it (not rank position),
    and omitting it preserves the exact pre-TAP-7338 rank-position formula.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from tapps_brain.retrieval import MemoryRetriever
from tapps_brain.store import MemoryStore
from tests.factories import make_entry

_EMBED_DIM = 384


class _FakeEmbeddingProvider:
    model_id = "fake-model"

    def embed(self, text: str) -> list[float]:
        return [0.1] * _EMBED_DIM


@pytest.fixture()
def store(tmp_path: Path):  # type: ignore[no-untyped-def]
    s = MemoryStore(tmp_path, embedding_provider=_FakeEmbeddingProvider())  # type: ignore[arg-type]
    yield s
    s.close()


class TestSearchFusion:
    """VAL-13: the semantically correct row surfaces even when FTS is non-empty."""

    def test_vector_only_row_surfaces_alongside_fts_hits(self, store: MemoryStore) -> None:
        # FTS matches "alpha" lexically; the semantically-correct row shares
        # no tokens with the query and only the vector branch can find it.
        store.save(key="k-lexical", value="alpha keyword match")
        store.save(key="k-semantic", value="unrelated wording entirely")

        def _knn(embedding: list[float], k: int, **kwargs: Any) -> list[tuple[str, float]]:
            return [("k-semantic", 0.02)]

        store._persistence.knn_search = _knn  # type: ignore[attr-defined]

        # Negative control: under the pre-TAP-7338 FTS-then-fallback rule,
        # the vector branch never runs once FTS is non-empty, so the
        # semantic row would be absent here.
        fts_only = store._persistence.search("alpha")
        assert {e.key for e in fts_only} == {"k-lexical"}
        assert "k-semantic" not in {e.key for e in fts_only}

        # Post-change: fusion runs the vector branch regardless, so the
        # semantic row surfaces alongside the lexical hit.
        results = store.search("alpha")
        assert {e.key for e in results} == {"k-lexical", "k-semantic"}

    def test_exact_keyword_query_still_returns_the_row(self, store: MemoryStore) -> None:
        """Positive control: fusion must not break the plain keyword case."""
        store.save(key="k-exact", value="unique-token-xyz content")
        store._persistence.knn_search = lambda *a, **kw: []  # type: ignore[attr-defined]
        results = store.search("unique-token-xyz")
        assert [e.key for e in results] == ["k-exact"]

    def test_last_search_relevance_carries_raw_magnitude(self, store: MemoryStore) -> None:
        store.save(key="k-lexical", value="alpha keyword match")
        store.save(key="k-semantic", value="unrelated wording entirely")

        def _knn(embedding: list[float], k: int, **kwargs: Any) -> list[tuple[str, float]]:
            # distance 0.02 -> similarity 1/(1+0.02) ~= 0.980
            return [("k-semantic", 0.02)]

        store._persistence.knn_search = _knn  # type: ignore[attr-defined]
        store.search("alpha")
        assert "k-semantic" in store.last_search_relevance
        assert store.last_search_relevance["k-semantic"] == pytest.approx(1.0 / 1.02, abs=1e-6)


class TestScoreByRankMagnitude:
    """VAL-14: score_by_rank tracks magnitude, not rank position, when given one."""

    def test_very_different_magnitudes_produce_scores_over_0_01_apart(self) -> None:
        retriever = MemoryRetriever()
        now = datetime.now(tz=UTC)
        strong = make_entry(key="strong", confidence=0.5)
        weak = make_entry(key="weak", confidence=0.5)

        score_strong = retriever.score_by_rank(strong, 0, 2, now, relevance_raw=0.95)
        score_weak = retriever.score_by_rank(weak, 1, 2, now, relevance_raw=0.05)

        assert score_strong - score_weak > 0.01

    def test_near_equal_magnitudes_stay_near_equal(self) -> None:
        retriever = MemoryRetriever()
        now = datetime.now(tz=UTC)
        a = make_entry(key="a", confidence=0.5)
        b = make_entry(key="b", confidence=0.5)

        score_a = retriever.score_by_rank(a, 0, 2, now, relevance_raw=0.50)
        score_b = retriever.score_by_rank(b, 1, 2, now, relevance_raw=0.51)

        assert abs(score_a - score_b) < 0.01

    def test_omitting_relevance_raw_preserves_rank_position_formula(self) -> None:
        """Negative control: without a magnitude, scoring is still rank-position."""
        retriever = MemoryRetriever()
        now = datetime.now(tz=UTC)
        top = make_entry(key="top", confidence=0.5)
        bottom = make_entry(key="bottom", confidence=0.5)

        score_top = retriever.score_by_rank(top, 0, 2, now)
        score_bottom = retriever.score_by_rank(bottom, 1, 2, now)

        # Rank-position formula: relevance 1.0 vs 0.0, everything else equal.
        assert score_top > score_bottom


class TestBrainRecallToleratesUnspeccedStoreDouble:
    """TAP-7338 follow-up: an unspecced MagicMock store must not poison scoring.

    ``MagicMock()`` with no ``spec`` auto-vivifies any attribute access,
    including ``last_search_relevance`` — so ``getattr(store,
    "last_search_relevance", None)`` never falls through to ``None``. Before
    the isinstance(..., dict) guard in ``memory_service.brain_recall``, that
    auto-vivified MagicMock was treated as the relevance map, `.get(key)`
    returned another MagicMock, and ``score_by_rank`` raised ``TypeError:
    '<' not supported between instances of 'MagicMock' and 'float'``.
    """

    def test_recall_scores_results_without_raising(self) -> None:
        from tapps_brain.models import MemoryEntry
        from tapps_brain.services.memory_service import brain_recall

        store = MagicMock()
        entry = MemoryEntry(key="key-a", value="some fact")
        store.search.return_value = [entry]

        results = brain_recall(store, "proj", "agent", query="something")

        assert len(results) == 1
        assert isinstance(results[0]["score"], float)
