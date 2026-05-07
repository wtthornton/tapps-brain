"""Regression test: RecallResult field-set snapshot (STORY-076.3).

This test locks the field set of :class:`~tapps_brain.models.RecallResult` and
:class:`~tapps_brain.models.RecallDiagnostics` so that EPIC-076 and later
stories can ONLY add fields — never remove them.  Existing brain_recall callers
(Claude Code wiring, AgentForge consumers) depend on backward-compatible
additive-only changes.

**How to update this test legitimately:**
- Add the new field name(s) to the ``_PRE_EPIC_076_*`` frozensets below.
- Do NOT remove field names from those sets — that would signal a breaking
  removal and should be rejected in code review.
"""

from __future__ import annotations

import pytest

from tapps_brain.models import (
    KGEdgeView,
    KGEntityView,
    KGEvidenceView,
    RecallDiagnostics,
    RecallResult,
)

# ---------------------------------------------------------------------------
# Snapshot of fields that existed BEFORE EPIC-076.
# New fields must NOT appear here — they live in _EPIC_076_* sets.
# ---------------------------------------------------------------------------

_PRE_EPIC_076_RECALL_RESULT_FIELDS: frozenset[str] = frozenset(
    {
        "memory_section",
        "memories",
        "token_count",
        "recall_time_ms",
        "truncated",
        "memory_count",
        "hive_memory_count",
        "quality_warning",
        "recall_diagnostics",
    }
)

_PRE_EPIC_076_RECALL_DIAGNOSTICS_FIELDS: frozenset[str] = frozenset(
    {
        "empty_reason",
        "retriever_hits",
        "visible_entries",
        # mentions_* were added by STORY-076.1 but before this test existed
        "mentions_matched",
        "mentions_unmatched",
    }
)

# Fields added by STORY-076.3.
_STORY_076_3_RECALL_RESULT_FIELDS: frozenset[str] = frozenset(
    {"entities", "edges", "evidence"}
)

