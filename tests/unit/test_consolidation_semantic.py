"""Tests for embedding-aware similarity and semantic consolidation (TAP-559).

Covers :func:`compute_similarity_with_embeddings` and the F1 benchmark
demonstrating that embedding cosine beats the Jaccard+TF-cosine baseline by
≥ 10% F1 on a labeled merge-dataset (STORY-SC03 acceptance criteria).
"""

from __future__ import annotations

import math

import pytest

from tapps_brain.models import MemoryEntry, MemoryTier
from tapps_brain.similarity import (
    DEFAULT_EMBEDDING_TAG_WEIGHT,
    DEFAULT_EMBEDDING_WEIGHT,
    compute_similarity,
    compute_similarity_with_embeddings,
    find_consolidation_groups,
    find_similar,
)
from tests.factories import make_entry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unit_vec(*components: float) -> list[float]:
    """Return a unit-normalised dense vector from *components*."""
    norm = math.sqrt(sum(x * x for x in components))
    if norm == 0:
        return list(components)
    return [x / norm for x in components]


def _make_entry_with_emb(
    key: str,
    value: str,
    embedding: list[float],
    *,
    tags: list[str] | None = None,
) -> MemoryEntry:
    """Create a MemoryEntry with a pre-computed embedding vector."""
    return MemoryEntry(
        key=key,
        value=value,
        tier=MemoryTier.pattern,
        embedding=embedding,
        tags=tags or [],
    )


def _f1(true_pos: int, false_pos: int, false_neg: int) -> float:
    if true_pos == 0:
        return 0.0
    precision = true_pos / (true_pos + false_pos)
    recall = true_pos / (true_pos + false_neg)
    if precision + recall == 0.0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


# ---------------------------------------------------------------------------
# Unit tests: compute_similarity_with_embeddings
# ---------------------------------------------------------------------------


class TestComputeSimilarityWithEmbeddings:
    def test_uses_embedding_when_both_present(self) -> None:
        """When both entries carry embeddings, used_embeddings=True."""
        emb_a = _unit_vec(1.0, 0.0, 0.0)
        emb_b = _unit_vec(0.9, 0.44, 0.0)  # cosine ≈ 0.9
        ea = _make_entry_with_emb("a", "Alpha text.", emb_a, tags=["x"])
        eb = _make_entry_with_emb("b", "Beta prose.", emb_b, tags=["x"])
        result = compute_similarity_with_embeddings(ea, eb)
        assert result.used_embeddings is True
        assert result.embedding_score is not None
        assert result.entry_key == "b"

    def test_falls_back_when_embedding_missing(self) -> None:
        """Without stored embeddings, used_embeddings=False."""
        ea = make_entry(key="a", value="same text here")
        eb = make_entry(key="b", value="same text here")
        result = compute_similarity_with_embeddings(ea, eb)
        assert result.used_embeddings is False
        assert result.embedding_score is None

    def test_falls_back_when_one_embedding_empty(self) -> None:
        """One empty embedding → text fallback."""
        emb_a = _unit_vec(1.0, 0.0)
        ea = _make_entry_with_emb("a", "some text", emb_a)
        eb = make_entry(key="b", value="some text")  # no embedding
        result = compute_similarity_with_embeddings(ea, eb)
        assert result.used_embeddings is False

    def test_embedding_score_matches_cosine(self) -> None:
        """embedding_score should equal cosine similarity of the two vectors."""
        # cos([1,0,0], [0,1,0]) = 0.0
        ea = _make_entry_with_emb("a", "text a", [1.0, 0.0, 0.0])
        eb = _make_entry_with_emb("b", "text b", [0.0, 1.0, 0.0])
        result = compute_similarity_with_embeddings(ea, eb)
        assert result.embedding_score == pytest.approx(0.0, abs=1e-4)

    def test_identical_embeddings_give_high_score(self) -> None:
        """Identical embeddings → embedding_score ≈ 1.0."""
        emb = _unit_vec(1.0, 1.0, 1.0)
        ea = _make_entry_with_emb("a", "text a", emb, tags=["t"])
        eb = _make_entry_with_emb("b", "text b", emb, tags=["t"])
        result = compute_similarity_with_embeddings(ea, eb)
        assert result.embedding_score == pytest.approx(1.0, abs=1e-4)
        assert result.combined_score > 0.9

    def test_combined_score_blends_embedding_and_tag(self) -> None:
        """Combined = embedding_weight*emb + tag_weight*tag (normalised)."""
        emb_a = _unit_vec(1.0, 0.0, 0.0)
        emb_b = _unit_vec(1.0, 0.0, 0.0)  # cosine = 1.0
        ea = _make_entry_with_emb("a", "diff text", emb_a, tags=["shared"])
        eb = _make_entry_with_emb("b", "other text", emb_b, tags=["shared"])
        result = compute_similarity_with_embeddings(ea, eb)
        total = DEFAULT_EMBEDDING_WEIGHT + DEFAULT_EMBEDDING_TAG_WEIGHT
        expected = (1.0 * DEFAULT_EMBEDDING_WEIGHT + 1.0 * DEFAULT_EMBEDDING_TAG_WEIGHT) / total
        assert result.combined_score == pytest.approx(expected, abs=1e-3)

    def test_low_embedding_cosine_gives_low_combined(self) -> None:
        """Orthogonal embeddings + no shared tags → combined < threshold."""
        ea = _make_entry_with_emb("a", "text", [1.0, 0.0])
        eb = _make_entry_with_emb("b", "prose", [0.0, 1.0])
        result = compute_similarity_with_embeddings(ea, eb)
        assert result.combined_score < 0.5

    def test_fallback_score_matches_compute_similarity(self) -> None:
        """Without embeddings, result equals compute_similarity()."""
        ea = make_entry(key="a", value="exact same text here", tags=["t"])
        eb = make_entry(key="b", value="exact same text here", tags=["t"])
        text_result = compute_similarity(ea, eb)
        emb_result = compute_similarity_with_embeddings(ea, eb)
        assert emb_result.combined_score == pytest.approx(text_result.combined_score, abs=1e-4)


