"""Tests for auto-recall protocols and models (EPIC-003)."""

from __future__ import annotations

import threading
from typing import Any

import pytest

from tapps_brain._protocols import CaptureHookLike, RecallHookLike
from tapps_brain.models import MemoryScope, MemoryTier, RecallResult
from tapps_brain.recall import RecallConfig, RecallOrchestrator
from tapps_brain.store import MemoryStore


# ---------------------------------------------------------------------------
# STORY-003.1: Protocol and model tests
# ---------------------------------------------------------------------------


class TestRecallResult:
    """Tests for RecallResult model."""

    def test_empty_result(self) -> None:
        result = RecallResult()
        assert result.memory_section == ""
        assert result.memories == []
        assert result.token_count == 0
        assert result.recall_time_ms == 0.0
        assert result.truncated is False
        assert result.memory_count == 0

    def test_populated_result(self) -> None:
        memories = [
            {"key": "tech-stack", "confidence": 0.9, "tier": "architectural", "score": 0.8, "stale": False}
        ]
        result = RecallResult(
            memory_section="### Project Memory\n- **tech-stack**: We use Python",
            memories=memories,
            token_count=42,
            recall_time_ms=3.5,
            truncated=False,
            memory_count=1,
        )
        assert result.memory_section.startswith("### Project Memory")
        assert len(result.memories) == 1
        assert result.token_count == 42
        assert result.recall_time_ms == 3.5
        assert result.memory_count == 1

    def test_serialization_round_trip(self) -> None:
        result = RecallResult(
            memory_section="test",
            memories=[{"key": "k1"}],
            token_count=10,
            recall_time_ms=1.0,
            memory_count=1,
        )
        data = result.model_dump()
        restored = RecallResult.model_validate(data)
        assert restored == result


class _StubRecallHook:
    """Minimal stub that satisfies RecallHookLike."""

    def recall(self, message: str, **kwargs: Any) -> RecallResult:
        return RecallResult(memory_section=f"recalled: {message}", memory_count=0)


class _StubCaptureHook:
    """Minimal stub that satisfies CaptureHookLike."""

    def capture(self, response: str, **kwargs: Any) -> list[str]:
        return ["key-1"]


class TestRecallHookProtocol:
    """Tests for RecallHookLike protocol structural subtyping."""

    def test_stub_satisfies_protocol(self) -> None:
        hook = _StubRecallHook()
        assert isinstance(hook, RecallHookLike)

    def test_stub_is_callable(self) -> None:
        hook = _StubRecallHook()
        result = hook.recall("hello")
        assert isinstance(result, RecallResult)
        assert result.memory_section == "recalled: hello"


class TestCaptureHookProtocol:
    """Tests for CaptureHookLike protocol structural subtyping."""

    def test_stub_satisfies_protocol(self) -> None:
        hook = _StubCaptureHook()
        assert isinstance(hook, CaptureHookLike)

    def test_stub_is_callable(self) -> None:
        hook = _StubCaptureHook()
        keys = hook.capture("some response")
        assert keys == ["key-1"]

    def test_non_conforming_object_rejected(self) -> None:
        class _Bad:
            pass

        assert not isinstance(_Bad(), RecallHookLike)
        assert not isinstance(_Bad(), CaptureHookLike)


# ---------------------------------------------------------------------------
# STORY-003.2: RecallOrchestrator tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path):
    """Create a MemoryStore with sample entries."""
    s = MemoryStore(tmp_path)
    s.save(key="tech-stack", value="We use Python 3.12 with FastAPI", tier="architectural", source="human")
    s.save(key="test-framework", value="We use pytest for all testing", tier="pattern", source="human")
    s.save(key="deploy-target", value="We deploy to AWS ECS Fargate", tier="architectural", source="agent")
    s.save(key="session-note", value="Discussed refactoring the auth module", tier="context", source="agent", scope="session")
    s.save(key="branch-feature", value="Working on feature-x branch auth rewrite", tier="context", source="agent", scope="branch", branch="feature-x")
    return s


