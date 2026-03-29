"""Tests for memory similarity detection (Epic 58, Story 58.1)."""

from __future__ import annotations

import pytest

from tapps_brain.models import MemoryEntry, MemoryTier
from tapps_brain.similarity import (
    DEFAULT_SIMILARITY_THRESHOLD,
    DEFAULT_TAG_WEIGHT,
    DEFAULT_TEXT_WEIGHT,
    SimilarityResult,
    _term_frequency,
    compute_similarity,
    cosine_similarity,
    find_consolidation_groups,
    find_similar,
    is_same_topic,
    jaccard_similarity,
    same_topic_score,
    tag_similarity,
    text_similarity,
)

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def security_entry() -> MemoryEntry:
    """A security-related memory entry."""
    return MemoryEntry(
        key="auth-jwt-config",
        value="Use RS256 for JWT signing. Store keys in environment variables.",
        tier=MemoryTier.architectural,
        tags=["security", "jwt", "authentication"],
    )


@pytest.fixture
def similar_security_entry() -> MemoryEntry:
    """Another security-related entry similar to security_entry."""
    return MemoryEntry(
        key="auth-jwt-tokens",
        value="JWT tokens should use RS256 algorithm. Refresh tokens expire in 7 days.",
        tier=MemoryTier.architectural,
        tags=["security", "jwt", "tokens"],
    )


@pytest.fixture
def database_entry() -> MemoryEntry:
    """A database-related memory entry."""
    return MemoryEntry(
        key="db-connection-pool",
        value="Use connection pooling with max 20 connections.",
        tier=MemoryTier.pattern,
        tags=["database", "postgres", "performance"],
    )


@pytest.fixture
def unrelated_entry() -> MemoryEntry:
    """An entry unrelated to security."""
    return MemoryEntry(
        key="ui-color-scheme",
        value="Use dark mode as default. Primary color is blue.",
        tier=MemoryTier.context,
        tags=["ui", "design", "colors"],
    )


# ---------------------------------------------------------------------------
# Jaccard similarity tests
# ---------------------------------------------------------------------------


class TestJaccardSimilarity:
    """Tests for jaccard_similarity function."""

    def test_identical_sets(self) -> None:
        """Identical sets have similarity 1.0."""
        set_a = {"a", "b", "c"}
        set_b = {"a", "b", "c"}
        assert jaccard_similarity(set_a, set_b) == 1.0

    def test_empty_sets(self) -> None:
        """Empty sets have similarity 0.0."""
        assert jaccard_similarity(set(), set()) == 0.0

    def test_one_empty_set(self) -> None:
        """One empty set gives similarity 0.0."""
        assert jaccard_similarity({"a", "b"}, set()) == 0.0
        assert jaccard_similarity(set(), {"a", "b"}) == 0.0

    def test_no_overlap(self) -> None:
        """Disjoint sets have similarity 0.0."""
        set_a = {"a", "b"}
        set_b = {"c", "d"}
        assert jaccard_similarity(set_a, set_b) == 0.0

    def test_partial_overlap(self) -> None:
        """Partial overlap gives expected similarity."""
        set_a = {"a", "b", "c"}
        set_b = {"b", "c", "d"}
        # Intersection: {b, c} = 2, Union: {a, b, c, d} = 4
        assert jaccard_similarity(set_a, set_b) == 0.5

    def test_subset(self) -> None:
        """Subset relationship."""
        set_a = {"a", "b"}
        set_b = {"a", "b", "c", "d"}
        # Intersection: {a, b} = 2, Union: {a, b, c, d} = 4
        assert jaccard_similarity(set_a, set_b) == 0.5


class TestTagSimilarity:
    """Tests for tag_similarity function."""

    def test_identical_tags(self, security_entry: MemoryEntry) -> None:
        """Entry compared to itself has tag similarity 1.0."""
        assert tag_similarity(security_entry, security_entry) == 1.0

    def test_overlapping_tags(
        self, security_entry: MemoryEntry, similar_security_entry: MemoryEntry
    ) -> None:
        """Entries with overlapping tags have positive similarity."""
        score = tag_similarity(security_entry, similar_security_entry)
        # Tags: {security, jwt, authentication} vs {security, jwt, tokens}
        # Intersection: {security, jwt} = 2, Union: {security, jwt, auth, tokens} = 4
        assert score == 0.5

    def test_no_tag_overlap(
        self, security_entry: MemoryEntry, unrelated_entry: MemoryEntry
    ) -> None:
        """Entries with no tag overlap have similarity 0.0."""
        assert tag_similarity(security_entry, unrelated_entry) == 0.0

    def test_case_insensitive(self) -> None:
        """Tag comparison is case-insensitive."""
        entry_a = MemoryEntry(key="test-a", value="test", tags=["Security", "JWT"])
        entry_b = MemoryEntry(key="test-b", value="test", tags=["security", "jwt"])
        assert tag_similarity(entry_a, entry_b) == 1.0


