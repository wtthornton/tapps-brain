"""Regression tests for EdgeSpec key/ref shorthand (TAP-3248 / EPIC-078)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tapps_brain.experience import (
    EdgeSpec,
    EntitySpec,
    ExperienceEvent,
    _build_entity_lookup,
    _resolve_edge_endpoint,
)


class TestEdgeSpecKeyShorthand:
    def test_uuid_payload_unchanged(self) -> None:
        spec = EdgeSpec(
            subject_entity_id="00000000-0000-0000-0000-000000000001",
            predicate="uses",
            object_entity_id="00000000-0000-0000-0000-000000000002",
        )
        assert spec.subject_entity_id.endswith("001")
        assert spec.object_entity_id.endswith("002")

    def test_subject_object_key_accepted(self) -> None:
        spec = EdgeSpec(
            subject_key="agentforge",
            object_key="task-123",
            predicate="completed_task",
        )
        assert spec.subject_key == "agentforge"
        assert spec.object_key == "task-123"
        assert spec.subject_entity_id is None

    def test_subject_object_ref_shorthand(self) -> None:
        spec = EdgeSpec(
            subject_ref={"type": "agent", "id": "ralph"},
            object_ref={"entity_type": "task", "canonical_name": "task-123"},
            predicate="completed_task",
        )
        assert spec.subject_ref is not None
        assert spec.object_ref is not None

    def test_predicate_only_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EdgeSpec(**{"predicate": "agent_solved_problem", "confidence": 0.5})


class TestEdgeEndpointResolution:
    def test_resolve_from_same_event_entities(self) -> None:
        entities = [
            EntitySpec(**{"key": "agentforge", "type": "agent"}),
            EntitySpec(**{"key": "task-123", "type": "task"}),
        ]
        typed, by_name = _build_entity_lookup(entities, ["uuid-agent", "uuid-task"])
        subject = _resolve_edge_endpoint(None, "agentforge", None, typed, by_name)
        obj = _resolve_edge_endpoint(None, "task-123", None, typed, by_name)
        assert subject == "uuid-agent"
        assert obj == "uuid-task"

    def test_resolve_typed_ref_disambiguates(self) -> None:
        entities = [
            EntitySpec(entity_type="agent", canonical_name="ralph"),
            EntitySpec(entity_type="task", canonical_name="ralph"),
        ]
        typed, by_name = _build_entity_lookup(entities, ["uuid-agent", "uuid-task"])
        resolved = _resolve_edge_endpoint(
            None,
            None,
            {"entity_type": "task", "canonical_name": "ralph"},
            typed,
            by_name,
        )
        assert resolved == "uuid-task"
        assert by_name["ralph"] == "uuid-task"

    def test_lookup_skips_failed_upsert_slots(self) -> None:
        """Partial upsert failures must not realign later entities onto earlier UUIDs."""
        entities = [
            EntitySpec(entity_type="agent", canonical_name="a"),
            EntitySpec(entity_type="task", canonical_name="b"),
            EntitySpec(entity_type="tool", canonical_name="c"),
        ]
        typed, by_name = _build_entity_lookup(entities, ["uuid-a", None, "uuid-c"])
        assert typed[("agent", "a")] == "uuid-a"
        assert typed[("tool", "c")] == "uuid-c"
        assert ("task", "b") not in typed
        assert by_name["c"] == "uuid-c"
        assert "b" not in by_name

    def test_agentforge_task_completion_event_shape(self) -> None:
        event = ExperienceEvent(
            event_type="workflow_completed",
            entities=[
                {"key": "task-123", "type": "task"},
                {"key": "agentforge", "type": "agent"},
            ],
            edges=[
                {
                    "subject_key": "agentforge",
                    "object_key": "task-123",
                    "predicate": "completed_task",
                }
            ],
        )
        assert len(event.entities) == 2
        assert event.edges[0].subject_key == "agentforge"
        assert event.edges[0].object_key == "task-123"
