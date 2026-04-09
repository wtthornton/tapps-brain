"""Tests for backend factory functions and SQLite adapters (STORY-054.3/4/5)."""

from __future__ import annotations

import pytest

from tapps_brain._protocols import (
    AgentRegistryBackend,
    FederationBackend,
    HiveBackend,
)
from tapps_brain.backends import (
    SqliteAgentRegistryBackend,
    SqliteFederationBackend,
    SqliteHiveBackend,
    create_agent_registry_backend,
    create_federation_backend,
    create_hive_backend,
)

# ---------------------------------------------------------------------------
# Hive backend factory
# ---------------------------------------------------------------------------


class TestCreateHiveBackend:
    def test_default_returns_sqlite(self, tmp_path):
        db = tmp_path / "hive.db"
        backend = create_hive_backend(str(db))
        try:
            assert isinstance(backend, SqliteHiveBackend)
        finally:
            backend.close()

    def test_none_dsn_returns_sqlite(self, tmp_path, monkeypatch):
        # When dsn is None the default path is used; override HOME so we
        # don't touch the real user directory.
        monkeypatch.setenv("HOME", str(tmp_path))
        backend = create_hive_backend(None)
        try:
            assert isinstance(backend, SqliteHiveBackend)
        finally:
            backend.close()

    def test_with_path(self, tmp_path):
        db = tmp_path / "custom.db"
        backend = create_hive_backend(str(db))
        try:
            assert backend._db_path == db
        finally:
            backend.close()

    def test_postgres_returns_postgres_backend(self):
        from tapps_brain.postgres_hive import PostgresHiveBackend

        backend = create_hive_backend("postgres://localhost/brain")
        try:
            assert isinstance(backend, PostgresHiveBackend)
        finally:
            backend.close()

    def test_satisfies_protocol(self, tmp_path):
        db = tmp_path / "hive.db"
        backend = create_hive_backend(str(db))
        try:
            assert isinstance(backend, HiveBackend)
        finally:
            backend.close()


# ---------------------------------------------------------------------------
# Federation backend factory
# ---------------------------------------------------------------------------


class TestCreateFederationBackend:
    def test_default_returns_sqlite(self, tmp_path):
        db = tmp_path / "federated.db"
        backend = create_federation_backend(str(db))
        try:
            assert isinstance(backend, SqliteFederationBackend)
        finally:
            backend.close()

    def test_postgres_returns_postgres_backend(self):
        from tapps_brain.postgres_federation import PostgresFederationBackend

        backend = create_federation_backend("postgres://localhost/brain")
        try:
            assert isinstance(backend, PostgresFederationBackend)
        finally:
            backend.close()

    def test_satisfies_protocol(self, tmp_path):
        db = tmp_path / "federated.db"
        backend = create_federation_backend(str(db))
        try:
            assert isinstance(backend, FederationBackend)
        finally:
            backend.close()


# ---------------------------------------------------------------------------
# Agent Registry backend factory
# ---------------------------------------------------------------------------


class TestCreateAgentRegistryBackend:
    def test_default_returns_sqlite(self, tmp_path):
        path = tmp_path / "agents.yaml"
        backend = create_agent_registry_backend(str(path))
        assert isinstance(backend, SqliteAgentRegistryBackend)

    def test_postgres_returns_postgres_backend(self):
        from tapps_brain.postgres_hive import PostgresAgentRegistry

        backend = create_agent_registry_backend("postgres://localhost/brain")
        assert isinstance(backend, PostgresAgentRegistry)

    def test_satisfies_protocol(self, tmp_path):
        path = tmp_path / "agents.yaml"
        backend = create_agent_registry_backend(str(path))
        assert isinstance(backend, AgentRegistryBackend)


# ---------------------------------------------------------------------------
# Adapter delegation smoke tests
# ---------------------------------------------------------------------------