# ---------------------------------------------------------------------------
# Text similarity tests
# ---------------------------------------------------------------------------


class TestTextSimilarity:
    """Tests for text_similarity function."""

    def test_identical_text(self, security_entry: MemoryEntry) -> None:
        """Entry compared to itself has text similarity ~1.0."""
        assert text_similarity(security_entry, security_entry) == pytest.approx(1.0)

    def test_similar_text(
        self, security_entry: MemoryEntry, similar_security_entry: MemoryEntry
    ) -> None:
        """Entries with similar text have positive similarity."""
        score = text_similarity(security_entry, similar_security_entry)
        # Both contain: JWT, RS256, tokens
        assert score > 0.3  # Some overlap expected

    def test_unrelated_text(
        self, security_entry: MemoryEntry, unrelated_entry: MemoryEntry
    ) -> None:
        """Entries with unrelated text have low similarity."""
        score = text_similarity(security_entry, unrelated_entry)
        assert score < 0.2  # Very little or no overlap

    def test_includes_key_text(self) -> None:
        """Key text is included in similarity calculation."""
        entry_a = MemoryEntry(key="jwt-auth-config", value="Configuration")
        entry_b = MemoryEntry(key="jwt-auth-setup", value="Setup")
        # Keys share jwt-auth, should have some similarity
        score = text_similarity(entry_a, entry_b)
        assert score > 0.0


class TestCosineSimilarity:
    """Tests for cosine_similarity function."""

    def test_identical_vectors(self) -> None:
        """Identical vectors have similarity ~1.0."""
        vec = {"a": 0.5, "b": 0.5}
        assert cosine_similarity(vec, vec) == pytest.approx(1.0)

    def test_empty_vectors(self) -> None:
        """Empty vectors have similarity 0.0."""
        assert cosine_similarity({}, {}) == 0.0
        assert cosine_similarity({"a": 1.0}, {}) == 0.0

    def test_orthogonal_vectors(self) -> None:
        """Orthogonal vectors have similarity 0.0."""
        vec_a = {"a": 1.0}
        vec_b = {"b": 1.0}
        assert cosine_similarity(vec_a, vec_b) == 0.0

    def test_zero_magnitude_returns_zero(self) -> None:
        """Vectors with zero norm yield 0.0 (guard on division)."""
        assert cosine_similarity({"a": 0.0}, {"a": 1.0}) == 0.0


def test_term_frequency_empty_terms() -> None:
    assert _term_frequency([]) == {}


# ---------------------------------------------------------------------------
# Combined similarity tests
# ---------------------------------------------------------------------------


class TestComputeSimilarity:
    """Tests for compute_similarity function."""

    def test_returns_similarity_result(
        self, security_entry: MemoryEntry, similar_security_entry: MemoryEntry
    ) -> None:
        """Returns a SimilarityResult with all components."""
        result = compute_similarity(security_entry, similar_security_entry)
        assert isinstance(result, SimilarityResult)
        assert result.entry_key == similar_security_entry.key
        assert 0.0 <= result.combined_score <= 1.0
        assert 0.0 <= result.tag_score <= 1.0
        assert 0.0 <= result.text_score <= 1.0

    def test_default_weights(
        self, security_entry: MemoryEntry, similar_security_entry: MemoryEntry
    ) -> None:
        """Default weights are DEFAULT_TAG_WEIGHT tag, DEFAULT_TEXT_WEIGHT text."""
        result = compute_similarity(security_entry, similar_security_entry)
        expected = (result.tag_score * DEFAULT_TAG_WEIGHT) + (
            result.text_score * DEFAULT_TEXT_WEIGHT
        )
        assert abs(result.combined_score - expected) < 0.01

    def test_custom_weights(
        self, security_entry: MemoryEntry, similar_security_entry: MemoryEntry
    ) -> None:
        """Custom weights are applied correctly."""
        result = compute_similarity(
            security_entry, similar_security_entry, tag_weight=0.8, text_weight=0.2
        )
        expected = (result.tag_score * 0.8) + (result.text_score * 0.2)
        assert abs(result.combined_score - expected) < 0.01

    def test_zero_total_weights_use_equal_split(
        self, security_entry: MemoryEntry, similar_security_entry: MemoryEntry
    ) -> None:
        """When tag_weight + text_weight == 0, fall back to 0.5 / 0.5."""
        result = compute_similarity(
            security_entry, similar_security_entry, tag_weight=0.0, text_weight=0.0
        )
        expected = (result.tag_score * 0.5) + (result.text_score * 0.5)
        assert abs(result.combined_score - expected) < 0.01

    def test_similarity_result_sorting(self) -> None:
        """SimilarityResult sorts by combined_score descending."""
        results = [
            SimilarityResult("a", 0.5, 0.4, 0.6),
            SimilarityResult("b", 0.9, 0.8, 0.95),
            SimilarityResult("c", 0.7, 0.6, 0.8),
        ]
        sorted_results = sorted(results)
        assert [r.entry_key for r in sorted_results] == ["b", "c", "a"]


