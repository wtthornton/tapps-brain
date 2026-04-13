"""Backend conformance test suite for Hive, Federation, and AgentRegistry.

ADR-007 / EPIC-055 — requires ``TAPPS_TEST_POSTGRES_DSN`` (set in CI).
"""

from __future__ import annotations

import os
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_PG_DSN = os.environ.get("TAPPS_TEST_POSTGRES_DSN", "")
_SKIP_PG = not _PG_DSN


def _make_postgres_hive() -> Any:
    from tapps_brain.postgres_connection import PostgresConnectionManager
    from tapps_brain.postgres_hive import PostgresHiveBackend
    from tapps_brain.postgres_migrations import apply_hive_migrations

    apply_hive_migrations(_PG_DSN)
    cm = PostgresConnectionManager(_PG_DSN)
    return PostgresHiveBackend(cm)


def _make_postgres_federation() -> Any:
    from tapps_brain.postgres_connection import PostgresConnectionManager
    from tapps_brain.postgres_federation import PostgresFederationBackend
    from tapps_brain.postgres_migrations import apply_federation_migrations

    apply_federation_migrations(_PG_DSN)
    cm = PostgresConnectionManager(_PG_DSN)
    return PostgresFederationBackend(cm)


def _make_postgres_agent_registry() -> Any:
    from tapps_brain.postgres_connection import PostgresConnectionManager
    from tapps_brain.postgres_hive import PostgresAgentRegistry
    from tapps_brain.postgres_migrations import apply_hive_migrations

    apply_hive_migrations(_PG_DSN)
    cm = PostgresConnectionManager(_PG_DSN)
    return PostgresAgentRegistry(cm)


@pytest.fixture
def hive_backend() -> Any:
    if _SKIP_PG:
        pytest.skip("TAPPS_TEST_POSTGRES_DSN not set")
    backend = _make_postgres_hive()
    # Clean up shared tables before the test so prior runs don't cause
    # unique-constraint violations or state pollution.
    from tapps_brain.postgres_connection import PostgresConnectionManager

    cm = PostgresConnectionManager(_PG_DSN)
    with cm.get_connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM hive_feedback_events")
        cur.execute("DELETE FROM hive_memories")
    cm.close()
    yield backend
    backend.close()


@pytest.fixture
def federation_backend() -> Any:
    if _SKIP_PG:
        pytest.skip("TAPPS_TEST_POSTGRES_DSN not set")
    backend = _make_postgres_federation()
    yield backend
    backend.close()


@pytest.fixture
def agent_registry() -> Any:
    if _SKIP_PG:
        pytest.skip("TAPPS_TEST_POSTGRES_DSN not set")
    registry = _make_postgres_agent_registry()
    yield registry


# ===========================================================================
# Hive Backend Conformance
# ===========================================================================


class TestHiveBackendConformance:
    """Verify PostgreSQL HiveBackend protocol conformance (ADR-007)."""

    def test_save_and_get(self, hive_backend: Any) -> None:
        result = hive_backend.save(key="k1", value="v1", namespace="universal")
        assert result is not None
        assert result["key"] == "k1"
        assert result["value"] == "v1"

        fetched = hive_backend.get("k1", "universal")
        assert fetched is not None
        assert fetched["key"] == "k1"
        assert fetched["value"] == "v1"

    def test_get_missing_returns_none(self, hive_backend: Any) -> None:
        assert hive_backend.get("nonexistent") is None

    def test_save_with_tags(self, hive_backend: Any) -> None:
        result = hive_backend.save(key="tagged", value="val", tags=["a", "b"])
        assert result is not None
        assert set(result["tags"]) == {"a", "b"}

    def test_search_returns_list(self, hive_backend: Any) -> None:
        hive_backend.save(key="searchable", value="The quick brown fox")
        results = hive_backend.search("fox")
        assert isinstance(results, list)

    def test_patch_confidence(self, hive_backend: Any) -> None:
        hive_backend.save(key="conf-key", value="val", namespace="ns1", confidence=0.5)
        changed = hive_backend.patch_confidence(namespace="ns1", key="conf-key", confidence=0.9)
        assert changed is True

        conf = hive_backend.get_confidence(namespace="ns1", key="conf-key")
        assert conf is not None
        assert abs(conf - 0.9) < 0.01

    def test_get_confidence_missing(self, hive_backend: Any) -> None:
        assert hive_backend.get_confidence(namespace="x", key="y") is None

    def test_groups_lifecycle(self, hive_backend: Any) -> None:
        group = hive_backend.create_group("grp1", "Test group")
        assert group["name"] == "grp1"

        groups = hive_backend.list_groups()
        assert any(g["name"] == "grp1" for g in groups)

        added = hive_backend.add_group_member("grp1", "agent-a")
        assert added is True

        members = hive_backend.get_group_members("grp1")
        assert any(m["agent_id"] == "agent-a" for m in members)

        agent_groups = hive_backend.get_agent_groups("agent-a")
        assert "grp1" in agent_groups

        assert hive_backend.agent_is_group_member("grp1", "agent-a") is True
        assert hive_backend.agent_is_group_member("grp1", "agent-b") is False

        removed = hive_backend.remove_group_member("grp1", "agent-a")
        assert removed is True

    def test_add_member_to_nonexistent_group_returns_false(self, hive_backend: Any) -> None:
        assert hive_backend.add_group_member("no-such-group", "agent-1") is False

    def test_feedback_events(self, hive_backend: Any) -> None:
        hive_backend.record_feedback_event(
            event_id="fb1",
            namespace="universal",
            entry_key="k1",
            event_type="positive",
            session_id="s1",
            utility_score=0.8,
            details={"note": "helpful"},
            timestamp="2025-01-01T00:00:00Z",
        )

        events = hive_backend.query_feedback_events(namespace="universal")
        assert len(events) >= 1
        assert events[0]["id"] == "fb1"

    def test_list_namespaces(self, hive_backend: Any) -> None:
        hive_backend.save(key="ns-test", value="val", namespace="test-ns")
        namespaces = hive_backend.list_namespaces()
        assert "test-ns" in namespaces

    def test_count_by_namespace(self, hive_backend: Any) -> None:
        hive_backend.save(key="cnt-1", value="val", namespace="counted")
        counts = hive_backend.count_by_namespace()
        assert counts.get("counted", 0) >= 1

    def test_count_by_agent(self, hive_backend: Any) -> None:
        hive_backend.save(key="agnt-1", value="val", source_agent="counting-agent")
        counts = hive_backend.count_by_agent()
        assert counts.get("counting-agent", 0) >= 1

    def test_write_notify_state(self, hive_backend: Any) -> None:
        state = hive_backend.get_write_notify_state()
        assert "revision" in state

    def test_search_with_groups(self, hive_backend: Any) -> None:
        hive_backend.save(key="grp-search", value="group searchable content", namespace="universal")
        results = hive_backend.search_with_groups("searchable", "agent-1")
        assert isinstance(results, list)


