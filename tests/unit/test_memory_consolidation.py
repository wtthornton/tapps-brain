"""Tests for memory consolidation engine (Epic 58, Story 58.2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tapps_brain.consolidation import (
    DEFAULT_MIN_ENTRIES_TO_CONSOLIDATE,
    MAX_CONSOLIDATED_VALUE_LENGTH,
    calculate_weighted_confidence,
    consolidate,
    detect_consolidation_reason,
    generate_consolidated_key,
    merge_tags,
    merge_values,
    select_tier,
    should_consolidate,
)
from tapps_brain.models import (
    ConsolidatedEntry,
    ConsolidationReason,
    MemoryEntry,
    MemorySource,
    MemoryTier,
)

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


def _make_entry(
    key: str,
    value: str,
    *,
    tier: MemoryTier = MemoryTier.pattern,
    confidence: float = 0.7,
    tags: list[str] | None = None,
    updated_at: str | None = None,
) -> MemoryEntry:
    """Helper to create test entries."""
    if updated_at is None:
        updated_at = datetime.now(tz=UTC).isoformat()
    return MemoryEntry(
        key=key,
        value=value,
        tier=tier,
        confidence=confidence,
        tags=tags or [],
        updated_at=updated_at,
    )


@pytest.fixture
def jwt_entries() -> list[MemoryEntry]:
    """A set of related JWT entries for consolidation."""
    base_time = datetime.now(tz=UTC)
    return [
        _make_entry(
            key="auth-jwt-config",
            value="Use RS256 for JWT signing. Store keys in environment variables.",
            tier=MemoryTier.architectural,
            confidence=0.9,
            tags=["security", "jwt", "authentication"],
            updated_at=(base_time - timedelta(days=2)).isoformat(),
        ),
        _make_entry(
            key="auth-jwt-tokens",
            value="JWT tokens should use RS256 algorithm. Refresh tokens expire in 7 days.",
            tier=MemoryTier.architectural,
            confidence=0.8,
            tags=["security", "jwt", "tokens"],
            updated_at=(base_time - timedelta(days=1)).isoformat(),
        ),
        _make_entry(
            key="auth-jwt-expiry",
            value="Access tokens expire in 15 minutes. Use sliding window for refresh.",
            tier=MemoryTier.pattern,
            confidence=0.7,
            tags=["security", "jwt", "expiry"],
            updated_at=base_time.isoformat(),
        ),
    ]


@pytest.fixture
def db_entry() -> MemoryEntry:
    """An unrelated database entry."""
    return _make_entry(
        key="db-connection-pool",
        value="Use connection pooling with max 20 connections.",
        tier=MemoryTier.pattern,
        tags=["database", "postgres"],
    )


# ---------------------------------------------------------------------------
# Key generation tests
# ---------------------------------------------------------------------------


class TestGenerateConsolidatedKey:
    """Tests for generate_consolidated_key function."""

    def test_generates_unique_key(self, jwt_entries: list[MemoryEntry]) -> None:
        """Generates a unique key for consolidated entry."""
        key = generate_consolidated_key(jwt_entries)
        assert isinstance(key, str)
        assert len(key) > 0
        assert len(key) <= 128  # Max key length

    def test_deterministic(self, jwt_entries: list[MemoryEntry]) -> None:
        """Same inputs produce same key."""
        key1 = generate_consolidated_key(jwt_entries)
        key2 = generate_consolidated_key(jwt_entries)
        assert key1 == key2

    def test_different_entries_different_keys(
        self, jwt_entries: list[MemoryEntry], db_entry: MemoryEntry
    ) -> None:
        """Different entry sets produce different keys."""
        key1 = generate_consolidated_key(jwt_entries)
        key2 = generate_consolidated_key([jwt_entries[0], db_entry])
        assert key1 != key2

    def test_empty_entries(self) -> None:
        """Empty list returns placeholder key."""
        key = generate_consolidated_key([])
        assert key == "consolidated-empty"

    def test_key_format_valid(self, jwt_entries: list[MemoryEntry]) -> None:
        """Generated key matches required format (slug)."""
        import re

        key = generate_consolidated_key(jwt_entries)
        # Key should be lowercase alphanumeric with dashes
        assert re.match(r"^[a-z0-9][a-z0-9._-]+$", key)


# ---------------------------------------------------------------------------
# Value merging tests
# ---------------------------------------------------------------------------


class TestMergeValues:
    """Tests for merge_values function."""

    def test_single_entry(self) -> None:
        """Single entry returns its value unchanged."""
        entry = _make_entry("test", "Test value here.")
        result = merge_values([entry])
        assert result == "Test value here."

    def test_empty_entries(self) -> None:
        """Empty list returns empty string."""
        result = merge_values([])
        assert result == ""

    def test_newest_value_primary(self, jwt_entries: list[MemoryEntry]) -> None:
        """Newest entry's value is the primary content."""
        result = merge_values(jwt_entries)
        # jwt_entries[2] is newest (auth-jwt-expiry)
        assert "Access tokens expire" in result

    def test_includes_older_unique_content(self, jwt_entries: list[MemoryEntry]) -> None:
        """Older entries' unique content is included."""
        result = merge_values(jwt_entries)
        # Should have content from older entries
        assert len(result) > len(jwt_entries[-1].value)

    def test_truncates_long_values(self) -> None:
        """Truncates merged value if too long."""
        # Create entries with very long values
        entries = [
            _make_entry("long-1", "A" * 2000, updated_at="2024-01-01T00:00:00+00:00"),
            _make_entry("long-2", "B" * 2000, updated_at="2024-01-02T00:00:00+00:00"),
            _make_entry("long-3", "C" * 2000, updated_at="2024-01-03T00:00:00+00:00"),
        ]
        result = merge_values(entries)
        assert len(result) <= MAX_CONSOLIDATED_VALUE_LENGTH


