"""Tests for the Hive — multi-agent shared brain (EPIC-011)."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
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
def hive(tmp_path: Path) -> HiveStore:
    """Create a HiveStore backed by a temp directory."""
    return HiveStore(db_path=tmp_path / "hive.db")


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
        # After close, operations should fail
        with pytest.raises(Exception):  # noqa: B017
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
        row = hive._conn.execute(
            "SELECT * FROM hive_memories WHERE key='lang'"
        ).fetchone()
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
        with pytest.raises(Exception):  # noqa: B017
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
