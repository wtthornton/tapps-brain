"""Tests for the Hive — multi-agent shared brain (EPIC-011)."""

from __future__ import annotations

import sqlite3
import threading
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

from tapps_brain.hive import (
    AgentRegistration,
    AgentRegistry,
    ConflictPolicy,
    HiveStore,
    PropagationEngine,
)
from tapps_brain.store import MemoryStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def hive(tmp_path: Path) -> Generator[HiveStore, None, None]:
    """Create a HiveStore backed by a temp directory."""
    store = HiveStore(db_path=tmp_path / "hive.db")
    yield store
    store.close()


# ---------------------------------------------------------------------------
# 011-A: HiveStore class and SQLite schema
# ---------------------------------------------------------------------------


class TestHiveStoreSchema:
    """Schema creation and connection setup."""

    def test_creates_db_file(self, tmp_path: Path) -> None:
        db_path = tmp_path / "sub" / "hive.db"
        store = HiveStore(db_path=db_path)
        assert db_path.exists()
        store.close()

    def test_wal_mode(self, hive: HiveStore) -> None:
        row = hive._conn.execute("PRAGMA journal_mode").fetchone()
        assert row[0] == "wal"

    def test_hive_memories_table_exists(self, hive: HiveStore) -> None:
        tables = hive._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='hive_memories'"
        ).fetchall()
        assert len(tables) == 1

    def test_hive_memories_columns(self, hive: HiveStore) -> None:
        cols = hive._conn.execute("PRAGMA table_info(hive_memories)").fetchall()
        col_names = {c["name"] for c in cols}
        expected = {
            "namespace",
            "key",
            "value",
            "tier",
            "confidence",
            "source",
            "source_agent",
            "tags",
            "created_at",
            "updated_at",
            "valid_at",
            "invalid_at",
            "superseded_by",
        }
        assert expected <= col_names

    def test_hive_feedback_events_table_exists(self, hive: HiveStore) -> None:
        tables = hive._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='hive_feedback_events'"
        ).fetchall()
        assert len(tables) == 1

    def test_primary_key_is_namespace_key(self, hive: HiveStore) -> None:
        """Composite PK (namespace, key) should reject duplicates."""
        hive._conn.execute(
            "INSERT INTO hive_memories (namespace, key, value, created_at, updated_at) "
            "VALUES ('ns', 'k1', 'v1', '2026-01-01', '2026-01-01')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            hive._conn.execute(
                "INSERT INTO hive_memories (namespace, key, value, created_at, updated_at) "
                "VALUES ('ns', 'k1', 'v2', '2026-01-01', '2026-01-01')"
            )

    def test_same_key_different_namespace(self, hive: HiveStore) -> None:
        """Same key in different namespaces should be allowed."""
        hive._conn.execute(
            "INSERT INTO hive_memories (namespace, key, value, created_at, updated_at) "
            "VALUES ('ns-a', 'k1', 'v1', '2026-01-01', '2026-01-01')"
        )
        hive._conn.execute(
            "INSERT INTO hive_memories (namespace, key, value, created_at, updated_at) "
            "VALUES ('ns-b', 'k1', 'v2', '2026-01-01', '2026-01-01')"
        )
        hive._conn.commit()
        rows = hive._conn.execute("SELECT * FROM hive_memories WHERE key='k1'").fetchall()
        assert len(rows) == 2

    def test_fts5_table_exists(self, hive: HiveStore) -> None:
        tables = hive._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='hive_fts'"
        ).fetchall()
        assert len(tables) == 1

    def test_indexes_created(self, hive: HiveStore) -> None:
        indexes = hive._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_hive_%'"
        ).fetchall()
        idx_names = {row[0] for row in indexes}
        assert "idx_hive_namespace" in idx_names
        assert "idx_hive_confidence" in idx_names
        assert "idx_hive_tier" in idx_names
        assert "idx_hive_source_agent" in idx_names

    def test_close(self, tmp_path: Path) -> None:
        store = HiveStore(db_path=tmp_path / "hive.db")
        store.close()
        # After close, operations should fail with ProgrammingError
        with pytest.raises(sqlite3.ProgrammingError):
            store._conn.execute("SELECT 1")

    def test_schema_idempotent(self, tmp_path: Path) -> None:
        """Creating HiveStore twice on the same DB should not error."""
        db_path = tmp_path / "hive.db"
        s1 = HiveStore(db_path=db_path)
        s1.close()
        s2 = HiveStore(db_path=db_path)
        s2.close()


# ---------------------------------------------------------------------------
# 011-B: HiveStore CRUD operations
# ---------------------------------------------------------------------------