# ---------------------------------------------------------------------------
# Confidence calculation tests
# ---------------------------------------------------------------------------


class TestCalculateWeightedConfidence:
    """Tests for calculate_weighted_confidence function."""

    def test_single_entry(self) -> None:
        """Single entry returns its confidence."""
        entry = _make_entry("test", "value", confidence=0.8)
        result = calculate_weighted_confidence([entry])
        assert result == 0.8

    def test_empty_entries(self) -> None:
        """Empty list returns default 0.5."""
        result = calculate_weighted_confidence([])
        assert result == 0.5

    def test_newer_entries_weighted_higher(self) -> None:
        """Newer entries have higher weight."""
        old = _make_entry("old", "value", confidence=0.5, updated_at="2024-01-01T00:00:00+00:00")
        new = _make_entry("new", "value", confidence=0.9, updated_at="2024-01-02T00:00:00+00:00")
        result = calculate_weighted_confidence([old, new])
        # Result should be closer to 0.9 than 0.5
        assert result > 0.7

    def test_result_in_range(self, jwt_entries: list[MemoryEntry]) -> None:
        """Result is always in [0.0, 1.0]."""
        result = calculate_weighted_confidence(jwt_entries)
        assert 0.0 <= result <= 1.0


# ---------------------------------------------------------------------------
# Tag merging tests
# ---------------------------------------------------------------------------


class TestMergeTags:
    """Tests for merge_tags function."""

    def test_empty_entries(self) -> None:
        """Empty list returns empty tags."""
        result = merge_tags([])
        assert result == []

    def test_merges_all_tags(self, jwt_entries: list[MemoryEntry]) -> None:
        """All unique tags are included."""
        result = merge_tags(jwt_entries)
        # All entries have "security" and "jwt"
        assert "security" in result
        assert "jwt" in result

    def test_common_tags_first(self, jwt_entries: list[MemoryEntry]) -> None:
        """Common tags appear before unique tags."""
        result = merge_tags(jwt_entries)
        # "security" and "jwt" appear in all entries, should be first
        common_indices = [result.index("security"), result.index("jwt")]
        unique_indices = [
            result.index(t) for t in ["authentication", "tokens", "expiry"] if t in result
        ]
        if unique_indices:
            assert max(common_indices) < min(unique_indices)

    def test_respects_max_tags(self) -> None:
        """Respects max_tags limit."""
        # Use multiple entries to get > 10 unique tags (model limits to 10 per entry)
        entries = [
            _make_entry("test-1", "value", tags=["tag0", "tag1", "tag2", "tag3", "tag4"]),
            _make_entry("test-2", "value", tags=["tag5", "tag6", "tag7", "tag8", "tag9"]),
            _make_entry("test-3", "value", tags=["tag10", "tag11", "tag12"]),
        ]
        result = merge_tags(entries, max_tags=5)
        assert len(result) <= 5


