"""Tests for memory consolidation engine (Epic 58, Story 58.2)."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest

from tapps_brain.consolidation import (
    DEFAULT_MIN_ENTRIES_TO_CONSOLIDATE,
    MAX_CONSOLIDATED_VALUE_LENGTH,
    _extract_sentences,
    calculate_weighted_confidence,
    consolidate,
    detect_consolidation_reason,
    generate_consolidated_key,
    merge_entry_relations,
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
from tapps_brain.similarity import compute_similarity, is_same_topic
from tests.factories import make_entry as _make_entry


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

    def test_url_survives_a_merge_intact(self) -> None:
        """VAL-09: a merged value keeps ``learn.microsoft.com`` whole and cased.

        Regression: splitting on bare ``[.!?]+`` cut the host into
        ``['...official learn', 'microsoft', 'com docs)']``; ``microsoft`` was
        already in ``seen_sentences``, so the merge emitted
        ``official learn com docs)``.
        """
        newest = _make_entry(
            "graph-api-newest",
            "Graph API paging uses @odata.nextLink.",
            updated_at="2024-01-02T00:00:00+00:00",
        )
        older = _make_entry(
            "graph-api-older",
            "Throttling limits are documented at https://learn.microsoft.com/graph/throttling "
            "(the official Learn docs).",
            updated_at="2024-01-01T00:00:00+00:00",
        )
        result = merge_values([newest, older])

        assert "https://learn.microsoft.com/graph/throttling" in result
        assert "official Learn docs" in result
        # The mangled fragments the old splitter produced must not appear.
        assert "learn com" not in result
        assert "com docs" not in result

    def test_merge_preserves_original_casing(self) -> None:
        """Merged fragments keep proper nouns and acronyms as written."""
        newest = _make_entry(
            "casing-newest", "We ship weekly.", updated_at="2024-01-02T00:00:00+00:00"
        )
        older = _make_entry(
            "casing-older",
            "PostgreSQL and pgvector back the HNSW index.",
            updated_at="2024-01-01T00:00:00+00:00",
        )
        result = merge_values([newest, older])
        assert "PostgreSQL and pgvector back the HNSW index." in result

    def test_merge_is_byte_identical_across_runs(self) -> None:
        """Determinism guarantee: same inputs, same bytes, every time."""
        entries = [
            _make_entry(
                "det-a",
                "Alpha one. Alpha two. Alpha three.",
                updated_at="2024-01-01T00:00:00+00:00",
            ),
            _make_entry(
                "det-b",
                "Beta one. Beta two. Beta three.",
                updated_at="2024-01-02T00:00:00+00:00",
            ),
            _make_entry(
                "det-c",
                "Gamma one. Gamma two. Gamma three.",
                updated_at="2024-01-03T00:00:00+00:00",
            ),
        ]
        first = merge_values(entries)
        assert all(merge_values(entries) == first for _ in range(5))


class TestExtractSentences:
    """Boundary detection for the sentence splitter used by ``merge_values``."""

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("learn.microsoft.com is the host.", ["learn.microsoft.com is the host."]),
            ("Pinned at v3.30.0 for now.", ["Pinned at v3.30.0 for now."]),
            ("The floor is 0.6 exactly.", ["The floor is 0.6 exactly."]),
            (
                "Use RS256, e.g. for signing. Rotate quarterly.",
                ["Use RS256, e.g. for signing.", "Rotate quarterly."],
            ),
            ("First one! Second one? Third one.", ["First one!", "Second one?", "Third one."]),
        ],
    )
    def test_boundaries(self, text: str, expected: list[str]) -> None:
        assert _extract_sentences(text) == expected

    def test_returns_an_ordered_deduplicated_list(self) -> None:
        """Never a set — set order is hash-seed dependent and breaks determinism."""
        result = _extract_sentences("One. Two. One. Three. two.")
        assert isinstance(result, list)
        assert result == ["One.", "Two.", "Three."]

    def test_dedup_is_case_insensitive_but_emission_is_not(self) -> None:
        assert _extract_sentences("Alpha beta. ALPHA BETA.") == ["Alpha beta."]


# ---------------------------------------------------------------------------
# Confidence calculation tests
# ---------------------------------------------------------------------------


class TestCalculateWeightedConfidence:
    """Tests for calculate_weighted_confidence function."""

    def test_single_entry(self) -> None:
        """Single entry returns its (decayed) confidence."""
        entry = _make_entry("test", "value", confidence=0.8)
        result = calculate_weighted_confidence([entry])
        assert result == pytest.approx(0.8, abs=1e-6)

    def test_empty_entries(self) -> None:
        """Empty list returns default 0.5."""
        result = calculate_weighted_confidence([])
        assert result == 0.5

    def test_newer_entries_weighted_higher(self) -> None:
        """Newer entries have higher weight when both are still fresh."""
        now = datetime.now(tz=UTC)
        old = _make_entry(
            "old",
            "value",
            confidence=0.5,
            updated_at=(now - timedelta(hours=2)).isoformat(),
        )
        new = _make_entry(
            "new",
            "value",
            confidence=0.9,
            updated_at=now.isoformat(),
        )
        result = calculate_weighted_confidence([old, new])
        # Result should be closer to 0.9 than 0.5
        assert result > 0.7

    def test_result_in_range(self, jwt_entries: list[MemoryEntry]) -> None:
        """Result is always in [0.0, 1.0]."""
        result = calculate_weighted_confidence(jwt_entries)
        assert 0.0 <= result <= 1.0

    def test_does_not_resurrect_floor_decayed_confidence(self) -> None:
        """Merging long-stale pattern entries must not mint near-fresh confidence."""
        from tapps_brain.decay import DecayConfig, calculate_decayed_confidence

        now = datetime.now(tz=UTC)
        old = (now - timedelta(days=200)).isoformat()
        a = _make_entry(
            "a",
            "JWT RS256 signing asymmetric keys API auth",
            confidence=0.9,
            tier=MemoryTier.pattern,
            updated_at=old,
        )
        b = _make_entry(
            "b",
            "JWT RS256 signing asymmetric keys service auth",
            confidence=0.85,
            tier=MemoryTier.pattern,
            updated_at=old,
        )
        cfg = DecayConfig()
        assert calculate_decayed_confidence(a, cfg, now=now) == pytest.approx(0.1)
        assert calculate_decayed_confidence(b, cfg, now=now) == pytest.approx(0.1)
        merged = calculate_weighted_confidence([a, b])
        assert merged == pytest.approx(0.1)
        consolidated = consolidate([a, b])
        assert calculate_decayed_confidence(consolidated, cfg, now=now) == pytest.approx(0.1)


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

    def test_custom_tier_only(self) -> None:
        """Custom tier string is returned when it's the only tier present."""
        entries = [
            _make_entry("a", "v", tier="my_custom_layer"),
        ]
        result = select_tier(entries)
        assert result == "my_custom_layer"

    def test_custom_tier_beats_context(self) -> None:
        """Custom tier string (default priority 3) beats context tier (priority 2)."""
        entries = [
            _make_entry("a", "v", tier=MemoryTier.context),
            _make_entry("b", "v", tier="my_custom_layer"),
        ]
        result = select_tier(entries)
        assert result == "my_custom_layer"

    def test_procedural_beats_custom_tier(self) -> None:
        """Built-in procedural (priority 3) ties with custom tier; architectural always wins."""
        entries = [
            _make_entry("a", "v", tier="my_custom_layer"),
            _make_entry("b", "v", tier=MemoryTier.architectural),
        ]
        result = select_tier(entries)
        assert result == MemoryTier.architectural

    def test_context_over_ephemeral(self) -> None:
        """Context outranks ephemeral (most durable wins)."""
        entries = [
            _make_entry("a", "v", tier=MemoryTier.ephemeral),
            _make_entry("b", "v", tier=MemoryTier.context),
        ]
        result = select_tier(entries)
        assert result == MemoryTier.context

    def test_context_over_session(self) -> None:
        """Context outranks session (most durable wins)."""
        entries = [
            _make_entry("a", "v", tier=MemoryTier.session),
            _make_entry("b", "v", tier=MemoryTier.context),
        ]
        result = select_tier(entries)
        assert result == MemoryTier.context


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

    def test_same_topic_below_threshold_is_not_a_match(self) -> None:
        """Same tier + tags is necessary but NOT sufficient — threshold still applies.

        Regression: the same-topic fast path used to return matches without
        ever consulting *threshold*, so unrelated long-form entries that
        merely shared tags were merged and lost their bodies to the 4096-char
        merge cap.
        """
        shared_tags = ["linkedin", "publishing", "content"]
        entry = _make_entry(
            key="linkedin-publish-cadence",
            value=(
                "Publishing cadence targets three posts per week, scheduled "
                "Tuesday, Wednesday and Thursday mornings in the author's "
                "local timezone."
            ),
            tier=MemoryTier.architectural,
            tags=shared_tags,
        )
        unrelated = _make_entry(
            key="linkedin-publish-image-pipeline",
            value=(
                "Rendered artwork is uploaded through the asset service, which "
                "returns a signed URL that expires after fifteen minutes and "
                "must be refreshed before attachment."
            ),
            tier=MemoryTier.architectural,
            tags=shared_tags,
        )

        # Precondition: they *are* same-topic, so only the threshold can stop them.
        assert is_same_topic(entry, unrelated) is True
        assert compute_similarity(entry, unrelated).combined_score < 0.7

        assert should_consolidate(entry, [unrelated], threshold=0.7) == []

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
        candidates: list[MemoryEntry] = [*jwt_entries, consolidated]
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


