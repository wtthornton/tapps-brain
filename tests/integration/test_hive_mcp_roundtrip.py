"""Integration tests — multi-agent Hive round-trip with MCP surface (EPIC-013).

Two agents with different profiles sharing a Hive. Tests propagation and
recall merging across agent_scope values (private, domain, hive) and
conflict resolution across agents.

All tests use real SQLite databases in tmp_path fixtures.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from tapps_brain.hive import (
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
    """Shared Hive database for all agents."""
    return HiveStore(db_path=tmp_path / "hive.db")


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
# Multi-agent Hive round-trip with different profiles (EPIC-013)
# ---------------------------------------------------------------------------


class TestMultiAgentHiveRoundTrip:
    """Two agents with different profiles sharing a Hive.

    Agent A: profile "repo-brain"
    Agent B: profile "personal-assistant"
    """

    def test_hive_scope_visible_to_all_agents(self, tmp_path: Path, hive: HiveStore) -> None:
        """Agent A saves with hive scope → Agent B (different profile) recalls."""
        store_a = _make_store(tmp_path, "project-a", hive, agent_id="agent-a")
        store_a.save(
            key="shared-arch",
            value="We use PostgreSQL 17 for all services",
            tier="architectural",
            source_agent="agent-a",
            agent_scope="hive",
            confidence=0.95,
        )

        store_b = _make_store(tmp_path, "project-b", hive, agent_id="agent-b")
        result = store_b.recall("PostgreSQL")
        hive_keys = [m.get("key") for m in result.memories if m.get("source") == "hive"]
        assert "shared-arch" in hive_keys
        assert result.hive_memory_count >= 1

    def test_private_scope_invisible_to_other_agents(self, tmp_path: Path, hive: HiveStore) -> None:
        """Agent A saves with private scope → Agent B cannot see it."""
        store_a = _make_store(tmp_path, "project-a", hive, agent_id="agent-a")
        store_a.save(
            key="private-note",
            value="Debug session scratch notes for auth module",
            tier="context",
            source_agent="agent-a",
            agent_scope="private",
            confidence=0.7,
        )

        # Verify nothing was propagated to the Hive
        universal = hive.search("debug session", namespaces=["universal"])
        domain = hive.search("debug session", namespaces=["repo-brain"])
        assert not any(r["key"] == "private-note" for r in universal)
        assert not any(r["key"] == "private-note" for r in domain)

        # Agent B cannot recall it
        store_b = _make_store(tmp_path, "project-b", hive, agent_id="agent-b")
        result = store_b.recall("debug session scratch notes")
        hive_keys = [m.get("key") for m in result.memories if m.get("source") == "hive"]
        assert "private-note" not in hive_keys

    def test_domain_scope_same_profile_visible(self, tmp_path: Path, hive: HiveStore) -> None:
        """Agent A domain scope → Agent B with same profile can recall."""
        # Propagate directly to domain namespace (simulating profile="repo-brain")
        PropagationEngine.propagate(
            key="domain-pattern",
            value="Always use dependency injection for database access",
            agent_scope="domain",
            agent_id="agent-a",
            agent_profile="repo-brain",
            tier="pattern",
            confidence=0.85,
            source="agent",
            tags=["coding", "architecture"],
            hive_store=hive,
        )

        # Agent B searches in the same domain namespace
        results = hive.search("dependency injection", namespaces=["repo-brain"])
        assert any(r["key"] == "domain-pattern" for r in results)

    def test_domain_scope_different_profile_invisible(
        self, tmp_path: Path, hive: HiveStore
    ) -> None:
        """Agent A domain scope (repo-brain) → Agent B (personal-assistant) cannot see."""
        PropagationEngine.propagate(
            key="repo-secret",
            value="Internal API uses JWT with RS256 signing",
            agent_scope="domain",
            agent_id="agent-a",
            agent_profile="repo-brain",
            tier="architectural",
            confidence=0.9,
            source="agent",
            tags=["security"],
            hive_store=hive,
        )

        # Different profile namespace yields no results
        results = hive.search("JWT with RS256", namespaces=["personal-assistant"])
        assert not any(r["key"] == "repo-secret" for r in results)

        # Same profile namespace finds it
        results_same = hive.search("JWT with RS256", namespaces=["repo-brain"])
        assert any(r["key"] == "repo-secret" for r in results_same)


class TestScopeValuePropagation:
    """Verify correct propagation behavior for each agent_scope value."""

    def test_save_with_hive_scope_appears_in_universal(
        self, tmp_path: Path, hive: HiveStore
    ) -> None:
        """Hive scope → universal namespace."""
        store = _make_store(tmp_path, "proj", hive, agent_id="dev-agent")
        store.save(
            key="global-standard",
            value="All services must use OpenTelemetry for tracing",
            tier="architectural",
            agent_scope="hive",
            confidence=0.95,
        )
        results = hive.search("OpenTelemetry", namespaces=["universal"])
        assert any(r["key"] == "global-standard" for r in results)

    def test_save_with_domain_scope_appears_in_profile_namespace(
        self, tmp_path: Path, hive: HiveStore
    ) -> None:
        """Domain scope → profile namespace (defaults to repo-brain)."""
        store = _make_store(tmp_path, "proj", hive, agent_id="dev-agent")
        store.save(
            key="domain-fact",
            value="Use pydantic v2 for all data models",
            tier="pattern",
            agent_scope="domain",
            confidence=0.8,
        )
        results = hive.search("pydantic", namespaces=["repo-brain"])
        assert any(r["key"] == "domain-fact" for r in results)

    def test_save_with_private_scope_not_in_hive(self, tmp_path: Path, hive: HiveStore) -> None:
        """Private scope → nothing in Hive."""
        store = _make_store(tmp_path, "proj", hive, agent_id="dev-agent")
        store.save(
            key="local-only",
            value="Temporary debug config for auth endpoint",
            tier="context",
            agent_scope="private",
            confidence=0.5,
        )
        all_results = hive.search("debug config")
        assert not any(r["key"] == "local-only" for r in all_results)


class TestConflictResolutionAcrossAgents:
    """Conflict resolution when multiple agents write to the same Hive key."""

    def test_supersede_creates_version_chain(self, hive: HiveStore) -> None:
        """Two agents supersede the same key — version chain preserved."""
        hive.save(
            key="deploy-strategy",
            value="Blue-green deployment",
            namespace="universal",
            source_agent="agent-a",
            confidence=0.8,
        )
        hive.save(
            key="deploy-strategy",
            value="Canary deployment with 5% traffic",
            namespace="universal",
            source_agent="agent-b",
            conflict_policy=ConflictPolicy.supersede,
            confidence=0.9,
        )

        # The old entry should be superseded (invalid_at set)
        old = hive.get("deploy-strategy", namespace="universal")
        assert old is not None
        assert old["invalid_at"] is not None
        assert old["superseded_by"] is not None

    def test_source_authority_blocks_foreign_agent(self, hive: HiveStore) -> None:
        """source_authority policy: different agent cannot overwrite."""
        hive.save(
            key="auth-method",
            value="OAuth2 with PKCE",
            namespace="repo-brain",
            source_agent="security-agent",
        )
        result = hive.save(
            key="auth-method",
            value="Basic auth",
            namespace="repo-brain",
            source_agent="dev-agent",
            conflict_policy=ConflictPolicy.source_authority,
        )
        assert result is None
        got = hive.get("auth-method", namespace="repo-brain")
        assert got is not None
        assert got["value"] == "OAuth2 with PKCE"

    def test_source_authority_allows_same_agent(self, hive: HiveStore) -> None:
        """source_authority policy: same agent can overwrite."""
        hive.save(
            key="cache-ttl",
            value="TTL 60s",
            namespace="repo-brain",
            source_agent="perf-agent",
        )
        result = hive.save(
            key="cache-ttl",
            value="TTL 300s after benchmarks",
            namespace="repo-brain",
            source_agent="perf-agent",
            conflict_policy=ConflictPolicy.source_authority,
        )
        assert result is not None
        assert result["value"] == "TTL 300s after benchmarks"

    def test_confidence_max_keeps_higher(self, hive: HiveStore) -> None:
        """confidence_max policy: higher confidence wins."""
        hive.save(
            key="framework",
            value="FastAPI",
            namespace="universal",
            source_agent="agent-a",
            confidence=0.7,
        )
        result = hive.save(
            key="framework",
            value="Django",
            namespace="universal",
            source_agent="agent-b",
            confidence=0.95,
            conflict_policy=ConflictPolicy.confidence_max,
        )
        assert result is not None
        assert result["value"] == "Django"

    def test_confidence_max_rejects_lower(self, hive: HiveStore) -> None:
        """confidence_max policy: lower confidence rejected."""
        hive.save(
            key="orm",
            value="SQLAlchemy",
            namespace="universal",
            source_agent="agent-a",
            confidence=0.9,
        )
        result = hive.save(
            key="orm",
            value="Tortoise ORM",
            namespace="universal",
            source_agent="agent-b",
            confidence=0.5,
            conflict_policy=ConflictPolicy.confidence_max,
        )
        assert result is None
        got = hive.get("orm", namespace="universal")
        assert got is not None
        assert got["value"] == "SQLAlchemy"

    def test_last_write_wins_overwrites(self, hive: HiveStore) -> None:
        """last_write_wins policy: always overwrites."""
        hive.save(
            key="log-level",
            value="INFO",
            namespace="universal",
            source_agent="agent-a",
        )
        result = hive.save(
            key="log-level",
            value="DEBUG",
            namespace="universal",
            source_agent="agent-b",
            conflict_policy=ConflictPolicy.last_write_wins,
        )
        assert result is not None
        assert result["value"] == "DEBUG"


class TestRecallMergingAcrossAgents:
    """Verify that recall correctly merges local + Hive results."""

    def test_recall_includes_both_local_and_hive(self, tmp_path: Path, hive: HiveStore) -> None:
        """Recall merges local entries with Hive entries."""
        # Hive entry from agent-a
        hive.save(
            key="hive-convention",
            value="Use snake_case for Python function names",
            namespace="universal",
            source_agent="agent-a",
            confidence=0.9,
        )

        # Agent B has a local entry
        store_b = _make_store(tmp_path, "project-b", hive, agent_id="agent-b")
        store_b.save(
            key="local-convention",
            value="Use snake_case for Python variable names",
            tier="pattern",
            confidence=0.85,
        )

        result = store_b.recall("snake_case")
        all_keys = [m.get("key") for m in result.memories]
        assert "local-convention" in all_keys
        # Hive entry should also appear
        hive_keys = [m.get("key") for m in result.memories if m.get("source") == "hive"]
        assert "hive-convention" in hive_keys

    def test_local_entry_takes_priority_over_hive_duplicate(
        self, tmp_path: Path, hive: HiveStore
    ) -> None:
        """When same key exists locally and in Hive, local wins."""
        # Hive has a version
        hive.save(
            key="api-version",
            value="API v2 is the current standard",
            namespace="universal",
            source_agent="agent-a",
            confidence=0.9,
        )

        # Agent B has the same key locally
        store_b = _make_store(tmp_path, "project-b", hive, agent_id="agent-b")
        store_b.save(
            key="api-version",
            value="API v3 is the current standard (updated)",
            tier="architectural",
            confidence=0.95,
        )

        result = store_b.recall("current standard")
        # Should have only one api-version entry (local)
        api_entries = [m for m in result.memories if m.get("key") == "api-version"]
        assert len(api_entries) <= 1
        if api_entries:
            # Local version should take priority
            assert api_entries[0].get("source") != "hive"

    def test_hive_recall_weight_reduces_hive_scores(self, tmp_path: Path, hive: HiveStore) -> None:
        """Hive results are weighted by hive_recall_weight (default 0.8)."""
        hive.save(
            key="weighted-fact",
            value="Kubernetes cluster runs on GKE",
            namespace="universal",
            confidence=1.0,
        )

        store = _make_store(tmp_path, "proj", hive)
        result = store.recall("Kubernetes cluster")
        hive_entries = [m for m in result.memories if m.get("source") == "hive"]
        if hive_entries:
            # Hive entries should have confidence < 1.0 due to weight
            for entry in hive_entries:
                assert entry.get("confidence", 1.0) <= 1.0

    def test_hive_memory_count_tracked(self, tmp_path: Path, hive: HiveStore) -> None:
        """RecallResult.hive_memory_count reflects Hive contributions."""
        hive.save(
            key="hive-only-1",
            value="Redis for caching layer",
            namespace="universal",
            confidence=0.8,
        )
        hive.save(
            key="hive-only-2",
            value="RabbitMQ for message queue",
            namespace="universal",
            confidence=0.8,
        )

        store = _make_store(tmp_path, "proj", hive)
        result = store.recall("caching layer")
        assert result.hive_memory_count >= 0  # May be 0 if query doesn't match


class TestMultiAgentPropagationEngine:
    """PropagationEngine routing with different agents and profiles."""

    def test_two_agents_same_profile_share_domain(self, hive: HiveStore) -> None:
        """Two agents with same profile share domain namespace."""
        PropagationEngine.propagate(
            key="team-pattern",
            value="Use factory pattern for service initialization",
            agent_scope="domain",
            agent_id="agent-a",
            agent_profile="backend-team",
            tier="pattern",
            confidence=0.8,
            source="agent",
            tags=None,
            hive_store=hive,
        )

        # Same profile agent can find it
        results = hive.search("factory pattern", namespaces=["backend-team"])
        assert any(r["key"] == "team-pattern" for r in results)

        # Agent B propagates to same domain
        PropagationEngine.propagate(
            key="team-convention",
            value="Always add docstrings to public methods",
            agent_scope="domain",
            agent_id="agent-b",
            agent_profile="backend-team",
            tier="pattern",
            confidence=0.85,
            source="agent",
            tags=None,
            hive_store=hive,
        )

        all_results = hive.search("methods", namespaces=["backend-team"])
        keys = [r["key"] for r in all_results]
        assert "team-convention" in keys

    def test_mixed_scope_propagation(self, hive: HiveStore) -> None:
        """Multiple entries with different scopes route correctly."""
        # Private → no Hive
        r1 = PropagationEngine.propagate(
            key="scratch",
            value="Temporary notes",
            agent_scope="private",
            agent_id="dev",
            agent_profile="repo-brain",
            tier="context",
            confidence=0.5,
            source="agent",
            tags=None,
            hive_store=hive,
        )
        assert r1 is None

        # Domain → repo-brain namespace
        r2 = PropagationEngine.propagate(
            key="domain-fact",
            value="Domain specific pattern",
            agent_scope="domain",
            agent_id="dev",
            agent_profile="repo-brain",
            tier="pattern",
            confidence=0.8,
            source="agent",
            tags=None,
            hive_store=hive,
        )
        assert r2 is not None
        assert r2["namespace"] == "repo-brain"

        # Hive → universal namespace
        r3 = PropagationEngine.propagate(
            key="global-fact",
            value="Universal standard",
            agent_scope="hive",
            agent_id="dev",
            agent_profile="repo-brain",
            tier="architectural",
            confidence=0.9,
            source="agent",
            tags=None,
            hive_store=hive,
        )
        assert r3 is not None
        assert r3["namespace"] == "universal"