# ---------------------------------------------------------------------------
# Tier selection tests
# ---------------------------------------------------------------------------


class TestSelectTier:
    """Tests for select_tier function."""

    def test_empty_entries(self) -> None:
        """Empty list returns pattern tier."""
        result = select_tier([])
        assert result == MemoryTier.pattern

    def test_selects_most_durable(self) -> None:
        """Selects most durable tier (architectural > pattern > procedural > context)."""
        entries = [
            _make_entry("a", "v", tier=MemoryTier.context),
            _make_entry("b", "v", tier=MemoryTier.architectural),
            _make_entry("c", "v", tier=MemoryTier.pattern),
        ]
        result = select_tier(entries)
        assert result == MemoryTier.architectural

    def test_procedural_over_context(self) -> None:
        """Procedural is selected over context (Epic 65.11)."""
        entries = [
            _make_entry("a", "v", tier=MemoryTier.context),
            _make_entry("b", "v", tier=MemoryTier.procedural),
        ]
        result = select_tier(entries)
        assert result == MemoryTier.procedural

    def test_pattern_over_context(self) -> None:
        """Pattern is selected over context."""
        entries = [
            _make_entry("a", "v", tier=MemoryTier.context),
            _make_entry("b", "v", tier=MemoryTier.pattern),
        ]
        result = select_tier(entries)
        assert result == MemoryTier.pattern


# ---------------------------------------------------------------------------
# Main consolidation tests
# ---------------------------------------------------------------------------


class TestConsolidate:
    """Tests for consolidate function."""

    def test_returns_consolidated_entry(self, jwt_entries: list[MemoryEntry]) -> None:
        """Returns a ConsolidatedEntry instance."""
        result = consolidate(jwt_entries)
        assert isinstance(result, ConsolidatedEntry)

    def test_raises_on_single_entry(self) -> None:
        """Raises ValueError for fewer than 2 entries."""
        entry = _make_entry("test", "value")
        with pytest.raises(ValueError, match="at least 2 entries"):
            consolidate([entry])

    def test_raises_on_empty(self) -> None:
        """Raises ValueError for empty list."""
        with pytest.raises(ValueError, match="at least 2 entries"):
            consolidate([])

    def test_tracks_source_ids(self, jwt_entries: list[MemoryEntry]) -> None:
        """Source IDs track original entry keys."""
        result = consolidate(jwt_entries)
        assert len(result.source_ids) == len(jwt_entries)
        for entry in jwt_entries:
            assert entry.key in result.source_ids

    def test_sets_consolidation_reason(self, jwt_entries: list[MemoryEntry]) -> None:
        """Consolidation reason is set correctly."""
        result = consolidate(jwt_entries, reason=ConsolidationReason.same_topic)
        assert result.consolidation_reason == ConsolidationReason.same_topic

    def test_sets_consolidated_at(self, jwt_entries: list[MemoryEntry]) -> None:
        """consolidated_at is set to current time."""
        result = consolidate(jwt_entries)
        assert result.consolidated_at is not None
        # Should be recent (within last minute)
        consolidated_time = datetime.fromisoformat(result.consolidated_at)
        assert (datetime.now(tz=UTC) - consolidated_time).total_seconds() < 60

    def test_is_consolidated_true(self, jwt_entries: list[MemoryEntry]) -> None:
        """is_consolidated flag is True."""
        result = consolidate(jwt_entries)
        assert result.is_consolidated is True

    def test_source_is_system(self, jwt_entries: list[MemoryEntry]) -> None:
        """Source is set to system."""
        result = consolidate(jwt_entries)
        assert result.source == MemorySource.system

    def test_source_agent_set(self, jwt_entries: list[MemoryEntry]) -> None:
        """source_agent identifies consolidation."""
        result = consolidate(jwt_entries)
        assert "consolidation" in result.source_agent.lower()