# ---------------------------------------------------------------------------
# find_similar tests
# ---------------------------------------------------------------------------


class TestFindSimilar:
    """Tests for find_similar function."""

    def test_finds_similar_entries(
        self,
        security_entry: MemoryEntry,
        similar_security_entry: MemoryEntry,
        database_entry: MemoryEntry,
        unrelated_entry: MemoryEntry,
    ) -> None:
        """Finds entries above threshold."""
        candidates = [similar_security_entry, database_entry, unrelated_entry]
        results = find_similar(security_entry, candidates, threshold=0.3)
        # similar_security_entry should be found; others unlikely
        assert len(results) >= 1
        assert any(r.entry_key == similar_security_entry.key for r in results)

    def test_excludes_self(self, security_entry: MemoryEntry) -> None:
        """Excludes the entry itself from results."""
        results = find_similar(security_entry, [security_entry], threshold=0.0, exclude_self=True)
        assert len(results) == 0

    def test_includes_self_when_disabled(self, security_entry: MemoryEntry) -> None:
        """Includes self when exclude_self=False."""
        results = find_similar(security_entry, [security_entry], threshold=0.0, exclude_self=False)
        assert len(results) == 1

    def test_empty_candidates(self, security_entry: MemoryEntry) -> None:
        """Returns empty list for empty candidates."""
        results = find_similar(security_entry, [], threshold=0.0)
        assert results == []

    def test_threshold_filtering(
        self,
        security_entry: MemoryEntry,
        similar_security_entry: MemoryEntry,
        unrelated_entry: MemoryEntry,
    ) -> None:
        """High threshold filters out lower similarity entries."""
        candidates = [similar_security_entry, unrelated_entry]
        # Very high threshold should exclude most entries
        results = find_similar(security_entry, candidates, threshold=0.95)
        assert len(results) == 0

    def test_results_sorted_by_score(
        self, security_entry: MemoryEntry, similar_security_entry: MemoryEntry
    ) -> None:
        """Results are sorted by combined score descending."""
        # Create entries with varying similarity
        entry_high = MemoryEntry(
            key="auth-jwt-security",
            value="JWT RS256 authentication security",
            tags=["security", "jwt", "authentication"],
        )
        entry_low = MemoryEntry(
            key="misc-security",
            value="Some security note",
            tags=["security"],
        )
        results = find_similar(security_entry, [entry_low, entry_high], threshold=0.1)
        assert len(results) >= 2, (
            f"Expected at least 2 results above threshold=0.1, got {len(results)}: {results}"
        )
        assert results[0].combined_score >= results[1].combined_score


# ---------------------------------------------------------------------------
# Consolidation group tests
# ---------------------------------------------------------------------------