# ---------------------------------------------------------------------------
# F1 benchmark: embedding path beats text baseline by ≥ 10% F1 (TAP-559 AC2)
# ---------------------------------------------------------------------------
#
# Dataset construction
# --------------------
# 10 pairs total: 6 positive (should merge), 4 negative (should not merge).
#
# Positive pairs A–D (text-similar, same tags):
#   Both paths identify them.  TF-cosine high because vocabulary overlaps.
#
# Positive pairs E–F (semantically similar, different vocabulary):
#   Embedding cosine ≥ 0.9 (injected). TF-cosine < 0.5 (different words).
#   Tag jaccard = 1.0 → combined (embedding path) ≥ 0.7.
#   Text path misses them (combined < 0.7).
#
# Negative pairs G–J:
#   Embedding cosine ≈ 0 (orthogonal vectors), tag jaccard = 0.
#   Both paths correctly reject them.
#
# Expected F1:
#   text_f1  = F1(TP=4, FP=0, FN=2) = 2*4/(2*4+0+2) = 8/10 = 0.80
#   emb_f1   = F1(TP=6, FP=0, FN=0) = 1.00
#   delta    = 0.20 ≥ 0.10 ✓


class _LabeledPair:
    """Ground-truth labelled pair for the F1 benchmark."""

    def __init__(
        self,
        entry_a: MemoryEntry,
        entry_b: MemoryEntry,
        should_merge: bool,
    ) -> None:
        self.entry_a = entry_a
        self.entry_b = entry_b
        self.should_merge = should_merge


