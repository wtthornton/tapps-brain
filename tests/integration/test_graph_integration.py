"""Integration tests for knowledge graph lifecycle (EPIC-006, STORY-006.6).

All tests use a real MemoryStore + SQLite (no mocks). Covers:
- Save entries with relations, close/reopen, verify persistence
- find_related traversal across reopened store
- Relation transfer on supersede
- Graph-based recall boost ranking
"""

from __future__ import annotations

import pytest

from tapps_brain.models import RecallResult
from tapps_brain.recall import RecallConfig, RecallOrchestrator
from tapps_brain.store import MemoryStore


@pytest.fixture()
def graph_store(tmp_path):
    """Create a store with relation-rich entries on real SQLite."""
    store = MemoryStore(tmp_path)
    # "X uses/manages Y" patterns produce relations via extract_relations
    store.save(key="svc-auth", value="AuthService uses TokenStore", tier="architectural")
    store.save(key="svc-api", value="ApiGateway uses AuthService", tier="architectural")
    store.save(key="svc-cache", value="CacheLayer manages TokenStore", tier="pattern")
    store.save(key="svc-db", value="DatabaseLayer manages DataStore", tier="architectural")
    store.save(key="plain-note", value="Remember to update docs", tier="context")
    yield store
    store.close()


class TestGraphPersistenceRoundTrip:
    """Relations survive close/reopen on real SQLite."""

    def test_relations_persist_across_restart(self, tmp_path):
        store1 = MemoryStore(tmp_path)
        store1.save(key="a", value="ServiceA uses ServiceB", tier="architectural")
        rels_before = store1.get_relations("a")
        assert len(rels_before) >= 1
        store1.close()

        store2 = MemoryStore(tmp_path)
        rels_after = store2.get_relations("a")
        assert len(rels_after) >= 1
        assert rels_after[0]["subject"].lower() == rels_before[0]["subject"].lower()
        store2.close()

    def test_find_related_after_restart(self, tmp_path):
        store1 = MemoryStore(tmp_path)
        store1.save(key="x", value="ServiceX uses ServiceY")
        store1.save(key="y", value="ServiceY manages DataStore")
        related_before = store1.find_related("x")
        assert len(related_before) >= 1
        store1.close()

        store2 = MemoryStore(tmp_path)
        related_after = store2.find_related("x")
        keys_after = [k for k, _h in related_after]
        assert "y" in keys_after
        store2.close()

    def test_query_relations_after_restart(self, tmp_path):
        store1 = MemoryStore(tmp_path)
        store1.save(key="m", value="ModuleA uses ModuleB")
        qr_before = store1.query_relations(predicate="uses")
        assert len(qr_before) >= 1
        store1.close()

        store2 = MemoryStore(tmp_path)
        qr_after = store2.query_relations(predicate="uses")
        assert len(qr_after) >= 1
        store2.close()


class TestGraphTraversal:
    """BFS traversal works correctly on real SQLite data."""

    def test_multi_hop_traversal(self, graph_store):
        """AuthService -> TokenStore -> CacheLayer is a 2-hop chain."""
        related = graph_store.find_related("svc-auth")
        hop_map = dict(related)
        # svc-api shares "AuthService" -> hop 1
        assert hop_map.get("svc-api") == 1
        # svc-cache shares "TokenStore" -> hop 1 or 2
        assert "svc-cache" in hop_map

    def test_plain_entry_not_connected(self, graph_store):
        """Entry without relations is not connected to others."""
        related = graph_store.find_related("plain-note")
        assert related == []

    def test_query_relations_filters(self, graph_store):
        """query_relations filters by predicate correctly."""
        uses_rels = graph_store.query_relations(predicate="uses")
        manages_rels = graph_store.query_relations(predicate="manages")
        assert len(uses_rels) >= 2  # auth->token, api->auth
        assert len(manages_rels) >= 1  # cache->token or db->datastore


class TestSupersedeRelationTransfer:
    """Supersede transfers relations on real SQLite."""

    def test_supersede_carries_relations(self, graph_store):
        old_rels = graph_store.get_relations("svc-auth")
        assert len(old_rels) >= 1

        new_entry = graph_store.supersede("svc-auth", "AuthService v2 uses TokenStore")
        new_rels = graph_store.get_relations(new_entry.key)
        # New entry should have at least the old relations
        old_subjects = {r["subject"].lower() for r in old_rels}
        new_subjects = {r["subject"].lower() for r in new_rels}
        assert old_subjects.issubset(new_subjects)

    def test_supersede_relations_persist_after_restart(self, tmp_path):
        store1 = MemoryStore(tmp_path)
        store1.save(key="fact-1", value="ServiceP uses ServiceQ", tier="architectural")
        new = store1.supersede("fact-1", "ServiceP v2 uses ServiceQ", key="fact-1.v2")
        rels = store1.get_relations(new.key)
        assert len(rels) >= 1
        store1.close()

        store2 = MemoryStore(tmp_path)
        rels_after = store2.get_relations("fact-1.v2")
        assert len(rels_after) >= 1
        store2.close()

    def test_find_related_follows_superseded_entry(self, tmp_path):
        """After supersede, the new entry is reachable via graph."""
        store = MemoryStore(tmp_path)
        store.save(key="comp-a", value="ComponentA uses ComponentB")
        store.save(key="comp-b", value="ComponentB manages DataLayer")
        new = store.supersede("comp-a", "ComponentA v2 uses ComponentB", key="comp-a-v2")

        related = store.find_related(new.key)
        keys = [k for k, _h in related]
        assert "comp-b" in keys
        store.close()


class TestRecallGraphBoost:
    """Graph boost affects ranking in real recall flow."""

    def test_graph_boost_returns_result(self, graph_store):
        cfg = RecallConfig(use_graph_boost=True, graph_boost_factor=0.2)
        orch = RecallOrchestrator(graph_store, config=cfg)
        result = orch.recall("AuthService TokenStore")
        assert isinstance(result, RecallResult)

    def test_graph_boost_does_not_crash_on_empty(self, tmp_path):
        """Graph boost on empty store returns empty result."""
        store = MemoryStore(tmp_path)
        cfg = RecallConfig(use_graph_boost=True)
        orch = RecallOrchestrator(store, config=cfg)
        result = orch.recall("anything")
        assert isinstance(result, RecallResult)
        assert result.memory_count == 0
        store.close()

    def test_boosted_scores_gte_unboosted(self, graph_store):
        """With graph boost, connected entries have scores >= unboosted."""
        orch_plain = RecallOrchestrator(graph_store)
        result_plain = orch_plain.recall("AuthService TokenStore ApiGateway")

        cfg = RecallConfig(use_graph_boost=True, graph_boost_factor=0.2)
        orch_boost = RecallOrchestrator(graph_store, config=cfg)
        result_boost = orch_boost.recall("AuthService TokenStore ApiGateway")

        scores_plain = {
            str(m.get("key", "")): float(m.get("score", 0)) for m in result_plain.memories
        }
        for mem in result_boost.memories:
            key = str(mem.get("key", ""))
            if mem.get("graph_boosted") and key in scores_plain:
                assert float(mem.get("score", 0)) >= scores_plain[key]
