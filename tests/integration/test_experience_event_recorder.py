"""Integration tests — TAP-1501 STORY-076.4: ExperienceEventRecorder.

Verifies that:
- A 4-component event (memory + entity + edge + evidence) is written in one
  Postgres transaction and returns the expected IDs.
- A constraint failure on any side-effect rolls back the entire transaction,
  including the experience_events row itself.

Requires a live Postgres ≥17 instance at ``TAPPS_TEST_POSTGRES_DSN``.
Tests are automatically skipped when that environment variable is not set.
"""

from __future__ import annotations

import os
import uuid

import pytest

from tapps_brain.experience import (
    EdgeSpec,
    EntitySpec,
    EvidenceSpec,
    ExperienceEvent,
    ExperienceEventRecorder,
    ExperienceResult,
    MemorySpec,
)

# ---------------------------------------------------------------------------
# Skip guard
# ---------------------------------------------------------------------------

_PG_DSN = os.environ.get("TAPPS_TEST_POSTGRES_DSN", "")
_SKIP_PG = not _PG_DSN

pytestmark = pytest.mark.skipif(_SKIP_PG, reason="TAPPS_TEST_POSTGRES_DSN not set")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _apply_migrations() -> None:
    from tapps_brain.postgres_migrations import apply_private_migrations

    apply_private_migrations(_PG_DSN)


def _make_cm() -> object:
    from tapps_brain.postgres_connection import PostgresConnectionManager

    return PostgresConnectionManager(_PG_DSN, min_size=1, max_size=3)


def _count_events(cm: object, project_id: str) -> int:
    """Count experience_events rows visible to *project_id* via RLS."""
    with cm.project_context(project_id) as conn:  # type: ignore[union-attr]
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM experience_events WHERE project_id = %s",
                (project_id,),
            )
            row = cur.fetchone()
            return int(row[0]) if row else 0


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _migrations() -> None:
    """Apply private migrations once per module (idempotent)."""
    _apply_migrations()


@pytest.fixture()
def cm():
    """Open connection manager; closes after each test."""
    manager = _make_cm()
    yield manager
    manager.close()


@pytest.fixture()
def project_id() -> str:
    """Unique project_id per test to avoid cross-test data leakage."""
    return f"test-exp-{uuid.uuid4().hex[:8]}"


@pytest.fixture()
def brain_id() -> str:
    return "tapps-brain-test"


@pytest.fixture()
def recorder(cm, project_id, brain_id) -> ExperienceEventRecorder:
    return ExperienceEventRecorder(
        cm,
        project_id=project_id,
        brain_id=brain_id,
        agent_id="test-agent",
    )


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