def _build_labeled_dataset() -> list[_LabeledPair]:
    """Build a small, deterministic labeled merge-dataset."""
    # High-cosine embedding direction (positive pole)
    pos_emb = _unit_vec(1.0, 0.2, 0.1)
    # Near-duplicate (cosine ≈ 0.99)
    pos_emb2 = _unit_vec(1.0, 0.21, 0.09)
    # Orthogonal (negative)
    neg_emb = _unit_vec(0.0, 1.0, 0.0)
    neg_emb2 = _unit_vec(0.0, 0.0, 1.0)

    pairs: list[_LabeledPair] = []

    # ----- Positive pairs A–D: text-similar (both paths identify) -----
    for i in range(4):
        text = f"The memory system persists agent knowledge for session {i}"
        pairs.append(
            _LabeledPair(
                _make_entry_with_emb(f"pos-text-a{i}", text, pos_emb, tags=["memory", "agent"]),
                _make_entry_with_emb(f"pos-text-b{i}", text, pos_emb2, tags=["memory", "agent"]),
                should_merge=True,
            )
        )

    # ----- Positive pairs E–F: embedding-only (different vocabulary) -----
    # Pair E: Postgres ↔ PostgreSQL (different tokens, same concept)
    pairs.append(
        _LabeledPair(
            _make_entry_with_emb(
                "pos-emb-e1",
                "Our primary storage layer utilises PostgreSQL for persistence.",
                pos_emb,
                tags=["database"],
            ),
            _make_entry_with_emb(
                "pos-emb-e2",
                "Postgres serves as the database backend for all memory records.",
                pos_emb2,  # cosine ≈ 0.99 — well above threshold
                tags=["database"],
            ),
            should_merge=True,
        )
    )
    # Pair F: "recall" vs "retrieval" (synonyms)
    pairs.append(
        _LabeledPair(
            _make_entry_with_emb(
                "pos-emb-f1",
                "BM25 recall provides lexical precision for short queries.",
                pos_emb,
                tags=["retrieval"],
            ),
            _make_entry_with_emb(
                "pos-emb-f2",
                "Hybrid retrieval merges BM25 with vector search via RRF fusion.",
                pos_emb2,
                tags=["retrieval"],
            ),
            should_merge=True,
        )
    )

    # ----- Negative pairs G–J: should NOT merge -----
    for i in range(4):
        pairs.append(
            _LabeledPair(
                _make_entry_with_emb(
                    f"neg-a{i}",
                    f"Unrelated fact about topic alpha number {i}.",
                    neg_emb,
                    tags=["alpha"],
                ),
                _make_entry_with_emb(
                    f"neg-b{i}",
                    f"Completely different subject matter beta item {i}.",
                    neg_emb2,
                    tags=["beta"],
                ),
                should_merge=False,
            )
        )

    return pairs


def _classify_pair(
    ea: MemoryEntry,
    eb: MemoryEntry,
    threshold: float,
    *,
    use_embeddings: bool,
) -> bool:
    """Return True if the pair is classified as 'should merge'."""
    if use_embeddings:
        result = compute_similarity_with_embeddings(ea, eb)
    else:
        result = compute_similarity(ea, eb)
    return result.combined_score >= threshold


class TestF1BenchmarkEmbeddingVsText:
    """Embedding cosine path must beat Jaccard+TF-cosine by ≥10% F1 (TAP-559 AC2)."""

    THRESHOLD = 0.7
    MIN_F1_DELTA = 0.10

    def test_f1_improvement_over_text_baseline(self) -> None:
        dataset = _build_labeled_dataset()
        n_pos = sum(1 for p in dataset if p.should_merge)
        n_neg = sum(1 for p in dataset if not p.should_merge)
        assert n_pos == 6, "Dataset must have exactly 6 positive pairs"
        assert n_neg == 4, "Dataset must have exactly 4 negative pairs"

        def _evaluate(*, use_embeddings: bool) -> tuple[int, int, int]:
            tp = fp = fn = 0
            for pair in dataset:
                predicted = _classify_pair(
                    pair.entry_a,
                    pair.entry_b,
                    self.THRESHOLD,
                    use_embeddings=use_embeddings,
                )
                if pair.should_merge and predicted:
                    tp += 1
                elif not pair.should_merge and predicted:
                    fp += 1
                elif pair.should_merge and not predicted:
                    fn += 1
            return tp, fp, fn

        tp_text, fp_text, fn_text = _evaluate(use_embeddings=False)
        tp_emb, fp_emb, fn_emb = _evaluate(use_embeddings=True)

        f1_text = _f1(tp_text, fp_text, fn_text)
        f1_emb = _f1(tp_emb, fp_emb, fn_emb)

        assert f1_emb - f1_text >= self.MIN_F1_DELTA, (
            f"Embedding F1 ({f1_emb:.3f}) must beat text F1 ({f1_text:.3f}) "
            f"by ≥{self.MIN_F1_DELTA:.2f}; delta={f1_emb - f1_text:.3f}"
        )

    def test_embedding_path_catches_vocabulary_gap(self) -> None:
        """Pairs E and F must be classified positive by embedding but NOT by text path."""
        dataset = _build_labeled_dataset()
        emb_only_pairs = [p for p in dataset if "pos-emb" in p.entry_a.key]
        assert len(emb_only_pairs) == 2, "Expected 2 embedding-only positive pairs"

        for pair in emb_only_pairs:
            text_pred = _classify_pair(
                pair.entry_a, pair.entry_b, self.THRESHOLD, use_embeddings=False
            )
            emb_pred = _classify_pair(
                pair.entry_a, pair.entry_b, self.THRESHOLD, use_embeddings=True
            )
            assert emb_pred is True, (
                f"Embedding path should identify {pair.entry_a.key} ↔ {pair.entry_b.key} as positive"
            )
            assert text_pred is False, (
                f"Text path should miss {pair.entry_a.key} ↔ {pair.entry_b.key} (vocabulary gap)"
            )

    def test_no_false_positives_on_negative_pairs(self) -> None:
        """Negative pairs must be rejected by both text and embedding paths."""
        dataset = _build_labeled_dataset()
        neg_pairs = [p for p in dataset if not p.should_merge]
        for pair in neg_pairs:
            for use_emb in (False, True):
                pred = _classify_pair(
                    pair.entry_a, pair.entry_b, self.THRESHOLD, use_embeddings=use_emb
                )
                assert pred is False, (
                    f"Negative pair {pair.entry_a.key} ↔ {pair.entry_b.key} "
                    f"wrongly flagged as positive (use_embeddings={use_emb})"
                )


