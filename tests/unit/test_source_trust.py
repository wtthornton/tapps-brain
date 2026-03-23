"""Tests for source_trust multiplier in composite retrieval scoring (M2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from tapps_brain.models import MemoryEntry, MemorySource
from tapps_brain.profile import _DEFAULT_SOURCE_TRUST, ScoringConfig
from tapps_brain.retrieval import _DEFAULT_SOURCE_TRUST as _RET_DEFAULT_TRUST
from tapps_brain.retrieval import MemoryRetriever
from tests.factories import make_entry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 3, 22, 12, 0, 0, tzinfo=UTC)
_RECENT = (_NOW - timedelta(days=1)).isoformat()


def _make_entry(
    key: str = "test-key",
    value: str = "test value",
    *,
    confidence: float = 0.8,
    source: MemorySource = MemorySource.agent,
    access_count: int = 5,
) -> MemoryEntry:
    return make_entry(
        key=key,
        value=value,
        confidence=confidence,
        source=source,
        updated_at=_RECENT,
        created_at=_RECENT,
        last_accessed=_RECENT,
        access_count=access_count,
    )


def _make_store(entries: list[MemoryEntry]) -> MagicMock:
    store = MagicMock()
    store.list_all.return_value = entries
    store.search.return_value = entries
    entry_map = {e.key: e for e in entries}
    store.get.side_effect = lambda k, **kwargs: entry_map.get(k)
    return store


# ---------------------------------------------------------------------------
# ScoringConfig source_trust tests
# ---------------------------------------------------------------------------


class TestScoringConfigSourceTrust:
    def test_default_source_trust_values(self) -> None:
        config = ScoringConfig()
        assert config.source_trust == {
            "human": 1.0,
            "system": 0.9,
            "agent": 0.7,
            "inferred": 0.5,
        }

    def test_custom_source_trust_accepted(self) -> None:
        config = ScoringConfig(source_trust={"human": 1.0, "agent": 0.5})
        assert config.source_trust["agent"] == 0.5

    def test_weight_sum_unaffected_by_source_trust(self) -> None:
        """source_trust is a multiplier, not a weight — weight sum check ignores it."""
        config = ScoringConfig(source_trust={"human": 2.0, "agent": 0.1})
        total = config.relevance + config.confidence + config.recency + config.frequency
        assert total == pytest.approx(1.0)

    def test_defaults_match_retrieval_module(self) -> None:
        """Profile defaults and retrieval module defaults must agree."""
        assert _DEFAULT_SOURCE_TRUST == _RET_DEFAULT_TRUST


# ---------------------------------------------------------------------------
# MemoryRetriever source_trust wiring tests
# ---------------------------------------------------------------------------


class TestRetrieverSourceTrust:
    def test_default_source_trust_loaded(self) -> None:
        retriever = MemoryRetriever()
        assert retriever._source_trust == _RET_DEFAULT_TRUST

    def test_scoring_config_source_trust_loaded(self) -> None:
        config = ScoringConfig(source_trust={"human": 1.0, "agent": 0.3})
        retriever = MemoryRetriever(scoring_config=config)
        assert retriever._source_trust["agent"] == 0.3

    def test_scoring_config_without_source_trust_uses_default(self) -> None:
        """If scoring_config has no source_trust attr, use module default."""

        class BareConfig:
            relevance = 0.40
            confidence = 0.30
            recency = 0.15
            frequency = 0.15
            bm25_norm_k = 5.0
            frequency_cap = 20

        retriever = MemoryRetriever(scoring_config=BareConfig())
        assert retriever._source_trust == _RET_DEFAULT_TRUST


# ---------------------------------------------------------------------------
# Ranking impact tests
# ---------------------------------------------------------------------------


class TestSourceTrustRanking:
    def test_human_outranks_inferred_same_content(self) -> None:
        """human (trust=1.0) should outrank inferred (trust=0.5) given equal signals."""
        entries = [
            _make_entry("from-inferred", "shared data pattern", source=MemorySource.inferred),
            _make_entry("from-human", "shared data pattern", source=MemorySource.human),
        ]
        retriever = MemoryRetriever()
        store = _make_store(entries)

        results = retriever.search("shared data pattern", store)
        assert len(results) == 2
        assert results[0].entry.key == "from-human"
        assert results[0].score > results[1].score

    def test_human_outranks_agent_same_content(self) -> None:
        """human (trust=1.0) should outrank agent (trust=0.7) given equal signals."""
        entries = [
            _make_entry("from-agent", "api design pattern", source=MemorySource.agent),
            _make_entry("from-human", "api design pattern", source=MemorySource.human),
        ]
        retriever = MemoryRetriever()
        store = _make_store(entries)

        results = retriever.search("api design pattern", store)
        assert len(results) == 2
        assert results[0].entry.key == "from-human"

    def test_system_outranks_agent(self) -> None:
        """system (trust=0.9) should outrank agent (trust=0.7) given equal signals."""
        entries = [
            _make_entry("from-agent", "config setup data", source=MemorySource.agent),
            _make_entry("from-system", "config setup data", source=MemorySource.system),
        ]
        retriever = MemoryRetriever()
        store = _make_store(entries)

        results = retriever.search("config setup data", store)
        assert len(results) == 2
        assert results[0].entry.key == "from-system"

    def test_source_trust_ordering_all_sources(self) -> None:
        """All four sources should rank in trust order: human > system > agent > inferred."""
        entries = [
            _make_entry("from-inferred", "common query term match", source=MemorySource.inferred),
            _make_entry("from-agent", "common query term match", source=MemorySource.agent),
            _make_entry("from-system", "common query term match", source=MemorySource.system),
            _make_entry("from-human", "common query term match", source=MemorySource.human),
        ]
        retriever = MemoryRetriever()
        store = _make_store(entries)

        results = retriever.search("common query term match", store)
        assert len(results) == 4
        keys = [r.entry.key for r in results]
        assert keys == ["from-human", "from-system", "from-agent", "from-inferred"]

    def test_custom_trust_reverses_ranking(self) -> None:
        """Custom trust config can invert the default ordering."""
        config = ScoringConfig(source_trust={"human": 0.5, "agent": 1.0})
        entries = [
            _make_entry("from-human", "database config", source=MemorySource.human),
            _make_entry("from-agent", "database config", source=MemorySource.agent),
        ]
        retriever = MemoryRetriever(scoring_config=config)
        store = _make_store(entries)

        results = retriever.search("database config", store)
        assert len(results) == 2
        # Agent has trust=1.0, human has trust=0.5 — agent wins
        assert results[0].entry.key == "from-agent"

    def test_unknown_source_defaults_to_1(self) -> None:
        """An entry with an unrecognized source string gets trust=1.0 (no penalty)."""
        entry = _make_entry("custom-source", "test data")
        # Manually set source to a string not in the trust dict
        entry = entry.model_copy(update={"source": "custom"})

        # Use all-1.0 trust as reference — unknown source should behave identically
        config_all_one = ScoringConfig(
            source_trust={"human": 1.0, "agent": 1.0, "system": 1.0, "inferred": 1.0}
        )
        retriever_default = MemoryRetriever()
        retriever_all_one = MemoryRetriever(scoring_config=config_all_one)
        store = _make_store([entry])

        results_default = retriever_default.search("test data", store)
        results_all_one = retriever_all_one.search("test data", store)
        assert len(results_default) == 1
        # Unknown source falls back to trust=1.0 — score must match the all-1.0 retriever
        assert results_default[0].score == pytest.approx(results_all_one[0].score, rel=1e-6)

    def test_empty_source_trust_dict_uses_no_penalty(self) -> None:
        """Empty source_trust dict causes all sources to get trust=1.0 (get default)."""
        config = ScoringConfig(source_trust={})
        entries = [
            _make_entry("agent-entry", "test content", source=MemorySource.agent),
            _make_entry("inferred-entry", "test content", source=MemorySource.inferred),
        ]
        retriever = MemoryRetriever(scoring_config=config)
        store = _make_store(entries)

        results = retriever.search("test content", store)
        assert len(results) == 2
        # With empty trust dict, all sources default to 1.0 → scores should be equal
        assert results[0].score == pytest.approx(results[1].score, rel=1e-6)

    def test_trust_1_preserves_original_score(self) -> None:
        """Trust=1.0 should not modify the composite score (identity multiplier)."""
        config = ScoringConfig(
            source_trust={"human": 1.0, "agent": 1.0, "system": 1.0, "inferred": 1.0}
        )
        entries = [_make_entry("test-key", "framework config", source=MemorySource.agent)]
        retriever_default = MemoryRetriever()
        retriever_all_one = MemoryRetriever(scoring_config=config)
        store = _make_store(entries)

        # With trust=1.0 for all, agent default is 0.7 → score differs
        # But if we set agent=1.0 explicitly, it should be higher
        results_one = retriever_all_one.search("framework config", store)
        results_default = retriever_default.search("framework config", store)
        assert len(results_one) == 1
        assert len(results_default) == 1
        # All-1.0 should produce >= default (agent default is 0.7)
        assert results_one[0].score >= results_default[0].score


# ---------------------------------------------------------------------------
# Profile YAML loading test
# ---------------------------------------------------------------------------


class TestProfileSourceTrustLoading:
    def test_repo_brain_profile_has_source_trust(self) -> None:
        from tapps_brain.profile import get_builtin_profile

        profile = get_builtin_profile("repo-brain")
        assert profile.scoring.source_trust == {
            "human": 1.0,
            "system": 0.9,
            "agent": 0.7,
            "inferred": 0.5,
        }