# ---------------------------------------------------------------------------
# STORY-006.5: merge_entry_relations tests
# ---------------------------------------------------------------------------


class TestMergeEntryRelations:
    """Tests for merge_entry_relations()."""

    def test_empty_input(self) -> None:
        result = merge_entry_relations([], "consolidated-key")
        assert result == []

    def test_single_source(self) -> None:
        rels = [
            {
                "subject": "ServiceA",
                "predicate": "uses",
                "object_entity": "ServiceB",
                "confidence": 0.9,
            },
        ]
        result = merge_entry_relations([rels], "c-key")
        assert len(result) == 1
        assert result[0].subject == "ServiceA"
        assert result[0].source_entry_keys == ["c-key"]

    def test_deduplicates_same_triple(self) -> None:
        """Same triple from two sources produces one relation."""
        rels1 = [{"subject": "A", "predicate": "uses", "object_entity": "B", "confidence": 0.7}]
        rels2 = [{"subject": "A", "predicate": "uses", "object_entity": "B", "confidence": 0.9}]
        result = merge_entry_relations([rels1, rels2], "c-key")
        assert len(result) == 1
        # Should keep highest confidence
        assert result[0].confidence == 0.9

    def test_case_insensitive_dedup(self) -> None:
        """Deduplication is case-insensitive."""
        rels1 = [{"subject": "ServiceA", "predicate": "Uses", "object_entity": "ServiceB"}]
        rels2 = [{"subject": "servicea", "predicate": "uses", "object_entity": "serviceb"}]
        result = merge_entry_relations([rels1, rels2], "c-key")
        assert len(result) == 1

    def test_different_triples_preserved(self) -> None:
        """Different triples are all kept."""
        rels1 = [{"subject": "A", "predicate": "uses", "object_entity": "B"}]
        rels2 = [{"subject": "C", "predicate": "manages", "object_entity": "D"}]
        result = merge_entry_relations([rels1, rels2], "c-key")
        assert len(result) == 2

    def test_target_key_in_source_keys(self) -> None:
        """All merged relations have the target_key in source_entry_keys."""
        rels = [{"subject": "X", "predicate": "uses", "object_entity": "Y"}]
        result = merge_entry_relations([rels], "my-target")
        assert "my-target" in result[0].source_entry_keys
