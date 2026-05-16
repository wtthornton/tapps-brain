"""Dedicated unit tests for similarity.py — TAP-1821.

Covers hand-computed TF vector assertions, _extract_text normalisation,
tokeniser boundary conditions for text_similarity, find_similar threshold
boundaries (0.0, 1.0), same_topic_score overlap boundary (≥50% gate), and
the use_embeddings=False path in find_consolidation_groups.

Complements tests/unit/test_memory_similarity.py (high-level fixture tests)
and tests/unit/test_consolidation_semantic.py (embedding-path tests).
"""

from __future__ import annotations

import math

import pytest

from tapps_brain.models import MemoryEntry, MemoryTier
from tapps_brain.similarity import (
    _extract_text,
    _term_frequency,
    cosine_similarity,
    find_consolidation_groups,
    find_similar,
    jaccard_similarity,
    same_topic_score,
    text_similarity,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entry(
    key: str,
    value: str,
    *,
    tags: list[str] | None = None,
    tier: MemoryTier = MemoryTier.pattern,
) -> MemoryEntry:
    """Minimal MemoryEntry factory for concise test setup."""
    return MemoryEntry(key=key, value=value, tier=tier, tags=tags or [])


# ---------------------------------------------------------------------------
# Hand-computed _term_frequency tests
# ---------------------------------------------------------------------------


class TestTermFrequencyHandComputed:
    """Verify _term_frequency returns exact, hand-computed fractions."""

    def test_single_unique_term(self) -> None:
        """Single unique term has TF = 1.0 (1/1)."""
        assert _term_frequency(["alpha"]) == {"alpha": pytest.approx(1.0)}

    def test_two_identical_terms(self) -> None:
        """Two identical tokens → TF = 1.0 (2/2)."""
        tf = _term_frequency(["alpha", "alpha"])
        assert tf == {"alpha": pytest.approx(1.0)}

    def test_three_tokens_two_unique(self) -> None:
        """Three tokens: 2×alpha, 1×beta.

        TF(alpha) = 2/3, TF(beta) = 1/3.
        """
        tf = _term_frequency(["alpha", "alpha", "beta"])
        assert tf["alpha"] == pytest.approx(2 / 3)
        assert tf["beta"] == pytest.approx(1 / 3)

    def test_four_unique_tokens_equal_weight(self) -> None:
        """Four unique tokens each have TF = 0.25 (1/4)."""
        tf = _term_frequency(["p", "q", "r", "s"])
        for term in ("p", "q", "r", "s"):
            assert tf[term] == pytest.approx(0.25)

    def test_term_order_invariant(self) -> None:
        """Term order does not affect the output dict."""
        tf1 = _term_frequency(["alpha", "beta", "alpha"])
        tf2 = _term_frequency(["beta", "alpha", "alpha"])
        assert tf1.keys() == tf2.keys()
        for k in tf1:
            assert tf1[k] == pytest.approx(tf2[k])

    def test_tf_values_sum_to_one(self) -> None:
        """All TF values for a non-empty list sum to 1.0."""
        terms = ["alpha", "beta", "gamma", "alpha", "delta"]
        tf = _term_frequency(terms)
        assert sum(tf.values()) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Hand-computed cosine_similarity tests
# ---------------------------------------------------------------------------


class TestCosineSimilarityHandComputed:
    """Verify cosine_similarity against manually computed expected values.

    Formula: cos(u, v) = dot(u, v) / (|u| · |v|)
    """

    def test_exactly_40_percent_overlap(self) -> None:
        """Two 2-component vectors sharing one term give cos = 0.4.

        A = {alpha: 2/3, beta: 1/3}
        B = {alpha: 1/3, gamma: 2/3}
        dot  = (2/3)·(1/3) = 2/9
        |A|  = sqrt((2/3)² + (1/3)²) = sqrt(5)/3
        |B|  = sqrt((1/3)² + (2/3)²) = sqrt(5)/3
        cos  = (2/9) / (5/9) = 2/5 = 0.4
        """
        vec_a = {"alpha": 2 / 3, "beta": 1 / 3}
        vec_b = {"alpha": 1 / 3, "gamma": 2 / 3}
        result = cosine_similarity(vec_a, vec_b)
        assert result == pytest.approx(0.4, abs=1e-9)

    def test_parallel_unit_vectors_give_one(self) -> None:
        """Unit vectors pointing in the same direction give cos = 1.0."""
        s = 1.0 / math.sqrt(2)
        vec = {"x": s, "y": s}
        assert cosine_similarity(vec, vec) == pytest.approx(1.0)

    def test_perpendicular_vectors_give_zero(self) -> None:
        """Perpendicular vectors give cos = 0.0."""
        vec_a = {"x": 1.0, "y": 0.0}
        vec_b = {"x": 0.0, "y": 1.0}
        assert cosine_similarity(vec_a, vec_b) == pytest.approx(0.0)

    def test_3d_partial_overlap(self) -> None:
        """Three-term vectors with partial overlap — hand-verified.

        A = {a: 0.5, b: 0.5}
        B = {a: 0.5, c: 0.5}
        dot  = 0.5·0.5 = 0.25
        |A|  = sqrt(0.25 + 0.25) = sqrt(0.5)
        |B|  = sqrt(0.25 + 0.25) = sqrt(0.5)
        cos  = 0.25 / 0.5 = 0.5
        """
        vec_a = {"a": 0.5, "b": 0.5}
        vec_b = {"a": 0.5, "c": 0.5}
        assert cosine_similarity(vec_a, vec_b) == pytest.approx(0.5, abs=1e-9)

    def test_one_empty_vector(self) -> None:
        """One empty vector gives cos = 0.0 regardless of the other."""
        assert cosine_similarity({}, {"x": 1.0}) == 0.0
        assert cosine_similarity({"x": 1.0}, {}) == 0.0


# ---------------------------------------------------------------------------
# Jaccard similarity — additional boundary cases
# ---------------------------------------------------------------------------


class TestJaccardSimilarityBoundaries:
    """Edge cases not covered by test_memory_similarity.py."""

    def test_singleton_same_element(self) -> None:
        """Two singleton sets with the same element → 1.0."""
        assert jaccard_similarity({"alpha"}, {"alpha"}) == 1.0

    def test_singleton_different_elements(self) -> None:
        """Two singleton sets with different elements → 0.0."""
        assert jaccard_similarity({"alpha"}, {"beta"}) == 0.0

    def test_large_identical_sets(self) -> None:
        """Large identical sets → 1.0."""
        big = set("abcdefghijklmnopqrstuvwxyz")
        assert jaccard_similarity(big, big) == 1.0

    def test_one_shared_element_among_many(self) -> None:
        """One element in common out of 4+4 → 1/7 ≈ 0.1429."""
        set_a = {"x", "a", "b", "c"}
        set_b = {"x", "d", "e", "f"}
        # intersection={x}=1, union=7
        assert jaccard_similarity(set_a, set_b) == pytest.approx(1 / 7)


# ---------------------------------------------------------------------------
# _extract_text — key normalisation
# ---------------------------------------------------------------------------


class TestExtractText:
    """Verify _extract_text substitutes key separators and appends value."""

    def test_hyphens_replaced_by_spaces(self) -> None:
        """Hyphens in key are replaced by spaces."""
        entry = _entry("auth-jwt-config", "some value")
        text = _extract_text(entry)
        assert "auth jwt config" in text
        assert "-" not in text.split("some value")[0]

    def test_underscores_replaced_by_spaces(self) -> None:
        """Underscores in key are replaced by spaces."""
        entry = _entry("auth_jwt_config", "some value")
        text = _extract_text(entry)
        assert "auth jwt config" in text
        assert "_" not in text.split("some value")[0]

    def test_dots_replaced_by_spaces(self) -> None:
        """Dots in key are replaced by spaces."""
        entry = _entry("auth.jwt.config", "some value")
        text = _extract_text(entry)
        assert "auth jwt config" in text
        assert "." not in text.split("some value")[0]

    def test_value_appended_after_key(self) -> None:
        """Value text is present after the normalised key."""
        entry = _entry("my-key", "distinct value content here")
        text = _extract_text(entry)
        assert "distinct value content here" in text
        # key portion appears before value in the joined output
        key_pos = text.index("my key")
        val_pos = text.index("distinct value content here")
        assert key_pos < val_pos

    def test_empty_value(self) -> None:
        """Empty value produces output containing only key text."""
        entry = _entry("some-key", "")
        text = _extract_text(entry)
        assert "some key" in text


# ---------------------------------------------------------------------------
# text_similarity — tokeniser boundary conditions
# ---------------------------------------------------------------------------


class TestTextSimilarityBoundary:
    """Edge cases for text_similarity (short inputs, stop-word-only, empty)."""

    def test_identical_key_empty_value_gives_one(self) -> None:
        """Identical key with empty value: TF vectors match → similarity 1.0."""
        entry_a = _entry("auth-jwt", "")
        entry_b = _entry("auth-jwt", "")
        assert text_similarity(entry_a, entry_b) == pytest.approx(1.0)

    def test_identical_entries_give_one(self) -> None:
        """Entry compared against itself: similarity = 1.0."""
        entry = _entry("memory-key", "some important fact about configuration")
        assert text_similarity(entry, entry) == pytest.approx(1.0)

    def test_completely_disjoint_token_sets_give_zero(self) -> None:
        """Entries with no shared tokens after tokenisation → similarity 0.0.

        Uses clearly non-stop-word, non-overlapping tokens so the test
        exercises the orthogonal-vector path of cosine_similarity without
        relying on stop-word stripping behaviour.
        """
        # TF(a) ≈ {"elephant": 0.5, "ocean": 0.5}
        # TF(b) ≈ {"volcano": 0.5, "castle": 0.5}
        # No shared terms → cosine = 0.0
        entry_a = _entry("elephant", "ocean")
        entry_b = _entry("volcano", "castle")
        assert text_similarity(entry_a, entry_b) == pytest.approx(0.0)

    def test_shared_significant_token_gives_positive_score(self) -> None:
        """Two entries sharing exactly one significant token → score > 0."""
        entry_a = _entry("unique-term-alpha", "nothing else here")
        entry_b = _entry("another-unique-alpha", "different content altogether")
        # Both share "alpha" (not a stop word, not stemmed away)
        score = text_similarity(entry_a, entry_b)
        assert score > 0.0

    def test_stop_words_only_entries_give_zero(self) -> None:
        """Entries whose entire text consists of stop words → similarity 0.0.

        preprocess_similarity strips all stop words → empty TF vectors
        → cosine_similarity({}, {}) = 0.0.
        """
        # "a", "the" are stop words; "is", "of" are stop words
        entry_a = _entry("a-the", "is of")
        entry_b = _entry("the-a", "of is")
        assert text_similarity(entry_a, entry_b) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# find_similar — explicit threshold=0.0 and threshold=1.0
# ---------------------------------------------------------------------------


class TestFindSimilarThresholdBoundaries:
    """Explicit boundary tests for threshold=0.0 and threshold=1.0."""

    @pytest.fixture()
    def three_dissimilar_candidates(self) -> list[MemoryEntry]:
        """Three candidates with tags and values unlike any reference entry."""
        return [
            _entry("cand-a", "completely unrelated topic one", tags=["ui"]),
            _entry("cand-b", "database pooling configuration", tags=["db"]),
            _entry("cand-c", "logging and metrics setup", tags=["logging"]),
        ]

    def test_threshold_zero_returns_all_candidates(
        self, three_dissimilar_candidates: list[MemoryEntry]
    ) -> None:
        """threshold=0.0 returns all candidates (combined_score ≥ 0 is always true)."""
        ref = _entry("ref-entry", "reference note", tags=["ref"])
        results = find_similar(
            ref,
            three_dissimilar_candidates,
            threshold=0.0,
            use_embeddings=False,
        )
        assert len(results) == 3

    def test_threshold_zero_results_sorted_descending(
        self, three_dissimilar_candidates: list[MemoryEntry]
    ) -> None:
        """Even at threshold=0.0, results are sorted by combined_score descending."""
        ref = _entry("ref-entry", "reference note", tags=["ref"])
        results = find_similar(
            ref,
            three_dissimilar_candidates,
            threshold=0.0,
            use_embeddings=False,
        )
        scores = [r.combined_score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_threshold_one_returns_only_perfect_match(self) -> None:
        """threshold=1.0 with exclude_self=False: only an identical entry scores 1.0."""
        ref = _entry(
            "ref-entry",
            "specific jwt authentication value",
            tags=["jwt", "auth"],
        )
        identical = _entry(
            "ref-entry",
            "specific jwt authentication value",
            tags=["jwt", "auth"],
        )
        unrelated = _entry("db-pool", "database connection pooling", tags=["db"])
        results = find_similar(
            ref,
            [identical, unrelated],
            threshold=1.0,
            exclude_self=False,
            use_embeddings=False,
        )
        keys = {r.entry_key for r in results}
        assert "ref-entry" in keys
        assert "db-pool" not in keys

    def test_threshold_one_with_exclude_self_no_results(self) -> None:
        """threshold=1.0 with exclude_self=True: identical-key candidate is skipped."""
        ref = _entry(
            "ref-entry",
            "specific jwt authentication value",
            tags=["jwt", "auth"],
        )
        identical = _entry(
            "ref-entry",
            "specific jwt authentication value",
            tags=["jwt", "auth"],
        )
        results = find_similar(
            ref,
            [identical],
            threshold=1.0,
            exclude_self=True,
            use_embeddings=False,
        )
        assert len(results) == 0

    def test_use_embeddings_false_forces_jaccard_plus_tf_path(self) -> None:
        """use_embeddings=False uses compute_similarity (not compute_similarity_with_embeddings)."""
        ref = _entry("jwt-token-config", "JWT RS256 signing setup", tags=["jwt", "auth"])
        similar = _entry("jwt-auth-setup", "JWT RS256 algorithm configuration", tags=["jwt", "auth"])
        # Both paths should still find similarity; verify the flag doesn't break results
        results_emb_off = find_similar(
            ref, [similar], threshold=0.0, use_embeddings=False
        )
        assert len(results_emb_off) == 1
        # Score should be > 0 (shared tags + text overlap)
        assert results_emb_off[0].combined_score > 0.0
        # used_embeddings must be False on the Jaccard+TF path
        assert results_emb_off[0].used_embeddings is False


# ---------------------------------------------------------------------------
# same_topic_score — overlap boundary (≥50%)
# ---------------------------------------------------------------------------


class TestSameTopicScoreOverlapBoundary:
    """Verify the ≥50% tag-overlap gate in same_topic_score."""

    def test_exactly_50_percent_overlap_inclusive(self) -> None:
        """2-element sets with 1 overlap (50%) → 1.0 (boundary is inclusive)."""
        entry_a = _entry("a", "v", tier=MemoryTier.pattern, tags=["x", "y"])
        entry_b = _entry("b", "v", tier=MemoryTier.pattern, tags=["x", "z"])
        # smaller=2, overlap=1, 1/2 = 0.5 ≥ 0.5 → 1.0
        assert same_topic_score(entry_a, entry_b) == 1.0

    def test_below_50_percent_overlap_returns_zero(self) -> None:
        """3-element sets with 1 overlap (33%) → 0.0 (below boundary)."""
        entry_a = _entry("a", "v", tier=MemoryTier.pattern, tags=["x", "y", "z"])
        entry_b = _entry("b", "v", tier=MemoryTier.pattern, tags=["x", "p", "q"])
        # smaller=3, overlap=1, 1/3 < 0.5 → 0.0
        assert same_topic_score(entry_a, entry_b) == 0.0

    def test_above_50_percent_overlap_returns_one(self) -> None:
        """3-element sets with 2 overlap (67%) → 1.0 (above boundary)."""
        entry_a = _entry("a", "v", tier=MemoryTier.pattern, tags=["x", "y", "z"])
        entry_b = _entry("b", "v", tier=MemoryTier.pattern, tags=["x", "y", "w"])
        # smaller=3, overlap=2, 2/3 ≥ 0.5 → 1.0
        assert same_topic_score(entry_a, entry_b) == 1.0

    def test_asymmetric_tag_sizes_uses_smaller_as_base(self) -> None:
        """Smaller set fully overlapped: smaller=1, overlap=1 → 1.0."""
        entry_a = _entry("a", "v", tier=MemoryTier.pattern, tags=["x"])
        entry_b = _entry("b", "v", tier=MemoryTier.pattern, tags=["x", "y", "z", "w"])
        # smaller=1, overlap=1, 1/1 = 1.0 ≥ 0.5 → 1.0
        assert same_topic_score(entry_a, entry_b) == 1.0

    def test_different_tier_short_circuits(self) -> None:
        """Different tiers → 0.0 regardless of tag overlap."""
        entry_a = _entry("a", "v", tier=MemoryTier.architectural, tags=["x", "y"])
        entry_b = _entry("b", "v", tier=MemoryTier.pattern, tags=["x", "y"])
        assert same_topic_score(entry_a, entry_b) == 0.0


# ---------------------------------------------------------------------------
# find_consolidation_groups — use_embeddings=False explicit path
# ---------------------------------------------------------------------------


class TestFindConsolidationGroupsFallback:
    """Verify find_consolidation_groups with use_embeddings=False."""

    def test_groups_formed_on_jaccard_tf_path(self) -> None:
        """Similar entries are grouped even without embeddings."""
        entries = [
            _entry("jwt-config-a", "JWT RS256 signing configuration", tags=["jwt", "auth"]),
            _entry("jwt-config-b", "JWT token RS256 setup", tags=["jwt", "auth"]),
            _entry("db-pool", "database connection pool settings", tags=["db", "postgres"]),
        ]
        groups = find_consolidation_groups(
            entries,
            threshold=0.4,
            min_group_size=2,
            use_embeddings=False,
        )
        assert len(groups) >= 1
        all_keys: set[str] = {k for group in groups for k in group}
        # At least one JWT entry must be in a group
        assert "jwt-config-a" in all_keys or "jwt-config-b" in all_keys

    def test_too_few_entries_returns_empty(self) -> None:
        """Fewer entries than min_group_size (default 2) returns empty list."""
        groups = find_consolidation_groups(
            [_entry("only", "value", tags=["tag"])],
            use_embeddings=False,
        )
        assert groups == []

    def test_high_threshold_yields_no_groups(self) -> None:
        """Threshold=0.99 for dissimilar entries → no groups."""
        entries = [
            _entry("alpha-entry", "completely different topic", tags=["alpha"]),
            _entry("beta-entry", "unrelated database configuration", tags=["beta"]),
        ]
        groups = find_consolidation_groups(
            entries,
            threshold=0.99,
            use_embeddings=False,
        )
        assert groups == []

    def test_all_assigned_at_most_once(self) -> None:
        """Each entry key appears in at most one group (greedy assignment)."""
        entries = [
            _entry("jwt-a", "JWT RS256 token signing config", tags=["jwt"]),
            _entry("jwt-b", "JWT RS256 signing token setup", tags=["jwt"]),
            _entry("jwt-c", "RS256 JWT token configuration auth", tags=["jwt"]),
        ]
        groups = find_consolidation_groups(
            entries,
            threshold=0.3,
            min_group_size=2,
            use_embeddings=False,
        )
        all_keys = [k for group in groups for k in group]
        # No key should appear more than once across all groups
        assert len(all_keys) == len(set(all_keys))