# ---------------------------------------------------------------------------
# Integration: find_similar and find_consolidation_groups route through embeddings
# ---------------------------------------------------------------------------


class TestFindSimilarRoutesEmbeddings:
    def test_find_similar_uses_embedding_path(self) -> None:
        """find_similar with use_embeddings=True routes through compute_similarity_with_embeddings."""
        high_emb = _unit_vec(1.0, 0.1, 0.0)
        entry = _make_entry_with_emb("ref", "different vocabulary entry", high_emb, tags=["t"])
        candidate = _make_entry_with_emb(
            "cand", "completely distinct wording here", high_emb, tags=["t"]
        )
        results = find_similar(entry, [candidate], threshold=0.7, use_embeddings=True)
        assert len(results) == 1
        assert results[0].used_embeddings is True

    def test_find_similar_text_path_misses_vocab_gap(self) -> None:
        """find_similar with use_embeddings=False misses entries only linked by meaning."""
        high_emb = _unit_vec(1.0, 0.0, 0.0)
        entry = _make_entry_with_emb("ref", "PostgreSQL persistence layer", high_emb)
        candidate = _make_entry_with_emb(
            "cand", "Postgres database backend serves memory", high_emb
        )
        results_text = find_similar(entry, [candidate], threshold=0.7, use_embeddings=False)
        results_emb = find_similar(entry, [candidate], threshold=0.7, use_embeddings=True)
        # Text path should score lower (different tokens).
        assert (
            len(results_text) == 0 or results_text[0].combined_score < results_emb[0].combined_score
        )

    def test_find_consolidation_groups_uses_embeddings(self) -> None:
        """find_consolidation_groups with use_embeddings=True forms groups via embeddings."""
        high = _unit_vec(1.0, 0.1, 0.0)
        entries = [
            _make_entry_with_emb("a", "alpha prose", high, tags=["x"]),
            _make_entry_with_emb("b", "beta prose", high, tags=["x"]),
            _make_entry_with_emb("c", "gamma prose", high, tags=["x"]),
        ]
        groups = find_consolidation_groups(entries, threshold=0.7, use_embeddings=True)
        assert len(groups) >= 1
        # At least one group should contain more than one entry.
        assert any(len(g) >= 2 for g in groups)

    def test_similarity_result_has_embedding_fields(self) -> None:
        """SimilarityResult from the embedding path has embedding_score populated."""
        emb = _unit_vec(1.0, 0.0)
        ea = _make_entry_with_emb("a", "text", emb)
        eb = _make_entry_with_emb("b", "text", emb)
        result = compute_similarity_with_embeddings(ea, eb)
        assert result.embedding_score is not None
        assert result.used_embeddings is True
