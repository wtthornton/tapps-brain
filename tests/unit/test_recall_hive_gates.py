"""Pin recall orchestrator hive-path quality gates (run 5 audit).

Previously: low engagement injected Hive memories anyway, the circuit-breaker
hive-weight multiplier was bypassed by the store's constructor arg, min_score
and tier_filter never applied to Hive results, the truncation cost model
undercounted the real line format, and post-filters ran after truncation.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tapps_brain.injection import estimate_tokens
from tapps_brain.models import MemoryTier
from tapps_brain.recall import RecallConfig, RecallOrchestrator
from tapps_brain.store import MemoryStore


def _hive_mock(rows: list[dict]) -> MagicMock:
    hive = MagicMock()
    hive.search.return_value = rows
    hive.get_agent_groups.return_value = []
    return hive


@pytest.fixture
def store(tmp_path) -> MemoryStore:
    s = MemoryStore(tmp_path)
    yield s
    s.close()


class TestLowEngagementHive:
    def test_low_engagement_skips_hive(self, store):
        hive = _hive_mock([{"key": "hive-fact", "confidence": 0.9, "value": "hive says tabs"}])
        orch = RecallOrchestrator(store, hive_store=hive, hive_recall_weight=0.8)
        result = orch.recall("tabs or spaces", engagement_level="low")
        assert result.memory_count == 0
        assert result.hive_memory_count == 0
        hive.search.assert_not_called()


class TestCircuitBreakerWeight:
    def test_store_orchestrator_respects_dynamic_weight(self, store):
        hive = _hive_mock([{"key": "hive-fact", "confidence": 0.8, "value": "v"}])
        store._hive_store = hive
        # Simulate an OPEN diagnostics circuit: weight goes to 0.0.
        store._hive_recall_weight_multiplier = 0.0
        orch = store._recall_get_orchestrator()
        assert orch._hive_recall_weight is None  # dynamic getter path
        hive_memories, _, _ = orch._search_hive("query", [], RecallConfig())
        # score = 0.8 * 0.0 = 0.0 < min_score -> suppressed entirely
        assert hive_memories == []


class TestHiveMinScore:
    def test_hive_results_below_min_score_dropped(self, store):
        hive = _hive_mock([{"key": "weak", "confidence": 0.2, "value": "v"}])
        orch = RecallOrchestrator(store, hive_store=hive, hive_recall_weight=0.8)
        hive_memories, count, _ = orch._search_hive("q", [], RecallConfig(min_score=0.3))
        assert hive_memories == [] and count == 0

    def test_hive_results_above_min_score_kept(self, store):
        hive = _hive_mock([{"key": "strong", "confidence": 0.8, "value": "v"}])
        orch = RecallOrchestrator(store, hive_store=hive, hive_recall_weight=0.8)
        hive_memories, count, _ = orch._search_hive("q", [], RecallConfig(min_score=0.3))
        assert count == 1 and hive_memories[0]["score"] == pytest.approx(0.64)


class TestTruncationCostModel:
    def test_truncated_section_fits_budget(self):
        memories = [
            {
                "key": f"key-{i}",
                "confidence": 0.7,
                "tier": "pattern",
                "score": 0.7,
                "source": "hive",
                "namespace": "universal",
                "value": "some medium length memory value " * 3,
            }
            for i in range(10)
        ]
        budget = 300
        kept, truncated = RecallOrchestrator._truncate_to_budget(memories, budget)
        assert truncated is True
        section = RecallOrchestrator._rebuild_section(kept)
        assert estimate_tokens(section) <= budget


class TestHiveTierFilter:
    def test_tier_filter_applies_to_hive_memories(self, store):
        orch = RecallOrchestrator(store)
        memories: list[dict[str, object]] = [
            {
                "key": "hive-pattern",
                "confidence": 0.7,
                "tier": "pattern",
                "score": 0.7,
                "source": "hive",
                "namespace": "universal",
                "value": "v",
            }
        ]
        cfg = RecallConfig(tier_filter=MemoryTier.architectural)
        filtered, _ = orch._apply_post_filters(memories, cfg)
        assert filtered == []

    def test_matching_hive_tier_kept(self, store):
        orch = RecallOrchestrator(store)
        memories: list[dict[str, object]] = [
            {
                "key": "hive-arch",
                "confidence": 0.7,
                "tier": "architectural",
                "score": 0.7,
                "source": "hive",
                "namespace": "universal",
                "value": "v",
            }
        ]
        cfg = RecallConfig(tier_filter=MemoryTier.architectural)
        filtered, _ = orch._apply_post_filters(memories, cfg)
        assert len(filtered) == 1


class TestPostFilterBeforeTruncation:
    def test_deduped_entries_do_not_consume_budget(self, store):
        hive_rows = [
            {"key": "hive-a", "confidence": 0.8, "value": "aaaa " * 10},
            {"key": "hive-b", "confidence": 0.7, "value": "bbbb " * 10},
        ]
        hive = _hive_mock(hive_rows)
        store.save(key="local-dup", value="dddd " * 10, tier="pattern", source="agent")
        orch = RecallOrchestrator(store, hive_store=hive, hive_recall_weight=0.8)
        # Budget sized to fit ~2 lines; local-dup is deduped away and must
        # not have consumed budget that would evict hive-b.
        result = orch.recall(
            "dddd aaaa bbbb",
            dedupe_window=["local-dup"],
            max_tokens=60,
        )
        keys = [m["key"] for m in result.memories]
        assert "local-dup" not in keys
        assert "hive-a" in keys
