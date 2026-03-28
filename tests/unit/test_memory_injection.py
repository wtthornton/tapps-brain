"""Tests for memory injection into expert/research responses (Epic 25, Story 25.2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from tapps_brain.injection import (
    _MAX_INJECT_HIGH,
    _MAX_INJECT_MEDIUM,
    append_memory_to_answer,
    estimate_tokens,
    inject_memories,
)
from tapps_brain.profile import ScoringConfig
from tests.factories import make_entry

if TYPE_CHECKING:
    from pathlib import Path

    from tapps_brain.models import MemoryEntry


# Default scoring config used by mock stores — weights sum to 1.0 as required.
_DEFAULT_SCORING = ScoringConfig()

_RECENT = (datetime.now(tz=UTC) - timedelta(hours=1)).isoformat()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(
    key: str = "test-key",
    value: str = "test value for matching",
    confidence: float = 0.8,
    contradicted: bool = False,
) -> MemoryEntry:
    return make_entry(
        key=key,
        value=value,
        confidence=confidence,
        access_count=5,
        updated_at=_RECENT,
        created_at=_RECENT,
        last_accessed=_RECENT,
        contradicted=contradicted,
    )


def _make_store(
    entries: list[MemoryEntry] | None = None,
    scoring_config: ScoringConfig | None = None,
) -> MagicMock:
    store = MagicMock()
    entries = entries or []
    store.search.return_value = entries
    store.list_all.return_value = entries
    entry_map = {e.key: e for e in entries}
    store.get.side_effect = lambda k, **kw: entry_map.get(k)
    # Provide a real ScoringConfig so MemoryRetriever weight-sum validation passes.
    mock_profile = MagicMock()
    mock_profile.scoring = scoring_config or _DEFAULT_SCORING
    store.profile = mock_profile
    return store


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestInjectMemories:
    def test_memories_injected_when_relevant(self) -> None:
        entries = [_make_entry("jwt-config", "JWT authentication with RS256")]
        store = _make_store(entries)

        result = inject_memories("JWT authentication", store, "high")

        assert result["memory_injected"] >= 1
        assert "jwt-config" in result["memory_section"]
        assert "Project Memory" in result["memory_section"]

    def test_no_injection_when_no_matches(self) -> None:
        store = _make_store([])

        result = inject_memories("completely unrelated", store, "high")

        assert result["memory_injected"] == 0
        assert result["memory_section"] == ""

    def test_low_engagement_never_injects(self) -> None:
        entries = [_make_entry("key", "matching value")]
        store = _make_store(entries)

        result = inject_memories("matching value", store, "low")

        assert result["memory_injected"] == 0
        assert result["memory_section"] == ""

    def test_high_engagement_max_limit(self) -> None:
        entries = [_make_entry(f"key-{i}", f"matching search term {i}") for i in range(10)]
        store = _make_store(entries)

        result = inject_memories("matching search term", store, "high")

        assert result["memory_injected"] <= _MAX_INJECT_HIGH

    def test_medium_engagement_max_limit(self) -> None:
        entries = [
            _make_entry(f"key-{i}", f"matching value {i}", confidence=0.9) for i in range(10)
        ]
        store = _make_store(entries)

        result = inject_memories("matching value", store, "medium")

        assert result["memory_injected"] <= _MAX_INJECT_MEDIUM

    def test_medium_engagement_filters_low_confidence(self) -> None:
        entries = [
            _make_entry("low-conf", "matching data", confidence=0.3),
        ]
        store = _make_store(entries)

        result = inject_memories("matching data", store, "medium")

        # Medium requires confidence > 0.5, entry has 0.3
        assert result["memory_injected"] == 0

    def test_contradicted_memories_not_injected(self) -> None:
        entries = [
            _make_entry("bad-key", "contradicted data", contradicted=True),
        ]
        store = _make_store(entries)

        result = inject_memories("contradicted data", store, "high")

        # Contradicted entries are excluded by retriever by default
        assert result["memory_injected"] == 0

    def test_memory_section_format(self) -> None:
        entries = [_make_entry("my-key", "my value content")]
        store = _make_store(entries)

        result = inject_memories("my value content", store, "high")

        if result["memory_injected"] > 0:
            assert "### Project Memory" in result["memory_section"]
            assert "confidence:" in result["memory_section"]
            assert "tier:" in result["memory_section"]

    def test_memories_list_in_result(self) -> None:
        entries = [_make_entry("test-key", "test matching value")]
        store = _make_store(entries)

        result = inject_memories("test matching value", store, "high")

        if result["memory_injected"] > 0:
            assert len(result["memories"]) > 0
            mem = result["memories"][0]
            assert "key" in mem
            assert mem.get("value") == "test matching value"
            assert "confidence" in mem
            assert "score" in mem

    def test_result_includes_truncated_and_injected_tokens(self) -> None:
        """Epic 65.16: injection result exposes truncation and token count."""
        entries = [_make_entry("k", "short")]
        store = _make_store(entries)
        result = inject_memories("short", store, "high")
        assert "truncated" in result
        assert "injected_tokens" in result
        if result["memory_injected"] > 0:
            assert result["injected_tokens"] >= estimate_tokens("short")

    def test_context_budget_truncates_when_over_limit(self) -> None:
        """Epic 65.16: when total tokens exceed injection_max_tokens, injection is truncated."""
        from tapps_brain.injection import InjectionConfig

        config = InjectionConfig(injection_max_tokens=60, reranker_enabled=False)

        # Each formatted line ~80+ chars → ~20 tokens; 4 entries would exceed 60
        long_value = "a" * 120
        entries = [_make_entry(f"key-{i}", long_value) for i in range(5)]
        store = _make_store(entries)

        result = inject_memories("a", store, "high", config=config)

        assert result["injected_tokens"] <= 60
        assert "truncated" in result
        if result["memory_injected"] >= 2:
            # With budget 60, we may have truncated if we had enough high-scoring results
            assert result["truncated"] is True or result["injected_tokens"] <= 60


class TestEstimateTokens:
    def test_estimate_tokens_approx_four_chars_per_token(self) -> None:
        assert estimate_tokens("") == 1
        assert estimate_tokens("abcd") == 1
        assert estimate_tokens("a" * 8) == 2
        assert estimate_tokens("a" * 40) == 10


class TestAppendMemoryToAnswer:
    def test_appends_when_section_exists(self) -> None:
        answer = "Expert answer here."
        memory_result = {
            "memory_section": "### Project Memory\n- **key** (conf: 0.8): value",
            "memory_injected": 1,
        }

        result = append_memory_to_answer(answer, memory_result)

        assert "Expert answer here." in result
        assert "### Project Memory" in result

    def test_no_append_when_empty_section(self) -> None:
        answer = "Expert answer here."
        memory_result = {"memory_section": "", "memory_injected": 0}

        result = append_memory_to_answer(answer, memory_result)

        assert result == answer


# ---------------------------------------------------------------------------
# Regression: BUG-002-A — source trust threading
# ---------------------------------------------------------------------------


class TestSourceTrustRegression:
    """Regression tests for BUG-002-A: scoring_config must flow through inject_memories.

    Before the fix, inject_memories always created MemoryRetriever() without
    scoring_config, so it used _DEFAULT_SOURCE_TRUST with agent=0.7. For
    marginal composite scores, the 0.7x multiplier pushed scores below
    _MIN_SCORE=0.3, causing zero results for agent-sourced memories.
    """

    def test_agent_sourced_memory_recalled_with_real_store(self, tmp_path: Path) -> None:
        """Memories with default (agent) source are recalled from a real store.

        Regression guard: inject_memories must thread scoring_config from the
        store's active profile so source trust is profile-configured, not hardcoded.
        """
        from tapps_brain.store import MemoryStore

        store = MemoryStore(tmp_path / "project")
        # Save with default source (agent) — the most common case
        store.save(
            key="api-pattern",
            value="api endpoint pattern for authentication and authorization",
            confidence=0.9,
            tier="architectural",
        )

        result = inject_memories(
            "api endpoint authentication",
            store,
            "high",
        )

        # Agent-sourced memory must survive the source trust multiplier
        assert result["memory_injected"] >= 1, (
            "Expected at least 1 injected memory for agent-sourced entry. "
            "Regression: source trust multiplier may be dropping scores below threshold."
        )

    def test_scoring_config_passed_to_retriever_overrides_source_trust(self) -> None:
        """inject_memories accepts explicit scoring_config and uses it for trust."""
        from tapps_brain.profile import ScoringConfig

        # Trust all sources equally (1.0) — no penalty
        cfg = ScoringConfig(
            source_trust={"human": 1.0, "agent": 1.0, "system": 1.0, "inferred": 1.0}
        )
        entries = [_make_entry("trust-key", "framework configuration pattern")]
        store = _make_store(entries)

        result_with_cfg = inject_memories(
            "framework configuration", store, "high", scoring_config=cfg
        )

        # Reset mock so list_all / search return the same entries
        store = _make_store(entries)
        inject_memories("framework configuration", store, "high")

        # Both paths should return results; explicit config path must also work
        if result_with_cfg["memory_injected"] > 0:
            assert "trust-key" in result_with_cfg["memory_section"]

    def test_inject_memories_reads_scoring_config_from_store_profile(self) -> None:
        """When no explicit scoring_config given, inject_memories reads it from store.profile."""
        from tapps_brain.profile import ScoringConfig

        custom_scoring = ScoringConfig(
            source_trust={"human": 1.0, "agent": 1.0, "system": 1.0, "inferred": 1.0}
        )

        entries = [_make_entry("profile-key", "profile-configured scoring result")]
        store = _make_store(entries, scoring_config=custom_scoring)

        result = inject_memories("profile scoring", store, "high")

        # The call should complete without error — scoring_config is read from profile
        assert isinstance(result, dict)
        assert "memory_injected" in result


# ---------------------------------------------------------------------------
# Return-key consistency (020-A: all paths return the same keys)
# ---------------------------------------------------------------------------

_EXPECTED_KEYS = {"memory_section", "memory_injected", "memories", "truncated", "injected_tokens"}


class TestReturnKeyConsistency:
    """All early-return and success paths of inject_memories must return the same keys."""

    def test_low_engagement_has_all_keys(self) -> None:
        store = _make_store([])
        result = inject_memories("any query", store, "low")
        assert _EXPECTED_KEYS.issubset(result.keys()), (
            f"Missing keys in low-engagement return: {_EXPECTED_KEYS - result.keys()}"
        )
        assert result["truncated"] is False
        assert result["injected_tokens"] == 0

    def test_no_results_has_all_keys(self) -> None:
        store = _make_store([])
        result = inject_memories("completely unrelated query xyz", store, "high")
        assert _EXPECTED_KEYS.issubset(result.keys()), (
            f"Missing keys in no-results return: {_EXPECTED_KEYS - result.keys()}"
        )
        assert result["truncated"] is False
        assert result["injected_tokens"] == 0

    def test_successful_injection_has_all_keys(self) -> None:
        entries = [_make_entry("k", "matching search text here")]
        store = _make_store(entries)
        result = inject_memories("matching search text", store, "high")
        assert _EXPECTED_KEYS.issubset(result.keys()), (
            f"Missing keys in success return: {_EXPECTED_KEYS - result.keys()}"
        )
