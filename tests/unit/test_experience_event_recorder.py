"""Unit tests for tapps_brain.experience — model layer.

TAP-1501 STORY-076.4.
TAP-2705: embedding path tests added.

These tests cover model construction and validation without a database
connection.  Integration tests (requiring TAPPS_TEST_POSTGRES_DSN) live in
tests/integration/test_experience_event_recorder.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest

from tapps_brain.experience import (
    AsyncExperienceEventRecorder,
    EdgeSpec,
    EntitySpec,
    EvidenceSpec,
    ExperienceEvent,
    ExperienceEventRecorder,
    ExperienceResult,
    MemorySpec,
)

# ---------------------------------------------------------------------------
# ExperienceEvent model
# ---------------------------------------------------------------------------


class TestExperienceEventModel:
    """ExperienceEvent Pydantic model construction."""

    def test_minimal_construction(self) -> None:
        """event_type is the only required field."""
        ev = ExperienceEvent(event_type="workflow_completed")
        assert ev.event_type == "workflow_completed"
        assert ev.utility_score == pytest.approx(0.0)
        assert ev.payload == {}
        assert ev.session_id is None
        assert ev.memory is None
        assert ev.entities == []
        assert ev.edges == []
        assert ev.evidence == []

    def test_utility_score_clamped(self) -> None:
        """utility_score must be in [0, 1]."""
        ev = ExperienceEvent(event_type="tool_called", utility_score=0.75)
        assert ev.utility_score == pytest.approx(0.75)

    def test_utility_score_below_zero_rejected(self) -> None:
        with pytest.raises(Exception):
            ExperienceEvent(event_type="x", utility_score=-0.1)

    def test_utility_score_above_one_rejected(self) -> None:
        with pytest.raises(Exception):
            ExperienceEvent(event_type="x", utility_score=1.1)

    def test_payload_arbitrary_dict(self) -> None:
        ev = ExperienceEvent(event_type="approach_failed", payload={"attempt": 3, "reason": "oom"})
        assert ev.payload["attempt"] == 3

    def test_all_fields_populated(self) -> None:
        ev = ExperienceEvent(
            event_type="memory_recalled",
            subject_key="some-memory-key",
            utility_score=0.5,
            payload={"hits": 10},
            session_id="sess-123",
            workflow_run_id="wf-456",
            memory=MemorySpec(key="k", value="v"),
            entities=[EntitySpec(entity_type="service", canonical_name="AuthService")],
            edges=[
                EdgeSpec(
                    subject_entity_id="uuid-1",
                    predicate="uses",
                    object_entity_id="uuid-2",
                )
            ],
            evidence=[EvidenceSpec(edge_id="uuid-3", source_type="agent")],
        )
        assert ev.subject_key == "some-memory-key"
        assert ev.session_id == "sess-123"
        assert len(ev.entities) == 1
        assert len(ev.edges) == 1
        assert len(ev.evidence) == 1
        assert ev.memory is not None


# ---------------------------------------------------------------------------
# EntitySpec model
# ---------------------------------------------------------------------------


class TestEntitySpecModel:
    """EntitySpec defaults and validation."""

    def test_required_fields(self) -> None:
        spec = EntitySpec(entity_type="module", canonical_name="RecallOrchestrator")
        assert spec.entity_type == "module"
        assert spec.canonical_name == "RecallOrchestrator"
        assert spec.aliases == []
        assert spec.metadata == {}
        assert spec.confidence == pytest.approx(0.6)
        assert spec.source == "agent"

    def test_confidence_must_be_zero_to_one(self) -> None:
        with pytest.raises(Exception):
            EntitySpec(entity_type="x", canonical_name="y", confidence=1.5)


# ---------------------------------------------------------------------------
# EdgeSpec model
# ---------------------------------------------------------------------------


class TestEdgeSpecModel:
    """EdgeSpec defaults and validation."""

    def test_required_fields(self) -> None:
        spec = EdgeSpec(
            subject_entity_id="uuid-a",
            predicate="depends_on",
            object_entity_id="uuid-b",
        )
        assert spec.predicate == "depends_on"
        assert spec.edge_class is None
        assert spec.layer is None
        assert spec.confidence == pytest.approx(0.6)

    def test_optional_fields(self) -> None:
        spec = EdgeSpec(
            subject_entity_id="uuid-a",
            predicate="uses",
            object_entity_id="uuid-b",
            edge_class="runtime",
            layer="pattern",
            profile_name="default",
            confidence=0.9,
            metadata={"weight": 1},
        )
        assert spec.edge_class == "runtime"
        assert spec.metadata == {"weight": 1}


# ---------------------------------------------------------------------------
# EvidenceSpec model
# ---------------------------------------------------------------------------


class TestEvidenceSpecModel:
    """EvidenceSpec defaults and validation."""

    def test_defaults(self) -> None:
        # TAP-2868: EvidenceSpec requires exactly one attachment, so defaults
        # are asserted with an edge_id set (bare EvidenceSpec() now raises).
        spec = EvidenceSpec(edge_id="uuid-edge")
        assert spec.entity_id is None
        assert spec.source_type == "agent"
        assert spec.confidence == pytest.approx(1.0)
        assert spec.utility_score is None

    def test_with_edge_id(self) -> None:
        spec = EvidenceSpec(edge_id="uuid-edge", quote="auth uses cache")
        assert spec.edge_id == "uuid-edge"
        assert spec.quote == "auth uses cache"

    def test_confidence_bounds(self) -> None:
        with pytest.raises(Exception):
            EvidenceSpec(confidence=1.5, edge_id="uuid-edge")

    def test_requires_exactly_one_attachment(self) -> None:
        # TAP-2868: neither attachment (the AgentForge payload that 500'd) and
        # both attachments are rejected at the model layer.
        with pytest.raises(Exception):
            EvidenceSpec(source_type="agent")
        with pytest.raises(Exception):
            EvidenceSpec(edge_id="uuid-edge", entity_id="uuid-entity")


# ---------------------------------------------------------------------------
# MemorySpec model
# ---------------------------------------------------------------------------


class TestMemorySpecModel:
    """MemorySpec defaults."""

    def test_required_fields(self) -> None:
        spec = MemorySpec(key="my-key", value="my value")
        assert spec.tier == "pattern"
        assert spec.confidence == pytest.approx(0.8)
        assert spec.tags == []
        assert spec.agent_scope == "private"


# ---------------------------------------------------------------------------
# ExperienceResult model
# ---------------------------------------------------------------------------


class TestExperienceResultModel:
    """ExperienceResult construction."""

    def test_defaults(self) -> None:
        r = ExperienceResult(event_id="uuid-ev")
        assert r.event_id == "uuid-ev"
        assert r.memory_key is None
        assert r.entity_ids == []
        assert r.edge_ids == []
        assert r.evidence_ids == []

    def test_with_all_ids(self) -> None:
        r = ExperienceResult(
            event_id="ev-1",
            memory_key="mem-k",
            entity_ids=["ent-1", "ent-2"],
            edge_ids=["edge-1"],
            evidence_ids=["ev-id-1"],
        )
        assert len(r.entity_ids) == 2
        assert r.memory_key == "mem-k"


# ---------------------------------------------------------------------------
# AsyncExperienceEventRecorder constructor
# ---------------------------------------------------------------------------


class TestAsyncRecorderConstructor:
    """AsyncExperienceEventRecorder wraps a sync recorder."""

    def test_wraps_recorder(self) -> None:
        # Construct with a mock that provides the expected interface.
        class _FakeRecorder:
            def record(self, event: ExperienceEvent) -> ExperienceResult:
                return ExperienceResult(event_id="fake-id")

        async_recorder = AsyncExperienceEventRecorder(_FakeRecorder())  # type: ignore[arg-type]
        assert async_recorder._recorder is not None

    def test_async_record_calls_sync(self) -> None:
        """AsyncExperienceEventRecorder.record delegates to sync recorder."""
        import asyncio

        class _FakeRecorder:
            called = False

            def record(self, event: ExperienceEvent) -> ExperienceResult:
                _FakeRecorder.called = True
                return ExperienceResult(event_id="from-fake")

        async_recorder = AsyncExperienceEventRecorder(_FakeRecorder())  # type: ignore[arg-type]

        async def _run() -> ExperienceResult:
            return await async_recorder.record(ExperienceEvent(event_type="test"))

        result = asyncio.run(_run())
        assert result.event_id == "from-fake"
        assert _FakeRecorder.called


# ---------------------------------------------------------------------------
# TAP-2705: ExperienceEventRecorder embedding path
# ---------------------------------------------------------------------------


def _make_fake_cm(memory_key: str = "test-key") -> MagicMock:
    """Return a PostgresConnectionManager mock that records cursor.execute calls."""
    cur = MagicMock()
    cur.fetchone.return_value = (memory_key,)
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    cm = MagicMock()
    cm.project_context.return_value.__enter__ = MagicMock(return_value=conn)
    cm.project_context.return_value.__exit__ = MagicMock(return_value=False)
    return cm


def _make_fake_provider(
    embedding: list[float] | None = None,
    model_id: str = "all-MiniLM-L6-v2",
) -> MagicMock:
    """Return a minimal embedding-provider mock."""
    provider = MagicMock()
    provider.embed.return_value = embedding if embedding is not None else [0.1] * 384
    provider.model_id = model_id
    return provider


class TestExperienceEventRecorderEmbedding:
    """TAP-2705: experience-event INSERT must persist the embedding column."""

    def test_record_passes_embedding_to_insert(self) -> None:
        """record() includes a pgvector string and model_id in the INSERT params."""
        emb_vec = [float(i) / 384 for i in range(384)]
        provider = _make_fake_provider(embedding=emb_vec, model_id="test-model")
        cm = _make_fake_cm()
        recorder = ExperienceEventRecorder(
            cm,  # type: ignore[arg-type]
            project_id="proj",
            brain_id="brain",
            agent_id="agent",
            embedding_provider=provider,
        )

        event = ExperienceEvent(
            event_type="workflow_completed",
            memory=MemorySpec(key="success-abc", value="task succeeded"),
        )
        result = recorder.record(event)

        # memory_key comes from the mock fetchone return value ("test-key").
        assert result.memory_key == "test-key"
        provider.embed.assert_called_once_with("task succeeded")

        # Locate the _INSERT_MEMORY_SQL execute call — it is the second
        # cur.execute call (first is _INSERT_EVENT_SQL).
        conn = cm.project_context.return_value.__enter__.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        assert cur.execute.call_count >= 2
        mem_call_args = cur.execute.call_args_list[1]
        params = mem_call_args[0][1]  # positional: (sql, params)
        # params[-1] is the pgvector string; params[-2] is the model_id.
        assert params[-2] == "test-model"
        assert params[-1] is not None and params[-1].startswith("[")

    def test_record_no_provider_sends_null_embedding(self) -> None:
        """record() sends None for embedding when provider is absent (fail-soft)."""
        cm = _make_fake_cm()
        recorder = ExperienceEventRecorder(
            cm,  # type: ignore[arg-type]
            project_id="proj",
            brain_id="brain",
            embedding_provider=None,
        )

        event = ExperienceEvent(
            event_type="approach_failed",
            memory=MemorySpec(key="failure-xyz", value="thing failed"),
        )
        recorder.record(event)

        conn = cm.project_context.return_value.__enter__.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        params = cur.execute.call_args_list[1][0][1]
        assert params[-2] is None  # embedding_model_id
        assert params[-1] is None  # embedding

    def test_record_many_embeds_per_event(self) -> None:
        """record_many() computes one embedding per event memory, outside the transaction."""
        emb_vec = [0.5] * 384
        provider = _make_fake_provider(embedding=emb_vec, model_id="m")
        cm = _make_fake_cm()
        recorder = ExperienceEventRecorder(
            cm,  # type: ignore[arg-type]
            project_id="proj",
            brain_id="brain",
            embedding_provider=provider,
        )

        events = [
            ExperienceEvent(
                event_type="workflow_completed",
                memory=MemorySpec(key=f"k-{i}", value=f"value {i}"),
            )
            for i in range(3)
        ]
        recorder.record_many(events)

        # embed() must be called once per event (3 events × 1 memory each).
        assert provider.embed.call_count == 3
        assert provider.embed.call_args_list == [
            call("value 0"),
            call("value 1"),
            call("value 2"),
        ]

    def test_compute_embedding_fail_soft(self) -> None:
        """_compute_embedding returns (None, None) when embed() raises."""
        provider = MagicMock()
        provider.embed.side_effect = RuntimeError("model not loaded")
        cm = _make_fake_cm()
        recorder = ExperienceEventRecorder(
            cm,  # type: ignore[arg-type]
            project_id="proj",
            brain_id="brain",
            embedding_provider=provider,
        )
        pgv, mid = recorder._compute_embedding("anything")
        assert pgv is None
        assert mid is None