# ===========================================================================
# Federation Backend Conformance
# ===========================================================================


class TestFederationBackendConformance:
    """Verify PostgreSQL federation backend conformance (ADR-007)."""

    def _make_entry(self, key: str = "entry1", value: str = "test value") -> Any:
        """Create a minimal entry object for publishing."""
        from unittest.mock import MagicMock

        entry = MagicMock()
        entry.key = key
        entry.value = value
        entry.tier = "pattern"
        entry.confidence = 0.7
        entry.source = "agent"
        entry.source_agent = "test-agent"
        entry.tags = ["tag1"]
        entry.created_at = "2025-01-01T00:00:00Z"
        entry.updated_at = "2025-01-01T00:00:00Z"
        entry.memory_group = None
        return entry

    def test_publish_and_get_entries(self, federation_backend: Any) -> None:
        entry = self._make_entry()
        count = federation_backend.publish("proj-1", [entry])
        assert count == 1

        entries = federation_backend.get_project_entries("proj-1")
        assert len(entries) >= 1

    def test_unpublish(self, federation_backend: Any) -> None:
        entry = self._make_entry(key="to-remove")
        federation_backend.publish("proj-2", [entry])

        removed = federation_backend.unpublish("proj-2", keys=["to-remove"])
        assert removed >= 1

    def test_unpublish_all(self, federation_backend: Any) -> None:
        entry = self._make_entry(key="all-remove")
        federation_backend.publish("proj-3", [entry])

        removed = federation_backend.unpublish("proj-3")
        assert removed >= 1

    def test_search_returns_list(self, federation_backend: Any) -> None:
        entry = self._make_entry(key="search-fed", value="the quick brown fox")
        federation_backend.publish("proj-search", [entry])

        results = federation_backend.search("fox")
        assert isinstance(results, list)

    def test_get_stats(self, federation_backend: Any) -> None:
        stats = federation_backend.get_stats()
        assert "total_entries" in stats
        assert "projects" in stats


# ===========================================================================
# Agent Registry Conformance
# ===========================================================================


class TestAgentRegistryConformance:
    """Verify PostgreSQL agent registry conformance (ADR-007)."""

    def _make_agent(self, agent_id: str = "test-agent") -> Any:
        from tapps_brain.models import AgentRegistration

        return AgentRegistration(
            id=agent_id,
            name="Test Agent",
            profile="repo-brain",
            skills=["code-review"],
            project_root="/tmp/test",
        )

    def test_register_and_get(self, agent_registry: Any) -> None:
        agent = self._make_agent("reg-1")
        agent_registry.register(agent)

        found = agent_registry.get("reg-1")
        assert found is not None

    def test_get_missing_returns_none(self, agent_registry: Any) -> None:
        assert agent_registry.get("nonexistent") is None

    def test_unregister(self, agent_registry: Any) -> None:
        agent = self._make_agent("unreg-1")
        agent_registry.register(agent)
        assert agent_registry.unregister("unreg-1") is True
        assert agent_registry.get("unreg-1") is None

    def test_unregister_missing_returns_false(self, agent_registry: Any) -> None:
        assert agent_registry.unregister("not-there") is False

    def test_list_agents(self, agent_registry: Any) -> None:
        agent = self._make_agent("list-1")
        agent_registry.register(agent)

        agents = agent_registry.list_agents()
        assert len(agents) >= 1

    def test_agents_for_domain(self, agent_registry: Any) -> None:
        agent = self._make_agent("domain-1")
        agent.profile = "special-domain"
        agent_registry.register(agent)

        found = agent_registry.agents_for_domain("special-domain")
        assert len(found) >= 1