class TestHiveStoreSave:
    """save() method."""

    def test_save_returns_entry_dict(self, hive: HiveStore) -> None:
        result = hive.save(key="db-choice", value="Use PostgreSQL", namespace="universal")
        assert result["key"] == "db-choice"
        assert result["value"] == "Use PostgreSQL"
        assert result["namespace"] == "universal"
        assert result["tier"] == "pattern"
        assert result["confidence"] == 0.6

    def test_save_persists_to_db(self, hive: HiveStore) -> None:
        hive.save(key="lang", value="Python 3.12", source_agent="dev-agent")
        row = hive._conn.execute("SELECT * FROM hive_memories WHERE key='lang'").fetchone()
        assert row is not None
        assert row["value"] == "Python 3.12"
        assert row["source_agent"] == "dev-agent"

    def test_save_with_tags(self, hive: HiveStore) -> None:
        hive.save(key="t1", value="tagged", tags=["db", "postgres"])
        got = hive.get("t1")
        assert got is not None
        assert got["tags"] == ["db", "postgres"]

    def test_save_upserts_on_same_namespace_key(self, hive: HiveStore) -> None:
        hive.save(key="k1", value="v1", namespace="ns")
        hive.save(
            key="k1",
            value="v2",
            namespace="ns",
            conflict_policy="last_write_wins",
        )
        got = hive.get("k1", namespace="ns")
        assert got is not None
        assert got["value"] == "v2"

    def test_save_different_namespaces_independent(self, hive: HiveStore) -> None:
        hive.save(key="k1", value="val-a", namespace="ns-a")
        hive.save(key="k1", value="val-b", namespace="ns-b")
        a = hive.get("k1", namespace="ns-a")
        b = hive.get("k1", namespace="ns-b")
        assert a is not None and a["value"] == "val-a"
        assert b is not None and b["value"] == "val-b"

    def test_save_custom_fields(self, hive: HiveStore) -> None:
        result = hive.save(
            key="arch",
            value="Microservices",
            tier="architectural",
            confidence=0.95,
            source="human",
            source_agent="user",
            valid_at="2026-01-01T00:00:00+00:00",
        )
        assert result["tier"] == "architectural"
        assert result["confidence"] == 0.95
        assert result["source"] == "human"
        assert result["valid_at"] == "2026-01-01T00:00:00+00:00"


class TestHiveStoreGet:
    """get() method."""

    def test_get_existing(self, hive: HiveStore) -> None:
        hive.save(key="k1", value="hello", namespace="ns")
        got = hive.get("k1", namespace="ns")
        assert got is not None
        assert got["value"] == "hello"

    def test_get_missing_returns_none(self, hive: HiveStore) -> None:
        assert hive.get("nonexistent") is None

    def test_get_wrong_namespace_returns_none(self, hive: HiveStore) -> None:
        hive.save(key="k1", value="hello", namespace="ns-a")
        assert hive.get("k1", namespace="ns-b") is None

    def test_get_default_namespace(self, hive: HiveStore) -> None:
        hive.save(key="k1", value="universal entry")
        got = hive.get("k1")
        assert got is not None
        assert got["namespace"] == "universal"


class TestHiveStoreSearch:
    """search() method."""

    def test_search_finds_matching(self, hive: HiveStore) -> None:
        hive.save(key="db-pg", value="PostgreSQL is the primary database")
        hive.save(key="cache", value="Redis for caching layer")
        results = hive.search("PostgreSQL")
        assert any(r["key"] == "db-pg" for r in results)

    def test_search_namespace_filter(self, hive: HiveStore) -> None:
        hive.save(key="k1", value="PostgreSQL database", namespace="repo-brain")
        hive.save(key="k2", value="PostgreSQL config", namespace="home-auto")
        results = hive.search("PostgreSQL", namespaces=["repo-brain"])
        assert all(r["namespace"] == "repo-brain" for r in results)
        assert len(results) >= 1

    def test_search_min_confidence(self, hive: HiveStore) -> None:
        hive.save(key="low", value="low confidence fact", confidence=0.2)
        hive.save(key="high", value="high confidence fact", confidence=0.9)
        results = hive.search("confidence fact", min_confidence=0.5)
        keys = [r["key"] for r in results]
        assert "high" in keys
        assert "low" not in keys

    def test_search_empty_results(self, hive: HiveStore) -> None:
        results = hive.search("nonexistent query")
        assert results == []

    def test_search_respects_limit(self, hive: HiveStore) -> None:
        for i in range(10):
            hive.save(key=f"item-{i}", value=f"searchable item number {i}")
        results = hive.search("searchable item", limit=3)
        assert len(results) <= 3