# ---------------------------------------------------------------------------
# Should consolidate tests
# ---------------------------------------------------------------------------


class TestShouldConsolidate:
    """Tests for should_consolidate function."""

    def test_finds_similar_entries(self, jwt_entries: list[MemoryEntry]) -> None:
        """Finds entries that should be consolidated."""
        # Create a new entry similar to existing ones
        new_entry = _make_entry(
            key="auth-jwt-new",
            value="JWT authentication with RS256 algorithm",
            tier=MemoryTier.architectural,
            tags=["security", "jwt"],
        )
        matches = should_consolidate(new_entry, jwt_entries, threshold=0.3)
        assert len(matches) > 0

    def test_empty_candidates(self) -> None:
        """Returns empty list for no candidates."""
        entry = _make_entry("test", "value")
        matches = should_consolidate(entry, [])
        assert matches == []

    def test_excludes_already_consolidated(self, jwt_entries: list[MemoryEntry]) -> None:
        """Excludes entries marked as consolidated."""
        # Create a consolidated entry
        consolidated = ConsolidatedEntry(
            key="already-consolidated",
            value="Already consolidated content",
            source_ids=["old-1", "old-2"],
            is_consolidated=True,
        )
        new_entry = _make_entry("new", "content")
        candidates = [*jwt_entries, consolidated]  # type: ignore[operator]
        matches = should_consolidate(new_entry, candidates, threshold=0.1)
        assert consolidated not in matches

    def test_excludes_self(self) -> None:
        """Excludes the entry itself from matches."""
        entry = _make_entry("test", "test value", tags=["test"])
        matches = should_consolidate(entry, [entry], threshold=0.0)
        assert entry not in matches


# ---------------------------------------------------------------------------
# Consolidation reason detection tests
# ---------------------------------------------------------------------------


class TestDetectConsolidationReason:
    """Tests for detect_consolidation_reason function."""

    def test_detects_same_topic(self) -> None:
        """Detects same-topic when tier and tags match."""
        entry = _make_entry(
            "auth-jwt-1", "value", tier=MemoryTier.architectural, tags=["security", "jwt"]
        )
        match = _make_entry(
            "auth-jwt-2", "value", tier=MemoryTier.architectural, tags=["security", "jwt"]
        )
        reason = detect_consolidation_reason(entry, [match])
        assert reason == ConsolidationReason.same_topic

    def test_detects_supersession(self) -> None:
        """Detects supersession when entry references another."""
        entry = _make_entry(
            "auth-jwt-v2",
            "Updated auth-jwt-config with new settings",  # References old key
        )
        match = _make_entry("auth-jwt-config", "Old settings")
        reason = detect_consolidation_reason(entry, [match])
        assert reason == ConsolidationReason.supersession

    def test_defaults_to_similarity(self) -> None:
        """Defaults to similarity when no specific pattern."""
        entry = _make_entry("new-entry", "Some content", tier=MemoryTier.pattern)
        match = _make_entry("other-entry", "Other content", tier=MemoryTier.architectural)
        reason = detect_consolidation_reason(entry, [match])
        assert reason == ConsolidationReason.similarity

    def test_empty_matches_returns_manual(self) -> None:
        """Empty matches returns manual reason."""
        entry = _make_entry("test", "value")
        reason = detect_consolidation_reason(entry, [])
        assert reason == ConsolidationReason.manual


# ---------------------------------------------------------------------------
# Constants tests
# ---------------------------------------------------------------------------


class TestConstants:
    """Tests for module constants."""

    def test_default_min_entries(self) -> None:
        """Default minimum entries is 2."""
        assert DEFAULT_MIN_ENTRIES_TO_CONSOLIDATE == 2

    def test_max_value_length(self) -> None:
        """Max consolidated value length is reasonable."""
        assert MAX_CONSOLIDATED_VALUE_LENGTH == 4096
