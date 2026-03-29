"""Tests for auto-recall protocols and models (EPIC-003)."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

from tapps_brain._protocols import CaptureHookLike, RecallHookLike
from tapps_brain.models import MemoryScope, MemoryTier, RecallResult
from tapps_brain.recall import RecallConfig, RecallOrchestrator
from tapps_brain.recall_diagnostics import RECALL_EMPTY_POST_FILTER
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
            {
                "key": "tech-stack",
                "confidence": 0.9,
                "tier": "architectural",
                "score": 0.8,
                "stale": False,
            }
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
def store(tmp_path: Path) -> Generator[MemoryStore, None, None]:
    """Create a MemoryStore with sample entries."""
    s = MemoryStore(tmp_path)
    s.save(
        key="tech-stack",
        value="We use Python 3.12 with FastAPI",
        tier="architectural",
        source="human",
    )
    s.save(
        key="test-framework", value="We use pytest for all testing", tier="pattern", source="human"
    )
    s.save(
        key="deploy-target",
        value="We deploy to AWS ECS Fargate",
        tier="architectural",
        source="agent",
    )
    s.save(
        key="session-note",
        value="Discussed refactoring the auth module",
        tier="context",
        source="agent",
        scope="session",
    )
    s.save(
        key="branch-feature",
        value="Working on feature-x branch auth rewrite",
        tier="context",
        source="agent",
        scope="branch",
        branch="feature-x",
    )
    yield s
    s.close()


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

    def test_post_filter_excluded_sets_recall_diagnostics(self, store):
        cfg = RecallConfig(scope_filter=MemoryScope.session)
        orch = RecallOrchestrator(store, config=cfg)
        result = orch.recall("Python 3.12 with FastAPI")
        assert result.memory_count == 0
        assert result.recall_diagnostics is not None
        assert result.recall_diagnostics.empty_reason == RECALL_EMPTY_POST_FILTER

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

        threads = [threading.Thread(target=do_recall, args=(f"query {i}",)) for i in range(5)]
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
        response = (
            "We decided to use PostgreSQL for the database layer."
            " Key decision: all APIs must be versioned."
        )
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
        orch.capture(response)
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


# ---------------------------------------------------------------------------
# STORY-006.4: Graph-based recall boost tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def graph_store(tmp_path: Path) -> Generator[MemoryStore, None, None]:
    """Create a store with relation-rich entries for graph boost tests."""
    s = MemoryStore(tmp_path)
    # These entries produce relations via "X uses/manages Y" patterns
    s.save(key="svc-a", value="ServiceA uses ServiceB", tier="architectural", source="human")
    s.save(key="svc-b", value="ServiceB manages DataStore", tier="architectural", source="human")
    s.save(key="svc-c", value="ServiceC uses DataStore", tier="pattern", source="agent")
    s.save(
        key="lonely",
        value="standalone note about Python testing",
        tier="context",
        source="agent",
    )
    yield s
    s.close()


class TestGraphBoost:
    """Tests for use_graph_boost / graph_boost_factor in RecallConfig."""

    def test_config_defaults(self):
        cfg = RecallConfig()
        assert cfg.use_graph_boost is False
        assert cfg.graph_boost_factor == 0.15

    def test_config_custom_values(self):
        cfg = RecallConfig(use_graph_boost=True, graph_boost_factor=0.25)
        assert cfg.use_graph_boost is True
        assert cfg.graph_boost_factor == 0.25

    def test_graph_boost_disabled_by_default(self, graph_store):
        """Without use_graph_boost, no graph_boosted flag appears."""
        orch = RecallOrchestrator(graph_store)
        result = orch.recall("ServiceA")
        for mem in result.memories:
            assert "graph_boosted" not in mem

    def test_graph_boost_marks_connected_entries(self, graph_store):
        """With use_graph_boost, connected entries get graph_boosted flag."""
        cfg = RecallConfig(use_graph_boost=True, graph_boost_factor=0.2)
        orch = RecallOrchestrator(graph_store, config=cfg)
        result = orch.recall("ServiceA ServiceB DataStore")
        boosted = [m for m in result.memories if m.get("graph_boosted")]
        # At least one entry should be boosted (connected via shared entities)
        if result.memory_count >= 2:
            assert len(boosted) >= 1

    def test_graph_boost_increases_score(self, graph_store):
        """Boosted entries have higher scores than without boost."""
        orch_no_boost = RecallOrchestrator(graph_store)
        result_no = orch_no_boost.recall("ServiceA ServiceB DataStore")

        cfg = RecallConfig(use_graph_boost=True, graph_boost_factor=0.2)
        orch_boost = RecallOrchestrator(graph_store, config=cfg)
        result_yes = orch_boost.recall("ServiceA ServiceB DataStore")

        # Build score maps
        scores_no = {str(m.get("key", "")): float(m.get("score", 0)) for m in result_no.memories}
        scores_yes = {str(m.get("key", "")): float(m.get("score", 0)) for m in result_yes.memories}

        # Any boosted entry should have score >= original
        for mem in result_yes.memories:
            key = str(mem.get("key", ""))
            if mem.get("graph_boosted") and key in scores_no:
                assert scores_yes[key] >= scores_no[key]

    def test_graph_boost_per_call_override(self, graph_store):
        """use_graph_boost can be overridden per call."""
        orch = RecallOrchestrator(graph_store)
        result = orch.recall("ServiceA ServiceB", use_graph_boost=True)
        # Should not raise; graph boost applied via override
        assert isinstance(result, RecallResult)


# ---------------------------------------------------------------------------
# Review 018-B: Hive count accuracy, token count, and filter robustness
# ---------------------------------------------------------------------------


class TestHiveCountAccuracy:
    """hive_memory_count must stay accurate when post-filters remove Hive entries."""

    def test_hive_count_zero_without_hive_store(self, store):
        """Without a hive_store, hive_memory_count must always be 0."""
        orch = RecallOrchestrator(store)
        result = orch.recall("python tech stack")
        assert result.hive_memory_count == 0

    def test_hive_count_decremented_by_dedupe_filter(self, store):
        """Hive entries removed by dedupe_window should not count in hive_memory_count."""
        # Manually invoke _search_hive result via merge: inject synthetic hive memories
        # into the orchestrator's result pipeline by patching _search_hive.
        from unittest.mock import patch

        orch = RecallOrchestrator(store)

        fake_hive_memories = [
            {
                "key": "hive-alpha",
                "confidence": 0.7,
                "tier": "pattern",
                "score": 0.7,
                "source": "hive",
                "namespace": "universal",
                "value": "Hive alpha fact",
            },
            {
                "key": "hive-beta",
                "confidence": 0.6,
                "tier": "context",
                "score": 0.6,
                "source": "hive",
                "namespace": "universal",
                "value": "Hive beta fact",
            },
        ]

        with (
            patch.object(orch, "_search_hive", return_value=(fake_hive_memories, 2)),
            patch.object(orch, "_hive_store", object()),  # non-None triggers hive path
        ):
            # Apply a dedupe_window that removes one hive entry
            result = orch.recall(
                "python tech stack",
                dedupe_window=["hive-alpha"],
            )

        # hive-alpha was deduped → only hive-beta remains with source=="hive"
        hive_keys = [m.get("key") for m in result.memories if m.get("source") == "hive"]
        assert "hive-alpha" not in hive_keys
        # hive_memory_count must reflect post-filter reality
        assert result.hive_memory_count == result.memory_count - sum(
            1 for m in result.memories if m.get("source") != "hive"
        )


class TestTokenCountAfterHiveMerge:
    """token_count must reflect the final memory_section, including Hive additions."""

    def test_token_count_nonzero_when_memories_found(self, store):
        """When memories are found, token_count must be > 0."""
        orch = RecallOrchestrator(store)
        result = orch.recall("python tech stack")
        if result.memory_count > 0:
            assert result.token_count > 0

    def test_token_count_zero_when_empty(self, store):
        """When no memories match, token_count must be 0."""
        orch = RecallOrchestrator(store)
        result = orch.recall("quantum computing blockchain")
        assert result.token_count == 0

    def test_token_count_consistent_with_section_length(self, store):
        """token_count should be ~ len(memory_section) // 4 (estimate_tokens formula)."""
        orch = RecallOrchestrator(store)
        result = orch.recall("python tech stack testing")
        if result.memory_section:
            expected = max(1, len(result.memory_section) // 4)
            assert result.token_count == expected


class TestApplyPostFiltersRobustness:
    """_apply_post_filters must handle edge cases without raising."""

    def test_non_numeric_confidence_does_not_raise(self, store):
        """A non-numeric confidence value in a memory dict must not raise ValueError."""
        orch = RecallOrchestrator(store)
        # Inject a memory with non-numeric confidence via dedupe_window path
        # (so scope/tier filters don't call store.get — bypassing the lookup).
        memories: list[dict[str, object]] = [
            {
                "key": "tech-stack",
                "confidence": "not-a-number",  # bad value
                "tier": "architectural",
                "score": 0.9,
                "value": "Python 3.12",
            }
        ]
        from tapps_brain.recall import RecallConfig

        cfg = RecallConfig(dedupe_window=["other-key"])  # no scope/tier/branch filter
        # Should NOT raise ValueError
        filtered, section = orch._apply_post_filters(memories, cfg, "query")
        assert len(filtered) == 1
        # confidence should default to 0.0 for non-numeric input
        assert "0.00" in section

    def test_empty_memories_returns_empty_section(self, store):
        """Empty input to _apply_post_filters returns ([], '')."""
        orch = RecallOrchestrator(store)
        from tapps_brain.recall import RecallConfig

        cfg = RecallConfig(dedupe_window=["x"])
        filtered, section = orch._apply_post_filters([], cfg, "query")
        assert filtered == []
        assert section == ""
