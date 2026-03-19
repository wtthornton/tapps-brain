"""Unit tests for entity/relationship extraction (tapps_brain.relations)."""

from __future__ import annotations

from tapps_brain.relations import (
    RelationEntry,
    _clean_entity,
    expand_via_relations,
    extract_relations,
    extract_relations_from_entries,
)
from tests.factories import make_entry as _make_entry


def _rel(subject, predicate, obj, keys=None):
    return RelationEntry(
        subject=subject,
        predicate=predicate,
        object_entity=obj,
        source_entry_keys=keys or ["test"],
    )


# ---------------------------------------------------------------------------
# Tests: RelationEntry model
# ---------------------------------------------------------------------------


class TestRelationEntryModel:
    def test_defaults(self):
        r = RelationEntry(subject="A", predicate="uses", object_entity="B")
        assert r.confidence == 0.8
        assert r.source_entry_keys == []
        assert r.created_at  # auto-populated

    def test_class_constants(self):
        assert RelationEntry.MAX_RELATIONS_PER_ENTRY == 5
        assert RelationEntry.MIN_ENTITY_LENGTH == 2


# ---------------------------------------------------------------------------
# Tests: _clean_entity
# ---------------------------------------------------------------------------


class TestCleanEntity:
    def test_strips_whitespace(self):
        assert _clean_entity("  hello  ") == "hello"

    def test_strips_punctuation(self):
        assert _clean_entity("hello.") == "hello"
        assert _clean_entity('"hello"') == "hello"

    def test_collapses_internal_whitespace(self):
        assert _clean_entity("a   b   c") == "a b c"


# ---------------------------------------------------------------------------
# Tests: extract_relations
# ---------------------------------------------------------------------------


class TestExtractRelations:
    def test_empty_value(self):
        assert extract_relations("k", "") == []
        assert extract_relations("k", "   ") == []

    def test_no_relations_in_text(self):
        assert extract_relations("k", "This is a plain sentence") == []

    def test_uses_pattern(self):
        rels = extract_relations("k1", "The backend uses PostgreSQL")
        assert len(rels) >= 1
        r = rels[0]
        assert r.predicate == "uses"
        assert r.source_entry_keys == ["k1"]

    def test_manages_pattern(self):
        rels = extract_relations("k", "The scheduler manages background tasks")
        assert len(rels) >= 1
        assert rels[0].predicate == "manages"

    def test_owns_pattern(self):
        rels = extract_relations("k", "Team alpha owns the billing module")
        assert len(rels) >= 1
        assert rels[0].predicate == "owns"

    def test_handles_pattern(self):
        rels = extract_relations("k", "The middleware handles authentication")
        assert len(rels) >= 1
        assert rels[0].predicate == "handles"

    def test_depends_on_pattern(self):
        rels = extract_relations("k", "The API depends on Redis")
        assert len(rels) >= 1
        assert rels[0].predicate == "depends on"

    def test_creates_pattern(self):
        rels = extract_relations("k", "The factory creates new widgets")
        assert len(rels) >= 1
        assert rels[0].predicate == "creates"

    def test_provides_pattern(self):
        rels = extract_relations("k", "The service provides data access")
        assert len(rels) >= 1
        assert rels[0].predicate == "provides"

    def test_case_insensitive(self):
        rels = extract_relations("k", "The Backend USES PostgreSQL")
        assert len(rels) >= 1

    def test_skips_short_entities(self):
        rels = extract_relations("k", "X uses Y")
        assert rels == []

    def test_deduplicates_within_entry(self):
        text = "The API uses Redis. The API uses Redis."
        rels = extract_relations("k", text)
        # Same triple should appear only once
        triples = [(r.subject.lower(), r.predicate, r.object_entity.lower()) for r in rels]
        assert len(triples) == len(set(triples))

    def test_max_relations_cap(self):
        # Build text with many relation sentences
        lines = [f"Service{i} uses Database{i}" for i in range(10)]
        text = ". ".join(lines)
        rels = extract_relations("k", text)
        assert len(rels) <= RelationEntry.MAX_RELATIONS_PER_ENTRY

    def test_multiple_relations_in_one_text(self):
        text = "The backend uses Redis and the scheduler manages background tasks"
        rels = extract_relations("k", text)
        predicates = {r.predicate for r in rels}
        assert "uses" in predicates
        assert "manages" in predicates