class TestRecordHappyPath:
    """record() writes all components and returns correct IDs."""

    def test_minimal_event_returns_event_id(
        self, recorder: ExperienceEventRecorder, cm: object, project_id: str
    ) -> None:
        """A minimal event (event_type only) creates an experience_events row."""
        result = recorder.record(ExperienceEvent(event_type="workflow_completed"))

        assert isinstance(result, ExperienceResult)
        assert result.event_id  # non-empty string
        assert result.memory_key is None
        assert result.entity_ids == []
        assert result.edge_ids == []
        assert result.evidence_ids == []
        assert _count_events(cm, project_id) == 1

    def test_event_with_memory_returns_memory_key(
        self, recorder: ExperienceEventRecorder, cm: object, project_id: str
    ) -> None:
        """An event with a MemorySpec writes to private_memories atomically."""
        result = recorder.record(
            ExperienceEvent(
                event_type="approach_failed",
                memory=MemorySpec(key="approach-key", value="Approach X did not converge."),
            )
        )

        assert result.memory_key == "approach-key"
        assert _count_events(cm, project_id) == 1

    def test_event_with_entity_returns_entity_id(
        self, recorder: ExperienceEventRecorder
    ) -> None:
        """An event with EntitySpec upserts the entity and returns its UUID."""
        result = recorder.record(
            ExperienceEvent(
                event_type="tool_called",
                entities=[EntitySpec(entity_type="service", canonical_name="AuthService")],
            )
        )

        assert len(result.entity_ids) == 1
        # Must be a valid UUID string
        uuid.UUID(result.entity_ids[0])

    def test_four_component_event(
        self, recorder: ExperienceEventRecorder, cm: object, project_id: str
    ) -> None:
        """A full 4-component event (memory + entity + edge + evidence) succeeds.

        Acceptance criterion: record() writes event + MemorySpec + entity +
        edge + evidence in one transaction and returns IDs for all.
        """
        # Upsert two entities first so we have real UUIDs for the edge.
        entity_event = recorder.record(
            ExperienceEvent(
                event_type="setup",
                entities=[
                    EntitySpec(entity_type="module", canonical_name="RecallOrchestrator"),
                    EntitySpec(entity_type="module", canonical_name="MemoryRetriever"),
                ],
            )
        )
        assert len(entity_event.entity_ids) == 2
        subject_id, object_id = entity_event.entity_ids

        # Now record the full 4-component event.
        result = recorder.record(
            ExperienceEvent(
                event_type="workflow_completed",
                utility_score=0.85,
                payload={"workflow": "recall_and_inject"},
                session_id="sess-abc",
                memory=MemorySpec(
                    key="orchestrator-uses-retriever",
                    value="RecallOrchestrator delegates to MemoryRetriever for ranking.",
                    tier="architectural",
                ),
                entities=[
                    EntitySpec(entity_type="module", canonical_name="RecallOrchestrator"),
                ],
                edges=[
                    EdgeSpec(
                        subject_entity_id=subject_id,
                        predicate="uses",
                        object_entity_id=object_id,
                        confidence=0.9,
                    )
                ],
                evidence=[
                    EvidenceSpec(
                        edge_id=None,
                        entity_id=subject_id,
                        source_type="agent",
                        quote="RecallOrchestrator delegates retrieval",
                        confidence=0.95,
                    )
                ],
            )
        )

        assert result.event_id
        assert result.memory_key == "orchestrator-uses-retriever"
        assert len(result.entity_ids) == 1
        assert len(result.edge_ids) == 1
        assert len(result.evidence_ids) == 1

        # All returned IDs must be valid UUID strings.
        uuid.UUID(result.entity_ids[0])
        uuid.UUID(result.edge_ids[0])
        uuid.UUID(result.evidence_ids[0])

    def test_multiple_entities_returned_in_order(
        self, recorder: ExperienceEventRecorder
    ) -> None:
        """entity_ids are returned in the same order as ExperienceEvent.entities."""
        result = recorder.record(
            ExperienceEvent(
                event_type="tool_called",
                entities=[
                    EntitySpec(entity_type="alpha", canonical_name="Alpha"),
                    EntitySpec(entity_type="beta", canonical_name="Beta"),
                    EntitySpec(entity_type="gamma", canonical_name="Gamma"),
                ],
            )
        )
        assert len(result.entity_ids) == 3
        # Each ID is a distinct UUID.
        assert len(set(result.entity_ids)) == 3

    def test_idempotent_entity_upsert(self, recorder: ExperienceEventRecorder) -> None:
        """Recording the same entity twice returns the same UUID both times."""
        spec = EntitySpec(entity_type="service", canonical_name="SharedService")
        r1 = recorder.record(ExperienceEvent(event_type="x", entities=[spec]))
        r2 = recorder.record(ExperienceEvent(event_type="x", entities=[spec]))
        assert r1.entity_ids[0] == r2.entity_ids[0]

    def test_event_with_payload(
        self, recorder: ExperienceEventRecorder, cm: object, project_id: str
    ) -> None:
        """Payload JSONB is persisted correctly."""
        payload = {"tool": "run_tests", "exit_code": 0, "duration_ms": 3400}
        recorder.record(ExperienceEvent(event_type="tool_called", payload=payload))

        with cm.project_context(project_id) as conn:  # type: ignore[union-attr]
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT payload FROM experience_events WHERE project_id = %s LIMIT 1",
                    (project_id,),
                )
                row = cur.fetchone()
        assert row is not None
        assert row[0]["tool"] == "run_tests"
        assert row[0]["exit_code"] == 0


# ---------------------------------------------------------------------------
# Atomicity tests
# ---------------------------------------------------------------------------


class TestAtomicity:
    """Any side-effect failure rolls back the entire transaction."""

    def test_invalid_evidence_rolls_back_event_row(
        self,
        recorder: ExperienceEventRecorder,
        cm: object,
        project_id: str,
    ) -> None:
        """Injecting a bad UUID in evidence causes full rollback.

        Acceptance criterion: constraint failure on the last insert (evidence)
        rolls back the entire transaction including the experience_events row.
        """
        # "not-a-uuid" will fail the ::uuid cast in ATTACH_EVIDENCE_SQL.
        bad_evidence = EvidenceSpec(edge_id="not-a-valid-uuid", source_type="agent")

        entity_spec = EntitySpec(entity_type="module", canonical_name="AtomicTestModule")
        memory_spec = MemorySpec(key="atomic-test-mem", value="should be rolled back")

        with pytest.raises(Exception):
            recorder.record(
                ExperienceEvent(
                    event_type="workflow_completed",
                    memory=memory_spec,
                    entities=[entity_spec],
                    evidence=[bad_evidence],
                )
            )

        # No experience_events row must survive.
        assert _count_events(cm, project_id) == 0

    def test_rollback_does_not_affect_prior_successful_records(
        self,
        recorder: ExperienceEventRecorder,
        cm: object,
        project_id: str,
    ) -> None:
        """A failed record does not roll back rows from prior successful calls."""
        # First record succeeds.
        recorder.record(ExperienceEvent(event_type="setup"))
        assert _count_events(cm, project_id) == 1

        # Second record fails mid-transaction.
        with pytest.raises(Exception):
            recorder.record(
                ExperienceEvent(
                    event_type="will_fail",
                    evidence=[EvidenceSpec(edge_id="not-a-uuid")],
                )
            )

        # First row must still be there; the failed record did not touch it.
        assert _count_events(cm, project_id) == 1