class TestFindConsolidationGroups:
    """Tests for find_consolidation_groups function."""

    def test_finds_groups(self) -> None:
        """Finds groups of similar entries."""
        entries = [
            MemoryEntry(
                key="auth-jwt-1",
                value="JWT configuration with RS256",
                tags=["security", "jwt"],
            ),
            MemoryEntry(
                key="auth-jwt-2",
                value="JWT token RS256 signing",
                tags=["security", "jwt"],
            ),
            MemoryEntry(
                key="auth-jwt-3",
                value="JWT RS256 algorithm setup",
                tags=["security", "jwt"],
            ),
            MemoryEntry(
                key="db-config",
                value="Database connection pooling",
                tags=["database", "postgres"],
            ),
        ]
        groups = find_consolidation_groups(entries, threshold=0.5, min_group_size=2)
        # Should find at least one group with the JWT entries
        assert len(groups) >= 1
        # Check that JWT entries are grouped together
        jwt_keys = {"auth-jwt-1", "auth-jwt-2", "auth-jwt-3"}
        found_jwt_group = any(jwt_keys & set(group) for group in groups)
        assert found_jwt_group

    def test_empty_entries(self) -> None:
        """Returns empty list for empty input."""
        groups = find_consolidation_groups([])
        assert groups == []

    def test_single_entry(self, security_entry: MemoryEntry) -> None:
        """Returns empty list for single entry (can't form group)."""
        groups = find_consolidation_groups([security_entry], min_group_size=2)
        assert groups == []

    def test_min_group_size(self) -> None:
        """Respects minimum group size."""
        entries = [
            MemoryEntry(key="a", value="test a", tags=["test"]),
            MemoryEntry(key="b", value="test b", tags=["test"]),
        ]
        # With min_group_size=3, no groups should form from 2 entries
        groups = find_consolidation_groups(entries, threshold=0.1, min_group_size=3)
        assert groups == []


# ---------------------------------------------------------------------------
# Same-topic detection tests
# ---------------------------------------------------------------------------


class TestSameTopicScore:
    """Tests for same_topic_score function."""

    def test_same_tier_overlapping_tags(
        self, security_entry: MemoryEntry, similar_security_entry: MemoryEntry
    ) -> None:
        """Returns 1.0 for same tier with sufficient tag overlap."""
        # Both are architectural tier with jwt, security tags
        score = same_topic_score(security_entry, similar_security_entry)
        assert score == 1.0

    def test_different_tiers(
        self, security_entry: MemoryEntry, database_entry: MemoryEntry
    ) -> None:
        """Returns 0.0 for different tiers."""
        # security_entry is architectural, database_entry is pattern
        score = same_topic_score(security_entry, database_entry)
        assert score == 0.0

    def test_same_tier_no_tag_overlap(self) -> None:
        """Returns 0.0 for same tier but no tag overlap."""
        entry_a = MemoryEntry(
            key="test-a",
            value="test",
            tier=MemoryTier.pattern,
            tags=["a", "b"],
        )
        entry_b = MemoryEntry(
            key="test-b",
            value="test",
            tier=MemoryTier.pattern,
            tags=["c", "d"],
        )
        score = same_topic_score(entry_a, entry_b)
        assert score == 0.0

    def test_no_tags(self) -> None:
        """Returns 0.0 when either entry has no tags."""
        entry_a = MemoryEntry(key="test-a", value="test", tags=[])
        entry_b = MemoryEntry(key="test-b", value="test", tags=["a", "b"])
        assert same_topic_score(entry_a, entry_b) == 0.0
        assert same_topic_score(entry_b, entry_a) == 0.0


class TestIsSameTopic:
    """Tests for is_same_topic function."""

    def test_returns_boolean(
        self, security_entry: MemoryEntry, similar_security_entry: MemoryEntry
    ) -> None:
        """Returns boolean result."""
        result = is_same_topic(security_entry, similar_security_entry)
        assert isinstance(result, bool)

    def test_same_topic_true(
        self, security_entry: MemoryEntry, similar_security_entry: MemoryEntry
    ) -> None:
        """Returns True for same-topic entries."""
        assert is_same_topic(security_entry, similar_security_entry) is True

    def test_same_topic_false(
        self, security_entry: MemoryEntry, database_entry: MemoryEntry
    ) -> None:
        """Returns False for different-topic entries."""
        assert is_same_topic(security_entry, database_entry) is False


# ---------------------------------------------------------------------------
# Default constants tests
# ---------------------------------------------------------------------------


class TestDefaultConstants:
    """Tests for default configuration constants."""

    def test_default_threshold(self) -> None:
        """Default threshold is 0.7."""
        assert DEFAULT_SIMILARITY_THRESHOLD == 0.7

    def test_default_weights(self) -> None:
        """Default weights sum to 1.0."""
        assert DEFAULT_TAG_WEIGHT + DEFAULT_TEXT_WEIGHT == 1.0
        assert DEFAULT_TAG_WEIGHT == 0.4
        assert DEFAULT_TEXT_WEIGHT == 0.6