class TestRecallOrchestrator:
    """Tests for RecallOrchestrator.recall()."""

    def test_recall_returns_result(self, store):
        orch = RecallOrchestrator(store)
        result = orch.recall("what is our tech stack?")
        assert isinstance(result, RecallResult)
        assert result.recall_time_ms > 0

    def test_recall_finds_relevant_memories(self, store):
        orch = RecallOrchestrator(store)
        result = orch.recall("python tech stack")
        assert result.memory_count > 0
        assert result.memory_section != ""

    def test_recall_empty_message_returns_empty(self, store):
        orch = RecallOrchestrator(store)
        result = orch.recall("")
        assert result.memory_count == 0
        assert result.memory_section == ""

    def test_recall_no_matches_returns_empty(self, store):
        orch = RecallOrchestrator(store)
        result = orch.recall("quantum computing blockchain")
        assert result.memory_count == 0

    def test_recall_low_engagement_returns_empty(self, store):
        cfg = RecallConfig(engagement_level="low")
        orch = RecallOrchestrator(store, config=cfg)
        result = orch.recall("tech stack")
        assert result.memory_count == 0

    def test_recall_respects_dedupe_window(self, store):
        cfg = RecallConfig(dedupe_window=["tech-stack"])
        orch = RecallOrchestrator(store, config=cfg)
        result = orch.recall("python tech stack")
        keys = [m.get("key") for m in result.memories]
        assert "tech-stack" not in keys

    def test_recall_respects_scope_filter(self, store):
        cfg = RecallConfig(scope_filter=MemoryScope.project)
        orch = RecallOrchestrator(store, config=cfg)
        result = orch.recall("auth module refactoring session")
        keys = [m.get("key") for m in result.memories]
        assert "session-note" not in keys

    def test_recall_respects_tier_filter(self, store):
        cfg = RecallConfig(tier_filter=MemoryTier.architectural)
        orch = RecallOrchestrator(store, config=cfg)
        result = orch.recall("python testing deploy")
        keys = [m.get("key") for m in result.memories]
        # Only architectural entries should remain
        for key in keys:
            entry = store.get(key)
            if entry:
                assert entry.tier == MemoryTier.architectural

    def test_recall_respects_branch_filter(self, store):
        cfg = RecallConfig(branch="feature-x")
        orch = RecallOrchestrator(store, config=cfg)
        result = orch.recall("auth rewrite branch feature")
        # branch-scoped entries for other branches should be excluded
        keys = [m.get("key") for m in result.memories]
        for key in keys:
            entry = store.get(key)
            if entry and entry.scope == MemoryScope.branch:
                assert entry.branch == "feature-x"

    def test_recall_token_budget_enforced(self, store):
        cfg = RecallConfig(max_tokens=10)  # Very small budget
        orch = RecallOrchestrator(store, config=cfg)
        result = orch.recall("python tech stack testing deploy")
        # With a 10-token budget, at most 1 entry can fit
        assert result.memory_count <= 1

    def test_recall_per_call_override(self, store):
        orch = RecallOrchestrator(store)
        result = orch.recall("tech stack", engagement_level="low")
        assert result.memory_count == 0

    def test_recall_thread_safety(self, store):
        orch = RecallOrchestrator(store)
        results = []
        errors = []

        def do_recall(msg):
            try:
                r = orch.recall(msg)
                results.append(r)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=do_recall, args=(f"query {i}",))
            for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 5
        for r in results:
            assert isinstance(r, RecallResult)

    def test_orchestrator_satisfies_recall_hook_protocol(self, store):
        orch = RecallOrchestrator(store)
        assert isinstance(orch, RecallHookLike)


# ---------------------------------------------------------------------------
# STORY-003.3: Capture pipeline tests
# ---------------------------------------------------------------------------


class TestCapturePipeline:
    """Tests for RecallOrchestrator.capture()."""

    def test_capture_extracts_facts(self, store):
        orch = RecallOrchestrator(store)
        response = "We decided to use PostgreSQL for the database layer. Key decision: all APIs must be versioned."
        keys = orch.capture(response)
        assert len(keys) > 0
        # Verify entries exist in store
        for key in keys:
            entry = store.get(key)
            assert entry is not None

    def test_capture_empty_response(self, store):
        orch = RecallOrchestrator(store)
        keys = orch.capture("")
        assert keys == []

    def test_capture_whitespace_response(self, store):
        orch = RecallOrchestrator(store)
        keys = orch.capture("   \n  ")
        assert keys == []

    def test_capture_no_duplicates(self, store):
        orch = RecallOrchestrator(store)
        response = "We decided to use Redis for caching."
        keys1 = orch.capture(response)
        keys2 = orch.capture(response)
        # Second capture should not create duplicates
        assert keys2 == []

    def test_capture_custom_source(self, store):
        orch = RecallOrchestrator(store)
        response = "We decided to use gRPC for internal services."
        keys = orch.capture(response, source="human")
        assert len(keys) > 0
        for key in keys:
            entry = store.get(key)
            assert entry is not None
            assert entry.source.value == "human"

    def test_orchestrator_satisfies_capture_hook_protocol(self, store):
        orch = RecallOrchestrator(store)
        assert isinstance(orch, CaptureHookLike)


# ---------------------------------------------------------------------------
# STORY-003.4: MemoryStore convenience method tests
# ---------------------------------------------------------------------------


class TestAutoRecall:
    """Tests for MemoryStore.recall() convenience method."""

    def test_store_recall_returns_result(self, store):
        result = store.recall("what is our tech stack?")
        assert isinstance(result, RecallResult)

    def test_store_recall_finds_memories(self, store):
        result = store.recall("python tech stack")
        assert result.memory_count > 0

    def test_store_recall_reuses_orchestrator(self, store):
        store.recall("first call")
        store.recall("second call")
        # Verify the orchestrator was cached (no error, same instance)
        assert hasattr(store, "_recall_orchestrator")

    def test_store_recall_thread_safe(self, store):
        results = []
        errors = []

        def do_recall():
            try:
                r = store.recall("tech stack")
                results.append(r)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=do_recall) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 5

    def test_store_recall_accepts_kwargs(self, store):
        result = store.recall("tech stack", engagement_level="low")
        assert result.memory_count == 0