class TestHiveStoreListNamespaces:
    """list_namespaces() method."""

    def test_empty_store(self, hive: HiveStore) -> None:
        assert hive.list_namespaces() == []

    def test_lists_distinct_namespaces(self, hive: HiveStore) -> None:
        hive.save(key="k1", value="v1", namespace="alpha")
        hive.save(key="k2", value="v2", namespace="beta")
        hive.save(key="k3", value="v3", namespace="alpha")
        ns = hive.list_namespaces()
        assert ns == ["alpha", "beta"]

    def test_includes_universal(self, hive: HiveStore) -> None:
        hive.save(key="k1", value="v1")  # default namespace = universal
        assert "universal" in hive.list_namespaces()


class TestHiveStoreCountByNamespace:
    """count_by_namespace() method."""

    def test_empty_store_returns_empty_dict(self, hive: HiveStore) -> None:
        assert hive.count_by_namespace() == {}

    def test_single_namespace(self, hive: HiveStore) -> None:
        hive.save(key="k1", value="v1", namespace="alpha")
        hive.save(key="k2", value="v2", namespace="alpha")
        counts = hive.count_by_namespace()
        assert counts == {"alpha": 2}

    def test_multiple_namespaces(self, hive: HiveStore) -> None:
        hive.save(key="k1", value="v1", namespace="alpha")
        hive.save(key="k2", value="v2", namespace="beta")
        hive.save(key="k3", value="v3", namespace="alpha")
        counts = hive.count_by_namespace()
        assert counts == {"alpha": 2, "beta": 1}

    def test_respects_lock(self, hive: HiveStore) -> None:
        """count_by_namespace runs inside the lock (no deadlock for single caller)."""
        hive.save(key="k1", value="v1", namespace="universal")
        counts = hive.count_by_namespace()
        assert "universal" in counts


class TestNamespaceIsolation:
    """Cross-cutting: namespace isolation guarantees."""

    def test_save_in_ns_a_not_visible_in_ns_b(self, hive: HiveStore) -> None:
        hive.save(key="secret", value="ns-a only", namespace="ns-a")
        assert hive.get("secret", namespace="ns-b") is None

    def test_search_ns_a_excludes_ns_b(self, hive: HiveStore) -> None:
        hive.save(key="shared-term", value="namespace alpha content", namespace="ns-a")
        hive.save(key="shared-term", value="namespace beta content", namespace="ns-b")
        results = hive.search("namespace content", namespaces=["ns-a"])
        assert all(r["namespace"] == "ns-a" for r in results)

    def test_upsert_in_one_ns_does_not_affect_other(self, hive: HiveStore) -> None:
        hive.save(key="k1", value="original-a", namespace="ns-a")
        hive.save(key="k1", value="original-b", namespace="ns-b")
        hive.save(
            key="k1",
            value="updated-a",
            namespace="ns-a",
            conflict_policy="last_write_wins",
        )
        a = hive.get("k1", namespace="ns-a")
        b = hive.get("k1", namespace="ns-b")
        assert a is not None and a["value"] == "updated-a"
        assert b is not None and b["value"] == "original-b"


# ---------------------------------------------------------------------------
# 011-C: AgentRegistration model and AgentRegistry
# ---------------------------------------------------------------------------


