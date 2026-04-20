"""Unit tests for write-path policy (TAP-560 / STORY-SC04)."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from tapps_brain.evaluation import JudgeResult
from tapps_brain.models import MemoryEntry, MemoryTier
from tapps_brain.store import MemoryStore
from tapps_brain.write_policy import (
    DeterministicWritePolicy,
    LLMWritePolicy,
    WriteDecision,
    WritePolicyResult,
    build_write_policy,
)

if TYPE_CHECKING:
    from collections.abc import Generator


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path: Path) -> Generator[MemoryStore, None, None]:
    """MemoryStore backed by InMemoryPrivateBackend (injected by conftest)."""
    s = MemoryStore(tmp_path)
    yield s
    s.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(key: str, value: str, tier: str = "pattern") -> MemoryEntry:
    return MemoryEntry(key=key, value=value, tier=MemoryTier(tier))  # type: ignore[call-arg]


def _make_judge(action: str, target: str | None = None, reasoning: str = "") -> Any:
    """Return a mock LLMJudge that returns the given write-decision payload."""
    import json

    payload = json.dumps({"action": action, "target_key": target, "reasoning": reasoning})
    judge = MagicMock()
    judge.judge_relevance.return_value = JudgeResult(score=1.0, reasoning=payload, confident=True)
    return judge


# ---------------------------------------------------------------------------
# DeterministicWritePolicy
# ---------------------------------------------------------------------------


class TestDeterministicWritePolicy:
    def test_always_returns_add(self) -> None:
        policy = DeterministicWritePolicy()
        result = policy.decide("k", "v", [])
        assert result.decision == WriteDecision.ADD

    def test_add_with_candidates(self) -> None:
        policy = DeterministicWritePolicy()
        candidates = [_make_entry("existing", "some value")]
        result = policy.decide("new-key", "new value", candidates)
        assert result.decision == WriteDecision.ADD

    def test_reasoning_non_empty(self) -> None:
        policy = DeterministicWritePolicy()
        result = policy.decide("k", "v", [])
        assert result.reasoning  # must provide some explanation

    def test_satisfies_write_policy_protocol(self) -> None:
        from tapps_brain._protocols import WritePolicy

        policy = DeterministicWritePolicy()
        assert isinstance(policy, WritePolicy)


# ---------------------------------------------------------------------------
# LLMWritePolicy
# ---------------------------------------------------------------------------


class TestLLMWritePolicy:
    def test_add_decision(self) -> None:
        judge = _make_judge("ADD", reasoning="unique info")
        policy = LLMWritePolicy(judge)
        result = policy.decide("k", "v", [])
        assert result.decision == WriteDecision.ADD
        assert "unique info" in result.reasoning

    def test_noop_decision(self) -> None:
        judge = _make_judge("NOOP", reasoning="already known")
        policy = LLMWritePolicy(judge)
        result = policy.decide("k", "v", [_make_entry("old", "v")])
        assert result.decision == WriteDecision.NOOP

    def test_update_decision_with_target(self) -> None:
        judge = _make_judge("UPDATE", target="old-key", reasoning="update this")
        policy = LLMWritePolicy(judge)
        result = policy.decide("k", "v", [_make_entry("old-key", "old value")])
        assert result.decision == WriteDecision.UPDATE
        assert result.target_key == "old-key"

    def test_delete_decision_with_target(self) -> None:
        judge = _make_judge("DELETE", target="stale-key", reasoning="stale")
        policy = LLMWritePolicy(judge)
        result = policy.decide("k", "v", [_make_entry("stale-key", "stale value")])
        assert result.decision == WriteDecision.DELETE
        assert result.target_key == "stale-key"

    def test_unknown_action_falls_back_to_add(self) -> None:
        import json

        judge = MagicMock()
        judge.judge_relevance.return_value = JudgeResult(
            score=0.5,
            reasoning=json.dumps({"action": "MERGE", "target_key": None, "reasoning": "?"}),
        )
        policy = LLMWritePolicy(judge)
        result = policy.decide("k", "v", [])
        assert result.decision == WriteDecision.ADD

    def test_llm_error_falls_back_to_add(self) -> None:
        judge = MagicMock()
        judge.judge_relevance.side_effect = RuntimeError("LLM timeout")
        policy = LLMWritePolicy(judge)
        result = policy.decide("k", "v", [])
        assert result.decision == WriteDecision.ADD
        assert "error" in result.reasoning.lower() or "fallback" in result.reasoning.lower()

    def test_malformed_json_falls_back_to_add(self) -> None:
        judge = MagicMock()
        judge.judge_relevance.return_value = JudgeResult(
            score=1.0, reasoning="not json at all", confident=False
        )
        policy = LLMWritePolicy(judge)
        result = policy.decide("k", "v", [])
        assert result.decision == WriteDecision.ADD

    def test_rate_limit_triggers_fallback(self) -> None:
        judge = _make_judge("NOOP")
        # Cap at 2 per minute
        policy = LLMWritePolicy(judge, rate_limit_per_minute=2)
        r1 = policy.decide("k1", "v1", [])
        r2 = policy.decide("k2", "v2", [])
        r3 = policy.decide("k3", "v3", [])  # should exceed cap
        assert r1.decision == WriteDecision.NOOP
        assert r2.decision == WriteDecision.NOOP
        # Third call must fall back to ADD
        assert r3.decision == WriteDecision.ADD
        assert "rate limit" in r3.reasoning.lower()

    def test_rate_limit_thread_safe(self) -> None:
        """32 concurrent threads must not exceed the configured rate cap."""
        cap = 10
        # Judge always returns ADD so no LLM parsing is needed; we only care
        # that _check_rate_limit counts correctly under concurrent access.
        judge = _make_judge("ADD")
        policy = LLMWritePolicy(judge, rate_limit_per_minute=cap)

        accepted: list[bool] = []
        lock = threading.Lock()

        def call() -> None:
            result = policy._check_rate_limit()
            with lock:
                accepted.append(result)

        threads = [threading.Thread(target=call) for _ in range(32)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly cap calls should have been accepted.
        assert accepted.count(True) == cap
        assert accepted.count(False) == 32 - cap

    def test_candidates_limited_to_max(self) -> None:
        """The policy should only pass candidates_limit entries to the LLM."""
        judge = _make_judge("ADD")
        policy = LLMWritePolicy(judge, candidates_limit=2)
        many_candidates = [_make_entry(f"k{i}", f"v{i}") for i in range(10)]
        policy.decide("new", "value", many_candidates)
        # Inspect what the judge was called with — the prompt should only
        # reference 2 candidates (indices [1] and [2]).
        call_args = judge.judge_relevance.call_args
        prompt = call_args[1]["query"] if call_args[1] else call_args[0][0]
        # Only k0 and k1 should appear in the prompt (first 2 candidates).
        assert "[3]" not in prompt  # candidate index 3 should NOT appear
        assert "[1]" in prompt
        assert "[2]" in prompt

    def test_satisfies_write_policy_protocol(self) -> None:
        from tapps_brain._protocols import WritePolicy

        judge = _make_judge("ADD")
        policy = LLMWritePolicy(judge)
        assert isinstance(policy, WritePolicy)


# ---------------------------------------------------------------------------
# WritePolicyResult
# ---------------------------------------------------------------------------


class TestWritePolicyResult:
    def test_defaults(self) -> None:
        r = WritePolicyResult(decision=WriteDecision.ADD)
        assert r.target_key is None
        assert r.reasoning == ""

    def test_with_target(self) -> None:
        r = WritePolicyResult(decision=WriteDecision.DELETE, target_key="k", reasoning="r")
        assert r.target_key == "k"
        assert r.reasoning == "r"


# ---------------------------------------------------------------------------
# build_write_policy factory
# ---------------------------------------------------------------------------


class TestBuildWritePolicy:
    def test_deterministic(self) -> None:
        p = build_write_policy("deterministic")
        assert isinstance(p, DeterministicWritePolicy)

    def test_llm_requires_judge(self) -> None:
        with pytest.raises(ValueError, match="LLMJudge"):
            build_write_policy("llm", judge=None)

    def test_llm_with_judge(self) -> None:
        judge = _make_judge("ADD")
        p = build_write_policy("llm", judge=judge)
        assert isinstance(p, LLMWritePolicy)

    def test_unknown_mode_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown write policy"):
            build_write_policy("magic")

    def test_whitespace_and_case_normalised(self) -> None:
        p = build_write_policy("  DETERMINISTIC  ")
        assert isinstance(p, DeterministicWritePolicy)


# ---------------------------------------------------------------------------
# MemoryStore integration: write_policy=None (default path unchanged)
# ---------------------------------------------------------------------------


class TestMemoryStoreWritePolicyIntegration:
    """Verify the store works correctly with both policy modes."""

    def test_default_no_policy_save_works(self, store: Any) -> None:
        """Default store (no policy) saves normally."""
        result = store.save(key="wp-test", value="hello world")
        from tapps_brain.models import MemoryEntry

        assert isinstance(result, MemoryEntry)
        assert result.key == "wp-test"

    def test_deterministic_policy_save_works(self, store: Any) -> None:
        """Explicit DeterministicWritePolicy is a no-op; entry is saved."""
        store._write_policy = DeterministicWritePolicy()
        result = store.save(key="wp-det", value="deterministic save")
        from tapps_brain.models import MemoryEntry

        assert isinstance(result, MemoryEntry)

    def test_noop_decision_returns_existing(self, store: Any) -> None:
        """NOOP decision returns the existing entry without creating a duplicate."""
        # Pre-populate an entry
        store.save(key="existing-key", value="existing value")

        judge = _make_judge("NOOP", reasoning="already known")
        store._write_policy = LLMWritePolicy(judge)
        result = store.save(key="existing-key", value="existing value")
        # Should get back the existing entry (or a noop dict)
        assert isinstance(result, dict | MemoryEntry)
        if isinstance(result, dict):
            assert result.get("write_policy") == "noop"

    def test_delete_decision_removes_target(self, store: Any) -> None:
        """DELETE decision removes the target key."""
        store.save(key="stale", value="stale value")
        assert store.get("stale") is not None

        judge = _make_judge("DELETE", target="stale", reasoning="outdated")
        store._write_policy = LLMWritePolicy(judge)
        result = store.save(key="new-key", value="replacement")
        assert isinstance(result, dict)
        assert result.get("write_policy") == "delete"
        assert store.get("stale") is None