class TestSqliteHiveBackendDelegation:
    def test_save_and_get(self, tmp_path):
        db = tmp_path / "hive.db"
        backend = SqliteHiveBackend(db_path=db)
        try:
            result = backend.save(key="test-key", value="test-value")
            assert result is not None
            assert result["key"] == "test-key"

            got = backend.get("test-key")
            assert got is not None
            assert got["value"] == "test-value"
        finally:
            backend.close()

    def test_search(self, tmp_path):
        db = tmp_path / "hive.db"
        backend = SqliteHiveBackend(db_path=db)
        try:
            backend.save(key="search-me", value="hello world")
            results = backend.search("hello")
            assert len(results) >= 1
        finally:
            backend.close()

    def test_groups(self, tmp_path):
        db = tmp_path / "hive.db"
        backend = SqliteHiveBackend(db_path=db)
        try:
            backend.create_group("testers", description="Test group")
            groups = backend.list_groups()
            assert any(g["name"] == "testers" for g in groups)

            backend.add_group_member("testers", "agent-1")
            assert backend.agent_is_group_member("testers", "agent-1")

            members = backend.get_group_members("testers")
            assert len(members) == 1

            agent_groups = backend.get_agent_groups("agent-1")
            assert "testers" in agent_groups

            backend.remove_group_member("testers", "agent-1")
            assert not backend.agent_is_group_member("testers", "agent-1")
        finally:
            backend.close()

    def test_confidence(self, tmp_path):
        db = tmp_path / "hive.db"
        backend = SqliteHiveBackend(db_path=db)
        try:
            backend.save(key="conf-key", value="v", namespace="ns1", confidence=0.5)
            assert backend.get_confidence(namespace="ns1", key="conf-key") == pytest.approx(0.5)

            backend.patch_confidence(namespace="ns1", key="conf-key", confidence=0.9)
            assert backend.get_confidence(namespace="ns1", key="conf-key") == pytest.approx(0.9)
        finally:
            backend.close()

    def test_introspection(self, tmp_path):
        db = tmp_path / "hive.db"
        backend = SqliteHiveBackend(db_path=db)
        try:
            backend.save(key="k1", value="v1", namespace="ns-a")
            backend.save(key="k2", value="v2", namespace="ns-b")

            ns = backend.list_namespaces()
            assert "ns-a" in ns
            assert "ns-b" in ns

            by_ns = backend.count_by_namespace()
            assert by_ns.get("ns-a") == 1

            by_agent = backend.count_by_agent()
            assert "unknown" in by_agent
        finally:
            backend.close()

    def test_write_notify(self, tmp_path):
        db = tmp_path / "hive.db"
        backend = SqliteHiveBackend(db_path=db)
        try:
            state = backend.get_write_notify_state()
            assert "revision" in state
        finally:
            backend.close()


class TestSqliteFederationBackendDelegation:
    def test_get_stats(self, tmp_path):
        db = tmp_path / "federated.db"
        backend = SqliteFederationBackend(db_path=db)
        try:
            stats = backend.get_stats()
            assert "total_entries" in stats
            assert stats["total_entries"] == 0
        finally:
            backend.close()

    def test_get_project_entries_empty(self, tmp_path):
        db = tmp_path / "federated.db"
        backend = SqliteFederationBackend(db_path=db)
        try:
            entries = backend.get_project_entries("nonexistent")
            assert entries == []
        finally:
            backend.close()


class TestSqliteAgentRegistryBackendDelegation:
    def test_register_and_list(self, tmp_path):
        from tapps_brain.hive import AgentRegistration

        path = tmp_path / "agents.yaml"
        backend = SqliteAgentRegistryBackend(registry_path=path)

        agent = AgentRegistration(id="a1", name="Agent One")
        backend.register(agent)

        agents = backend.list_agents()
        assert len(agents) == 1
        assert agents[0].id == "a1"

    def test_get_and_unregister(self, tmp_path):
        from tapps_brain.hive import AgentRegistration

        path = tmp_path / "agents.yaml"
        backend = SqliteAgentRegistryBackend(registry_path=path)

        agent = AgentRegistration(id="a2", name="Agent Two", profile="code-review")
        backend.register(agent)

        got = backend.get("a2")
        assert got is not None
        assert got.id == "a2"

        assert backend.unregister("a2") is True
        assert backend.get("a2") is None

    def test_agents_for_domain(self, tmp_path):
        from tapps_brain.hive import AgentRegistration

        path = tmp_path / "agents.yaml"
        backend = SqliteAgentRegistryBackend(registry_path=path)

        backend.register(AgentRegistration(id="a3", profile="testing"))
        backend.register(AgentRegistration(id="a4", profile="code-review"))

        testers = backend.agents_for_domain("testing")
        assert len(testers) == 1
        assert testers[0].id == "a3"