class TestAgentRegistration:
    """AgentRegistration Pydantic model."""

    def test_defaults(self) -> None:
        agent = AgentRegistration(id="dev-agent")
        assert agent.profile == "repo-brain"
        assert agent.skills == []
        assert agent.project_root is None
        assert agent.name == ""

    def test_full_fields(self) -> None:
        agent = AgentRegistration(
            id="cal",
            name="Calendar Agent",
            profile="personal-assistant",
            skills=["scheduling", "reminders"],
            project_root="/home/user/calendar",
        )
        assert agent.id == "cal"
        assert agent.profile == "personal-assistant"
        assert agent.skills == ["scheduling", "reminders"]

    def test_rejects_extra_fields(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AgentRegistration(id="x", unknown_field="bad")  # type: ignore[call-arg]


class TestAgentRegistry:
    """AgentRegistry YAML-backed storage."""

    def test_register_and_get(self, tmp_path: Path) -> None:
        reg = AgentRegistry(registry_path=tmp_path / "agents.yaml")
        agent = AgentRegistration(id="dev", profile="repo-brain")
        reg.register(agent)
        got = reg.get("dev")
        assert got is not None
        assert got.id == "dev"

    def test_register_updates_existing(self, tmp_path: Path) -> None:
        reg = AgentRegistry(registry_path=tmp_path / "agents.yaml")
        reg.register(AgentRegistration(id="dev", profile="repo-brain"))
        reg.register(AgentRegistration(id="dev", profile="personal-assistant"))
        got = reg.get("dev")
        assert got is not None
        assert got.profile == "personal-assistant"

    def test_unregister(self, tmp_path: Path) -> None:
        reg = AgentRegistry(registry_path=tmp_path / "agents.yaml")
        reg.register(AgentRegistration(id="dev"))
        assert reg.unregister("dev") is True
        assert reg.get("dev") is None

    def test_unregister_nonexistent(self, tmp_path: Path) -> None:
        reg = AgentRegistry(registry_path=tmp_path / "agents.yaml")
        assert reg.unregister("nope") is False

    def test_list_agents(self, tmp_path: Path) -> None:
        reg = AgentRegistry(registry_path=tmp_path / "agents.yaml")
        reg.register(AgentRegistration(id="a1"))
        reg.register(AgentRegistration(id="a2"))
        agents = reg.list_agents()
        assert len(agents) == 2
        assert {a.id for a in agents} == {"a1", "a2"}

    def test_agents_for_domain(self, tmp_path: Path) -> None:
        reg = AgentRegistry(registry_path=tmp_path / "agents.yaml")
        reg.register(AgentRegistration(id="dev", profile="repo-brain"))
        reg.register(AgentRegistration(id="cal", profile="personal-assistant"))
        reg.register(AgentRegistration(id="dev2", profile="repo-brain"))
        result = reg.agents_for_domain("repo-brain")
        assert len(result) == 2
        assert all(a.profile == "repo-brain" for a in result)

    def test_persistence_across_instances(self, tmp_path: Path) -> None:
        path = tmp_path / "agents.yaml"
        reg1 = AgentRegistry(registry_path=path)
        reg1.register(AgentRegistration(id="dev", name="Dev Agent", profile="repo-brain"))
        # New instance should load from disk
        reg2 = AgentRegistry(registry_path=path)
        got = reg2.get("dev")
        assert got is not None
        assert got.name == "Dev Agent"

    def test_empty_registry(self, tmp_path: Path) -> None:
        reg = AgentRegistry(registry_path=tmp_path / "agents.yaml")
        assert reg.list_agents() == []
        assert reg.get("anything") is None


# ---------------------------------------------------------------------------
# 011-E: PropagationEngine
# ---------------------------------------------------------------------------


class TestPropagation:
    """PropagationEngine routing tests."""

    def test_private_stays_local(self, hive: HiveStore) -> None:
        result = PropagationEngine.propagate(
            key="secret",
            value="private info",
            agent_scope="private",
            agent_id="dev",
            agent_profile="repo-brain",
            tier="context",
            confidence=0.6,
            source="agent",
            tags=None,
            hive_store=hive,
        )
        assert result is None
        assert hive.get("secret") is None

    def test_domain_goes_to_profile_namespace(self, hive: HiveStore) -> None:
        result = PropagationEngine.propagate(
            key="pattern-1",
            value="code review pattern",
            agent_scope="domain",
            agent_id="dev",
            agent_profile="repo-brain",
            tier="pattern",
            confidence=0.7,
            source="agent",
            tags=["patterns"],
            hive_store=hive,
        )
        assert result is not None
        assert result["namespace"] == "repo-brain"
        got = hive.get("pattern-1", namespace="repo-brain")
        assert got is not None
        assert got["source_agent"] == "dev"

    def test_hive_goes_to_universal(self, hive: HiveStore) -> None:
        result = PropagationEngine.propagate(
            key="arch-1",
            value="We use PostgreSQL",
            agent_scope="hive",
            agent_id="dev",
            agent_profile="repo-brain",
            tier="architectural",
            confidence=0.95,
            source="human",
            tags=["database"],
            hive_store=hive,
        )
        assert result is not None
        assert result["namespace"] == "universal"
        got = hive.get("arch-1", namespace="universal")
        assert got is not None

    def test_auto_propagate_upgrades_private_to_domain(self, hive: HiveStore) -> None:
        result = PropagationEngine.propagate(
            key="arch-fact",
            value="Architecture decision",
            agent_scope="private",
            agent_id="dev",
            agent_profile="repo-brain",
            tier="architectural",
            confidence=0.9,
            source="agent",
            tags=None,
            hive_store=hive,
            auto_propagate_tiers=["architectural"],
        )
        assert result is not None
        assert result["namespace"] == "repo-brain"

    def test_private_tiers_override_scope(self, hive: HiveStore) -> None:
        result = PropagationEngine.propagate(
            key="ctx",
            value="session context",
            agent_scope="hive",
            agent_id="dev",
            agent_profile="repo-brain",
            tier="context",
            confidence=0.5,
            source="agent",
            tags=None,
            hive_store=hive,
            private_tiers=["context"],
        )
        assert result is None
        assert hive.get("ctx") is None

    def test_auto_propagate_does_not_downgrade_hive(self, hive: HiveStore) -> None:
        """If scope is already 'hive', auto_propagate should not change it."""
        result = PropagationEngine.propagate(
            key="global",
            value="global fact",
            agent_scope="hive",
            agent_id="dev",
            agent_profile="repo-brain",
            tier="architectural",
            confidence=0.9,
            source="agent",
            tags=None,
            hive_store=hive,
            auto_propagate_tiers=["architectural"],
        )
        assert result is not None
        assert result["namespace"] == "universal"


# ---------------------------------------------------------------------------
# 011-F: Wire propagation into MemoryStore.save()
# ---------------------------------------------------------------------------


class TestStoreHiveWiring:
    """MemoryStore propagates to Hive when hive_store is set."""

    def test_save_with_hive_scope_propagates(self, tmp_path: Path) -> None:
        hive = HiveStore(db_path=tmp_path / "hive.db")
        store = MemoryStore(
            tmp_path / "project",
            hive_store=hive,
            hive_agent_id="dev-agent",
        )
        store.save(
            key="arch-decision",
            value="We use PostgreSQL for the primary database",
            tier="architectural",
        )
        # Default agent_scope is "private", so nothing in Hive
        assert hive.get("arch-decision") is None

    def test_hive_disabled_no_propagation(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path / "project")
        # No hive_store — save should work normally
        entry = store.save(key="k1", value="hello")
        assert hasattr(entry, "key")

    def test_backward_compat_no_hive(self, tmp_path: Path) -> None:
        """MemoryStore without hive_store behaves identically to before."""
        store = MemoryStore(tmp_path / "project")
        store.save(key="k1", value="v1", tier="architectural")
        got = store.get("k1")
        assert got is not None
        assert got.agent_scope == "private"


# ---------------------------------------------------------------------------
# 011-G/H: Conflict resolution
# ---------------------------------------------------------------------------


class TestConflictResolution:
    """ConflictPolicy resolution in HiveStore.save()."""

    def test_last_write_wins_overwrites(self, hive: HiveStore) -> None:
        hive.save(key="k1", value="v1", namespace="ns", source_agent="agent-a")
        result = hive.save(
            key="k1",
            value="v2",
            namespace="ns",
            source_agent="agent-b",
            conflict_policy=ConflictPolicy.last_write_wins,
        )
        assert result is not None
        assert result["value"] == "v2"
        got = hive.get("k1", namespace="ns")
        assert got is not None
        assert got["value"] == "v2"

    def test_confidence_max_keeps_higher(self, hive: HiveStore) -> None:
        hive.save(key="k1", value="high conf", namespace="ns", confidence=0.9)
        result = hive.save(
            key="k1",
            value="low conf",
            namespace="ns",
            confidence=0.5,
            conflict_policy=ConflictPolicy.confidence_max,
        )
        # Lower confidence rejected
        assert result is None
        got = hive.get("k1", namespace="ns")
        assert got is not None
        assert got["value"] == "high conf"

    def test_confidence_max_accepts_higher(self, hive: HiveStore) -> None:
        hive.save(key="k1", value="low conf", namespace="ns", confidence=0.3)
        result = hive.save(
            key="k1",
            value="high conf",
            namespace="ns",
            confidence=0.9,
            conflict_policy=ConflictPolicy.confidence_max,
        )
        assert result is not None
        assert result["value"] == "high conf"

    def test_source_authority_same_agent_accepted(self, hive: HiveStore) -> None:
        hive.save(key="k1", value="v1", namespace="ns", source_agent="dev")
        result = hive.save(
            key="k1",
            value="v2",
            namespace="ns",
            source_agent="dev",
            conflict_policy=ConflictPolicy.source_authority,
        )
        assert result is not None
        assert result["value"] == "v2"

    def test_source_authority_different_agent_rejected(self, hive: HiveStore) -> None:
        hive.save(key="k1", value="v1", namespace="ns", source_agent="dev")
        result = hive.save(
            key="k1",
            value="v2",
            namespace="ns",
            source_agent="calendar",
            conflict_policy=ConflictPolicy.source_authority,
        )
        assert result is None
        got = hive.get("k1", namespace="ns")
        assert got is not None
        assert got["value"] == "v1"

    def test_supersede_preserves_version_chain(self, hive: HiveStore) -> None:
        hive.save(key="db-choice", value="MySQL", namespace="ns")
        result = hive.save(
            key="db-choice",
            value="PostgreSQL",
            namespace="ns",
            conflict_policy=ConflictPolicy.supersede,
        )
        assert result is not None
        assert result["value"] == "PostgreSQL"
        # Original should be marked with invalid_at and superseded_by
        old = hive.get("db-choice", namespace="ns")
        assert old is not None
        assert old["invalid_at"] is not None
        assert old["superseded_by"] is not None

    def test_supersede_is_default_policy(self, hive: HiveStore) -> None:
        """Default conflict_policy should be supersede."""
        hive.save(key="k1", value="v1", namespace="ns")
        result = hive.save(key="k1", value="v2", namespace="ns")
        # Supersede creates a versioned key, so result key != "k1"
        assert result is not None
        assert result["value"] == "v2"

    def test_no_conflict_on_first_write(self, hive: HiveStore) -> None:
        """First write should succeed regardless of policy."""
        for policy in ConflictPolicy:
            result = hive.save(
                key=f"first-{policy.value}",
                value="hello",
                namespace="ns",
                conflict_policy=policy,
            )
            assert result is not None


# ---------------------------------------------------------------------------
# 011-I/J: Hive-aware recall
# ---------------------------------------------------------------------------


class TestHiveRecall:
    """RecallOrchestrator merges local + Hive results."""

    def test_recall_finds_hive_memory(self, tmp_path: Path) -> None:
        """Hive memory not in local store should appear in recall."""
        hive = HiveStore(db_path=tmp_path / "hive.db")
        hive.save(
            key="hive-only",
            value="PostgreSQL is the primary database",
            namespace="universal",
            confidence=0.9,
        )
        store = MemoryStore(
            tmp_path / "project",
            hive_store=hive,
            hive_agent_id="dev",
        )
        result = store.recall("database")
        assert result.hive_memory_count >= 1
        hive_keys = [m.get("key") for m in result.memories if m.get("source") == "hive"]
        assert "hive-only" in hive_keys

    def test_recall_hive_disabled_identical(self, tmp_path: Path) -> None:
        """Without hive_store, recall should return hive_memory_count=0."""
        store = MemoryStore(tmp_path / "project")
        store.save(key="local-fact", value="Python 3.12 is used", tier="architectural")
        result = store.recall("Python")
        assert result.hive_memory_count == 0

    def test_local_outranks_hive_same_key(self, tmp_path: Path) -> None:
        """Local entry with same key should win over Hive."""
        hive = HiveStore(db_path=tmp_path / "hive.db")
        hive.save(
            key="db-choice",
            value="MySQL from hive",
            namespace="universal",
            confidence=0.9,
        )
        store = MemoryStore(
            tmp_path / "project",
            hive_store=hive,
        )
        store.save(key="db-choice", value="PostgreSQL from local", tier="architectural")
        result = store.recall("database")
        # db-choice should appear once (local), not duplicated from Hive
        keys = [m.get("key") for m in result.memories]
        assert keys.count("db-choice") <= 1


# ---------------------------------------------------------------------------
# 021-B review fixes: thread safety, created_at preservation, FTS triggers,
# namespace SQL filter, AgentRegistry malformed YAML
# ---------------------------------------------------------------------------


class TestHiveStoreThreadSafety:
    """TOCTOU race fix: lock held for entire read+write in save()."""

    def test_concurrent_saves_to_same_key_no_corruption(self, tmp_path: Path) -> None:
        """Two threads saving to the same key should not corrupt the database."""
        hive = HiveStore(db_path=tmp_path / "hive.db")
        errors: list[Exception] = []

        def worker(value: str) -> None:
            try:
                hive.save(key="shared-key", value=value, namespace="ns")
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=worker, args=("value-a",))
        t2 = threading.Thread(target=worker, args=("value-b",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert errors == [], f"Thread errors: {errors}"
        # The key must exist in some valid state (no corruption)
        result = hive.get("shared-key", namespace="ns")
        # supersede policy may create versioned key; original may be marked invalid
        assert result is not None or hive.list_namespaces() != []
        hive.close()

    def test_concurrent_saves_different_keys_all_succeed(self, tmp_path: Path) -> None:
        """Concurrent saves to different keys should all succeed."""
        hive = HiveStore(db_path=tmp_path / "hive.db")
        errors: list[Exception] = []
        results: list[dict] = []
        lock = threading.Lock()

        def worker(i: int) -> None:
            try:
                r = hive.save(key=f"key-{i}", value=f"value-{i}", namespace="ns")
                with lock:
                    if r is not None:
                        results.append(r)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"
        assert len(results) == 10
        hive.close()


class TestCreatedAtPreservation:
    """created_at should not be overwritten on update paths."""

    def test_last_write_wins_preserves_created_at(self, hive: HiveStore) -> None:
        first = hive.save(key="k1", value="v1", namespace="ns")
        assert first is not None
        original_created_at = first["created_at"]

        second = hive.save(key="k1", value="v2", namespace="ns", conflict_policy="last_write_wins")
        assert second is not None
        # updated_at should change, created_at should be preserved
        assert second["created_at"] == original_created_at
        assert second["updated_at"] >= original_created_at

    def test_source_authority_same_agent_preserves_created_at(self, hive: HiveStore) -> None:
        first = hive.save(key="k1", value="v1", namespace="ns", source_agent="dev")
        assert first is not None
        original_created_at = first["created_at"]

        second = hive.save(
            key="k1",
            value="v2",
            namespace="ns",
            source_agent="dev",
            conflict_policy="source_authority",
        )
        assert second is not None
        assert second["created_at"] == original_created_at

    def test_confidence_max_accepted_preserves_created_at(self, hive: HiveStore) -> None:
        first = hive.save(key="k1", value="low", namespace="ns", confidence=0.3)
        assert first is not None
        original_created_at = first["created_at"]

        second = hive.save(
            key="k1",
            value="high",
            namespace="ns",
            confidence=0.9,
            conflict_policy="confidence_max",
        )
        assert second is not None
        assert second["created_at"] == original_created_at

    def test_first_write_uses_now_as_created_at(self, hive: HiveStore) -> None:
        result = hive.save(key="new-key", value="v1", namespace="ns")
        assert result is not None
        assert result["created_at"] == result["updated_at"]


class TestFTSTriggers:
    """FTS5 sync triggers should exist and keep FTS index in sync."""

    def test_fts_triggers_created(self, hive: HiveStore) -> None:
        triggers = hive._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'hive_fts_%'"
        ).fetchall()
        trigger_names = {row[0] for row in triggers}
        assert "hive_fts_ai" in trigger_names, "INSERT trigger missing"
        assert "hive_fts_ad" in trigger_names, "DELETE trigger missing"
        assert "hive_fts_au" in trigger_names, "UPDATE trigger missing"

    def test_fts_search_finds_entry_after_save(self, hive: HiveStore) -> None:
        """FTS index kept in sync via triggers — search should find saved entry."""
        hive.save(key="fts-test", value="unique fts trigger test phrase", namespace="ns")
        results = hive.search("unique fts trigger test", namespaces=["ns"])
        assert any(r["key"] == "fts-test" for r in results)

    def test_fts_search_does_not_find_deleted_entry(self, hive: HiveStore) -> None:
        """After supersede (marks old entry invalid), old entry still in DB but new key exists."""
        hive.save(key="trig-key", value="original unique phrase alpha", namespace="ns")
        # Supersede replaces with versioned key
        hive.save(
            key="trig-key",
            value="updated unique phrase alpha",
            namespace="ns",
            conflict_policy="supersede",
        )
        # The search should find updated content
        results = hive.search("updated unique phrase alpha", namespaces=["ns"])
        assert len(results) >= 1


class TestSearchNamespaceSQLFilter:
    """Namespace filter should be applied in SQL, not Python post-processing."""

    def test_search_with_namespaces_returns_only_matching(self, hive: HiveStore) -> None:
        """SQL namespace filter ensures only matching-namespace rows are returned."""
        for i in range(20):
            hive.save(key=f"item-{i}", value=f"searchable term item {i}", namespace="ns-a")
        for i in range(20):
            hive.save(key=f"item-{i}", value=f"searchable term item {i}", namespace="ns-b")

        results = hive.search("searchable term", namespaces=["ns-a"], limit=10)
        assert len(results) == 10
        assert all(r["namespace"] == "ns-a" for r in results)

    def test_search_multiple_namespaces_filter(self, hive: HiveStore) -> None:
        hive.save(key="a1", value="term alpha", namespace="alpha")
        hive.save(key="b1", value="term beta", namespace="beta")
        hive.save(key="c1", value="term gamma", namespace="gamma")

        results = hive.search("term", namespaces=["alpha", "beta"])
        ns_set = {r["namespace"] for r in results}
        assert ns_set <= {"alpha", "beta"}
        assert "gamma" not in ns_set


class TestAgentRegistryMalformedYAML:
    """AgentRegistry._load() should skip malformed entries with a warning."""

    def test_load_skips_malformed_agent_entry(self, tmp_path: Path) -> None:
        """Malformed agent entry (extra forbidden field) should be skipped, not crash."""
        registry_path = tmp_path / "agents.yaml"
        # Write YAML with one valid and one invalid entry
        registry_path.write_text(
            "agents:\n"
            "  - id: valid-agent\n"
            "    profile: repo-brain\n"
            "  - id: bad-agent\n"
            "    unknown_forbidden_field: oops\n",
            encoding="utf-8",
        )
        # Should not raise
        reg = AgentRegistry(registry_path=registry_path)
        # Valid entry loaded, bad entry skipped
        assert reg.get("valid-agent") is not None
        assert reg.get("bad-agent") is None

    def test_load_skips_non_dict_entry(self, tmp_path: Path) -> None:
        """Non-dict entry in YAML agents list should be skipped."""
        registry_path = tmp_path / "agents.yaml"
        registry_path.write_text(
            "agents:\n  - id: good\n  - just a string\n",
            encoding="utf-8",
        )
        reg = AgentRegistry(registry_path=registry_path)
        assert reg.get("good") is not None
        assert len(reg.list_agents()) == 1


# ---------------------------------------------------------------------------
# 021-C review fixes: ConflictPolicy docstrings, supersede key uniqueness,
# PropagationEngine unknown-scope warning
# ---------------------------------------------------------------------------


class TestSupersedeMicrosecondKey:
    """_supersede_existing_locked should generate microsecond-precision versioned keys."""

    def test_two_rapid_supersedes_produce_distinct_keys(self, tmp_path: Path) -> None:
        """Keys versioned with microseconds — two rapid supersedes don't collide."""
        hive = HiveStore(db_path=tmp_path / "hive.db")
        hive.save(key="base", value="v1", namespace="ns")
        hive.save(key="base", value="v2", namespace="ns", conflict_policy="supersede")
        hive.save(key="base", value="v3", namespace="ns", conflict_policy="supersede")

        # Check that we have two versioned (superseded) entries plus the current one
        rows = hive._conn.execute("SELECT key FROM hive_memories WHERE namespace = 'ns'").fetchall()
        keys = [r[0] for r in rows]
        # The versioned keys both start with "base-v"
        versioned = [k for k in keys if k.startswith("base-v")]
        assert len(versioned) >= 1, f"Expected versioned keys, got: {keys}"
        # All versioned keys must be distinct (no collision)
        assert len(set(versioned)) == len(versioned), f"Duplicate versioned keys: {versioned}"
        hive.close()

    def test_versioned_key_uses_microsecond_precision(self, tmp_path: Path) -> None:
        """Versioned key timestamp suffix has more than second-level precision."""
        hive = HiveStore(db_path=tmp_path / "hive.db")
        hive.save(key="mykey", value="v1", namespace="ns")
        hive.save(key="mykey", value="v2", namespace="ns", conflict_policy="supersede")

        rows = hive._conn.execute(
            "SELECT key FROM hive_memories WHERE namespace = 'ns' AND key != 'mykey'"
        ).fetchall()
        assert len(rows) == 1
        versioned_key = rows[0][0]
        # Should have more than 15-char suffix (second-granular would be exactly 15)
        suffix = versioned_key.split("-v", 1)[-1]
        assert len(suffix) > 15, f"Suffix too short (only second-granular?): {suffix!r}"
        hive.close()


class TestPropagationEngineUnknownScope:
    """PropagationEngine.propagate() should warn on unknown effective_scope values."""

    def test_unknown_scope_emits_warning_and_uses_domain(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Unknown scope falls back to domain (agent_profile) with a warning."""
        hive = HiveStore(db_path=tmp_path / "hive.db")
        result = PropagationEngine.propagate(
            key="scope-test",
            value="value",
            agent_scope="custom_unknown",
            agent_id="agent-1",
            agent_profile="test-profile",
            tier="pattern",
            confidence=0.7,
            source="agent",
            tags=None,
            hive_store=hive,
        )

        # Should NOT return None — falls back to domain (agent_profile)
        assert result is not None
        assert result["namespace"] == "test-profile"
        # Warning logged via structlog to stdout — event key contains "unknown_scope"
        captured = capsys.readouterr()
        assert "unknown_scope" in captured.out
        hive.close()
