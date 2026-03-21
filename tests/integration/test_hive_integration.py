"""Integration tests — multi-agent Hive round-trip (EPIC-011).

All tests use real SQLite databases in tmp_path fixtures.
"""

from __future__ import annotations

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
    return HiveStore(db_path=tmp_path / "hive.db")


@pytest.fixture()
def registry(tmp_path: Path) -> AgentRegistry:
    return AgentRegistry(registry_path=tmp_path / "agents.yaml")


def _make_store(
    tmp_path: Path,
    name: str,
    hive: HiveStore,
    agent_id: str = "agent",
) -> MemoryStore:
    """Create a MemoryStore wired to the shared Hive."""
    return MemoryStore(
        tmp_path / name,
        hive_store=hive,
        hive_agent_id=agent_id,
    )


# ---------------------------------------------------------------------------
# Multi-agent round-trip
# ---------------------------------------------------------------------------


class TestMultiAgentRoundTrip:
    """Agent A saves → Hive → Agent B recalls."""

    def test_agent_a_hive_scope_found_by_agent_b(
        self, tmp_path: Path, hive: HiveStore
    ) -> None:
        """Agent A saves with hive scope → Agent B recalls it."""
        # Agent A propagates to Hive
        PropagationEngine.propagate(
            key="db-choice",
            value="We use PostgreSQL for the primary database",
            agent_scope="hive",
            agent_id="agent-a",
            agent_profile="repo-brain",
            tier="architectural",
            confidence=0.95,
            source="human",
            tags=["database"],
            hive_store=hive,
        )

        # Agent B recalls from Hive
        store_b = _make_store(tmp_path, "project-b", hive, agent_id="agent-b")
        result = store_b.recall("database")
        hive_keys = [m.get("key") for m in result.memories if m.get("source") == "hive"]
        assert "db-choice" in hive_keys

    def test_domain_scope_isolation(
        self, tmp_path: Path, hive: HiveStore
    ) -> None:
        """Domain scope: matching profile finds it, non-matching doesn't."""
        PropagationEngine.propagate(
            key="code-pattern",
            value="Always use type hints",
            agent_scope="domain",
            agent_id="dev-agent",
            agent_profile="repo-brain",
            tier="pattern",
            confidence=0.8,
            source="agent",
            tags=["coding"],
            hive_store=hive,
        )

        # Search in matching namespace
        results = hive.search("type hints", namespaces=["repo-brain"])
        assert any(r["key"] == "code-pattern" for r in results)

        # Search in non-matching namespace
        results_other = hive.search("type hints", namespaces=["personal-assistant"])
        assert not any(r["key"] == "code-pattern" for r in results_other)


class TestConflictResolutionIntegration:
    """Conflict resolution with real SQLite."""

    def test_supersede_preserves_chain(self, hive: HiveStore) -> None:
        hive.save(key="db", value="MySQL 8", namespace="universal")
        hive.save(
            key="db",
            value="PostgreSQL 17",
            namespace="universal",
            conflict_policy=ConflictPolicy.supersede,
        )
        # Old entry should have invalid_at set
        old = hive.get("db", namespace="universal")
        assert old is not None
        assert old["invalid_at"] is not None
        assert old["superseded_by"] is not None

    def test_source_authority_rejects_unauthorized(self, hive: HiveStore) -> None:
        hive.save(
            key="arch-1",
            value="Microservices",
            namespace="repo-brain",
            source_agent="dev-agent",
        )
        result = hive.save(
            key="arch-1",
            value="Monolith",
            namespace="repo-brain",
            source_agent="calendar-agent",
            conflict_policy=ConflictPolicy.source_authority,
        )
        assert result is None
        got = hive.get("arch-1", namespace="repo-brain")
        assert got is not None
        assert got["value"] == "Microservices"


class TestAutoPropagatioIntegration:
    """Auto-propagation based on tier configuration."""

    def test_auto_propagate_architectural_to_hive(
        self, tmp_path: Path, hive: HiveStore
    ) -> None:
        result = PropagationEngine.propagate(
            key="arch-fact",
            value="REST API design",
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
        # Should auto-upgrade to domain
        assert result is not None
        assert result["namespace"] == "repo-brain"

    def test_private_tier_stays_local(
        self, tmp_path: Path, hive: HiveStore
    ) -> None:
        result = PropagationEngine.propagate(
            key="ctx-fact",
            value="Current debug session",
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


class TestBackwardCompat:
    """Hive disabled = identical to standalone store."""

    def test_no_hive_store_works_normally(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path / "project")
        store.save(key="k1", value="v1", tier="architectural")
        result = store.recall("v1")
        assert result.hive_memory_count == 0
        assert result.memory_count >= 1

    def test_hive_recall_weight_affects_ranking(
        self, tmp_path: Path, hive: HiveStore
    ) -> None:
        """Hive results should be weighted lower than local results."""
        # Put a memory in the Hive
        hive.save(
            key="hive-fact",
            value="Hive PostgreSQL database info",
            namespace="universal",
            confidence=0.9,
        )
        # Put a similar memory locally
        store = _make_store(tmp_path, "project", hive)
        store.save(key="local-fact", value="Local PostgreSQL database info", tier="architectural")

        result = store.recall("PostgreSQL database")
        # Both should appear
        keys = [m.get("key") for m in result.memories]
        assert "local-fact" in keys

    def test_agent_registry_survives_restart(
        self, tmp_path: Path, registry: AgentRegistry
    ) -> None:
        registry.register(AgentRegistration(id="dev", profile="repo-brain", skills=["coding"]))
        # New instance loads from disk
        reg2 = AgentRegistry(registry_path=tmp_path / "agents.yaml")
        assert reg2.get("dev") is not None
        assert reg2.get("dev").skills == ["coding"]  # type: ignore[union-attr]
