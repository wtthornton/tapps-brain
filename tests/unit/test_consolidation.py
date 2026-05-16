"""Unit tests for merge_entry_relations in consolidation.py (TAP-1816).

Verifies that confidence_history is accumulated across merged duplicate triples
and that the scalar confidence is re-derived via the configured aggregator.
"""

from __future__ import annotations

import pytest

from tapps_brain.consolidation import merge_entry_relations
from tapps_brain.relations import RelationEntry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rel(
    subject: str,
    predicate: str,
    obj: str,
    confidence: float,
    *,
    history: list[float] | None = None,
) -> dict:
    """Build a minimal relation dict as would be stored/serialized."""
    d: dict = {
        "subject": subject,
        "predicate": predicate,
        "object_entity": obj,
        "confidence": confidence,
    }
    if history is not None:
        d["confidence_history"] = history
    return d


# ---------------------------------------------------------------------------
# confidence_history accumulation
# ---------------------------------------------------------------------------


def test_merge_three_duplicates_accumulates_history() -> None:
    """Merging three duplicate triples accumulates all confidence values."""
    triple = ("A", "uses", "B")
    rel_lists = [
        [_rel(*triple, confidence=0.6)],
        [_rel(*triple, confidence=0.7)],
        [_rel(*triple, confidence=0.9)],
    ]
    results = merge_entry_relations(rel_lists, target_key="consolidated-key")
    assert len(results) == 1
    assert sorted(results[0].confidence_history) == pytest.approx([0.6, 0.7, 0.9])


def test_merge_three_duplicates_confidence_is_max() -> None:
    """confidence is the max of all contributing confidences (default aggregator)."""
    triple = ("A", "uses", "B")
    rel_lists = [
        [_rel(*triple, confidence=0.6)],
        [_rel(*triple, confidence=0.7)],
        [_rel(*triple, confidence=0.9)],
    ]
    results = merge_entry_relations(rel_lists, target_key="consolidated-key")
    assert results[0].confidence == pytest.approx(0.9)


def test_merge_three_duplicates_exact_history_values() -> None:
    """confidence_history contains exactly [0.6, 0.7, 0.9] after three-way merge."""
    triple = ("A", "uses", "B")
    rel_lists = [
        [_rel(*triple, confidence=0.6)],
        [_rel(*triple, confidence=0.7)],
        [_rel(*triple, confidence=0.9)],
    ]
    results = merge_entry_relations(rel_lists, target_key="consolidated-key")
    assert sorted(results[0].confidence_history) == pytest.approx([0.6, 0.7, 0.9])


def test_merge_no_duplicates_history_is_singleton() -> None:
    """A single relation (no merging) has confidence_history == [confidence]."""
    rel_lists = [[_rel("X", "manages", "Y", confidence=0.75)]]
    results = merge_entry_relations(rel_lists, target_key="k1")
    assert results[0].confidence_history == pytest.approx([0.75])
    assert results[0].confidence == pytest.approx(0.75)


def test_merge_with_custom_aggregator_uses_mean() -> None:
    """confidence_aggregator kwarg overrides max with a custom function."""
    triple = ("A", "provides", "C")
    rel_lists = [
        [_rel(*triple, confidence=0.6)],
        [_rel(*triple, confidence=0.9)],
    ]
    results = merge_entry_relations(
        rel_lists,
        target_key="k",
        confidence_aggregator=lambda hist: sum(hist) / len(hist),
    )
    assert results[0].confidence == pytest.approx(0.75)


def test_merge_distinct_triples_each_has_own_history() -> None:
    """Distinct triples each maintain their own confidence_history independently."""
    rel_lists = [
        [
            _rel("A", "uses", "B", confidence=0.8),
            _rel("C", "owns", "D", confidence=0.6),
        ],
        [
            _rel("A", "uses", "B", confidence=0.9),  # duplicate of first
        ],
    ]
    results = merge_entry_relations(rel_lists, target_key="k")
    by_triple = {(r.subject, r.predicate, r.object_entity): r for r in results}

    ab = by_triple[("A", "uses", "B")]
    cd = by_triple[("C", "owns", "D")]

    assert sorted(ab.confidence_history) == pytest.approx([0.8, 0.9])
    assert ab.confidence == pytest.approx(0.9)
    assert cd.confidence_history == pytest.approx([0.6])
    assert cd.confidence == pytest.approx(0.6)


def test_merge_preserves_existing_history_from_dict() -> None:
    """If a relation dict already carries confidence_history, it is respected."""
    # Pre-existing history from a prior merge (e.g. stored in Postgres JSONB)
    triple = ("A", "creates", "B")
    rel_lists = [
        [_rel(*triple, confidence=0.9, history=[0.7, 0.9])],  # already merged
        [_rel(*triple, confidence=0.5)],  # new extraction
    ]
    results = merge_entry_relations(rel_lists, target_key="k")
    assert len(results) == 1
    merged = results[0]
    assert sorted(merged.confidence_history) == pytest.approx([0.5, 0.7, 0.9])
    assert merged.confidence == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# RelationEntry model — confidence_history seeding
# ---------------------------------------------------------------------------


def test_relation_entry_seeds_history_on_construction() -> None:
    """A freshly constructed RelationEntry has confidence_history == [confidence]."""
    entry = RelationEntry(
        subject="Foo",
        predicate="manages",
        object_entity="Bar",
        confidence=0.65,
    )
    assert entry.confidence_history == pytest.approx([0.65])


def test_relation_entry_explicit_history_not_overridden() -> None:
    """Supplying confidence_history explicitly is not overridden by the validator."""
    entry = RelationEntry(
        subject="Foo",
        predicate="manages",
        object_entity="Bar",
        confidence=0.65,
        confidence_history=[0.5, 0.65],
    )
    assert entry.confidence_history == pytest.approx([0.5, 0.65])