_STORY_076_3_RECALL_DIAGNOSTICS_FIELDS: frozenset[str] = frozenset(
    {"graph_hits", "dropped_stale", "dropped_low_confidence"}
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _model_fields(cls: type) -> frozenset[str]:
    """Return the set of field names declared on a Pydantic v2 model."""
    return frozenset(cls.model_fields.keys())


# ---------------------------------------------------------------------------
# RecallResult shape tests
# ---------------------------------------------------------------------------


class TestRecallResultShape:
    """RecallResult field-set snapshot."""

    def test_pre_epic076_fields_still_present(self) -> None:
        """All pre-EPIC-076 fields must remain on RecallResult."""
        actual = _model_fields(RecallResult)
        missing = _PRE_EPIC_076_RECALL_RESULT_FIELDS - actual
        assert not missing, (
            f"RecallResult is MISSING pre-EPIC-076 fields (breaking removal): {missing}"
        )

    def test_story_076_3_fields_added(self) -> None:
        """STORY-076.3 must have added entities/edges/evidence to RecallResult."""
        actual = _model_fields(RecallResult)
        missing = _STORY_076_3_RECALL_RESULT_FIELDS - actual
        assert not missing, (
            f"RecallResult is MISSING STORY-076.3 fields (story incomplete): {missing}"
        )

    def test_field_count_grew(self) -> None:
        """RecallResult field count must be strictly larger after STORY-076.3."""
        baseline = len(_PRE_EPIC_076_RECALL_RESULT_FIELDS)
        actual = len(_model_fields(RecallResult))
        assert actual > baseline, (
            f"RecallResult field count should be > {baseline} (pre-EPIC-076 baseline), "
            f"got {actual}"
        )

    def test_default_construction_no_args(self) -> None:
        """RecallResult() must be constructable with no arguments."""
        r = RecallResult()
        assert r.memory_section == ""
        assert r.memories == []
        assert r.entities == []
        assert r.edges == []
        assert r.evidence == []
        assert r.memory_count == 0

    def test_new_fields_default_to_empty_list(self) -> None:
        """entities/edges/evidence default to empty lists — not None."""
        r = RecallResult()
        assert isinstance(r.entities, list)
        assert isinstance(r.edges, list)
        assert isinstance(r.evidence, list)

    def test_existing_fields_still_readable(self) -> None:
        """Spot-check a sample of pre-EPIC-076 fields remain accessible."""
        r = RecallResult(
            memory_section="# Memory",
            memories=[{"key": "k", "value": "v"}],
            token_count=10,
            recall_time_ms=5.0,
            memory_count=1,
            hive_memory_count=0,
            truncated=False,
        )
        assert r.memory_section == "# Memory"
        assert len(r.memories) == 1
        assert r.token_count == 10

    def test_kg_fields_accept_view_objects(self) -> None:
        """entities/edges/evidence fields accept correctly typed list items."""
        entity = KGEntityView(entity_id="e1", surface="MemoryRetriever")
        edge = KGEdgeView(edge_id="ed1", predicate="uses", neighbor_id="n1")
        evidence = KGEvidenceView(evidence_id="ev1", source_type="agent")

        r = RecallResult(entities=[entity], edges=[edge], evidence=[evidence])
        assert r.entities[0].entity_id == "e1"
        assert r.edges[0].predicate == "uses"
        assert r.evidence[0].evidence_id == "ev1"


# ---------------------------------------------------------------------------
# RecallDiagnostics shape tests
# ---------------------------------------------------------------------------


class TestRecallDiagnosticsShape:
    """RecallDiagnostics field-set snapshot."""

    def test_pre_epic076_fields_still_present(self) -> None:
        """All pre-EPIC-076 fields must remain on RecallDiagnostics."""
        actual = _model_fields(RecallDiagnostics)
        missing = _PRE_EPIC_076_RECALL_DIAGNOSTICS_FIELDS - actual
        assert not missing, (
            f"RecallDiagnostics is MISSING pre-EPIC-076 fields (breaking removal): {missing}"
        )

    def test_story_076_3_graph_fields_added(self) -> None:
        """STORY-076.3 must have added graph diagnostic fields."""
        actual = _model_fields(RecallDiagnostics)
        missing = _STORY_076_3_RECALL_DIAGNOSTICS_FIELDS - actual
        assert not missing, (
            f"RecallDiagnostics is MISSING STORY-076.3 fields: {missing}"
        )

    def test_default_construction(self) -> None:
        """RecallDiagnostics() must be constructable with no arguments."""
        d = RecallDiagnostics()
        assert d.empty_reason is None
        assert d.retriever_hits == 0
        assert d.graph_hits == 0
        assert d.dropped_stale == 0
        assert d.dropped_low_confidence == 0
        assert d.mentions_matched == 0
        assert d.mentions_unmatched == 0

    def test_model_validate_from_dict_ignores_no_extras(self) -> None:
        """RecallDiagnostics.model_validate accepts all new + old fields."""
        payload = {
            "empty_reason": None,
            "retriever_hits": 5,
            "visible_entries": 20,
            "mentions_matched": 2,
            "mentions_unmatched": 1,
            "graph_hits": 8,
            "dropped_stale": 1,
            "dropped_low_confidence": 0,
        }
        d = RecallDiagnostics.model_validate(payload)
        assert d.retriever_hits == 5
        assert d.mentions_matched == 2
        assert d.graph_hits == 8
        assert d.dropped_stale == 1


# ---------------------------------------------------------------------------
# KG view model tests
# ---------------------------------------------------------------------------


class TestKGViewModels:
    """Sanity checks for the new KG view model defaults."""

    def test_kg_entity_view_defaults(self) -> None:
        e = KGEntityView(entity_id="abc", surface="SomeName")
        assert e.entity_id == "abc"
        assert e.confidence == pytest.approx(0.0)
        assert e.reason == ""

    def test_kg_edge_view_defaults(self) -> None:
        e = KGEdgeView(edge_id="e1", predicate="uses", neighbor_id="n1")
        assert e.hop == 1
        assert e.score == pytest.approx(0.0)
        assert e.evidence_count == 0

    def test_kg_evidence_view_defaults(self) -> None:
        e = KGEvidenceView()
        assert e.evidence_id == ""
        assert e.quote is None
        assert e.source_uri is None
        assert e.confidence == pytest.approx(0.0)