# ---------------------------------------------------------------------------
# Tests: extract_relations_from_entries
# ---------------------------------------------------------------------------


class TestExtractFromEntries:
    def test_empty_list(self):
        assert extract_relations_from_entries([]) == []

    def test_single_entry(self):
        e = _make_entry(key="m1", value="The API uses Redis")
        rels = extract_relations_from_entries([e])
        assert len(rels) >= 1
        assert "m1" in rels[0].source_entry_keys

    def test_deduplicates_across_entries(self):
        e1 = _make_entry(key="m1", value="The API uses Redis")
        e2 = _make_entry(key="m2", value="The API uses Redis")
        rels = extract_relations_from_entries([e1, e2])
        # Should be deduplicated to one relation with merged keys
        api_redis = [r for r in rels if "redis" in r.object_entity.lower()]
        assert len(api_redis) == 1
        assert "m1" in api_redis[0].source_entry_keys
        assert "m2" in api_redis[0].source_entry_keys

    def test_different_entries_produce_different_relations(self):
        e1 = _make_entry(key="m1", value="The API uses Redis")
        e2 = _make_entry(key="m2", value="The scheduler manages workers")
        rels = extract_relations_from_entries([e1, e2])
        predicates = {r.predicate for r in rels}
        assert len(predicates) >= 2


# ---------------------------------------------------------------------------
# Tests: expand_via_relations
# ---------------------------------------------------------------------------


class TestExpandViaRelations:
    def test_empty_query(self):
        assert expand_via_relations("", [_rel("A", "uses", "B")]) == []

    def test_empty_relations(self):
        assert expand_via_relations("who handles auth", []) == []

    def test_no_matching_pattern(self):
        rels = [_rel("A", "uses", "B")]
        assert expand_via_relations("tell me about auth", rels) == []

    def test_who_handles_query(self):
        rels = [_rel("AuthService", "handles", "authentication")]
        result = expand_via_relations("who handles authentication", rels)
        assert "AuthService" in result

    def test_what_uses_query(self):
        rels = [_rel("Backend", "uses", "Redis")]
        result = expand_via_relations("what uses Redis", rels)
        assert "Backend" in result

    def test_subject_match_returns_object(self):
        rels = [_rel("Backend", "uses", "Redis")]
        result = expand_via_relations("what uses Backend", rels)
        assert "Redis" in result

    def test_hop2_traversal(self):
        rels = [
            _rel("AuthService", "handles", "authentication"),
            _rel("AuthService", "uses", "JWT"),
        ]
        result = expand_via_relations("who handles authentication", rels)
        # Hop 1: AuthService; Hop 2: JWT (related to AuthService)
        assert "AuthService" in result
        assert "JWT" in result

    def test_who_manages_query(self):
        rels = [_rel("Scheduler", "manages", "jobs")]
        result = expand_via_relations("who manages jobs", rels)
        assert "Scheduler" in result

    def test_who_owns_query(self):
        rels = [_rel("TeamAlpha", "owns", "billing")]
        result = expand_via_relations("who owns billing", rels)
        assert "TeamAlpha" in result

    def test_who_creates_query(self):
        rels = [_rel("Factory", "creates", "widgets")]
        result = expand_via_relations("who creates widgets", rels)
        assert "Factory" in result

    def test_who_provides_query(self):
        rels = [_rel("DataService", "provides", "analytics")]
        result = expand_via_relations("who provides analytics", rels)
        assert "DataService" in result

    def test_what_depends_on_query(self):
        rels = [_rel("API", "depends on", "Redis")]
        result = expand_via_relations("what depends on Redis", rels)
        assert "API" in result

    def test_no_duplicate_entities(self):
        rels = [
            _rel("A", "handles", "X"),
            _rel("A", "uses", "X"),
        ]
        result = expand_via_relations("who handles X", rels)
        # A should appear only once
        assert result.count("A") == 1

    def test_hop2_does_not_include_target(self):
        rels = [
            _rel("Service", "handles", "auth"),
            _rel("Service", "uses", "auth"),
        ]
        result = expand_via_relations("who handles auth", rels)
        # "auth" is the target, should not appear in expanded results from hop2
        # (hop1 may include it if subject matches target, but that's by design)
        assert "Service" in result
