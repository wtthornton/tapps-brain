"""Integration tests for auto-recall round-trip (EPIC-003, STORY-003.5).

All tests use a real MemoryStore + SQLite (no mocks).
"""

from __future__ import annotations

import pytest

from tapps_brain.models import MemoryScope, RecallResult
from tapps_brain.recall import RecallConfig, RecallOrchestrator
from tapps_brain.store import MemoryStore


@pytest.fixture()
def populated_store(tmp_path):
    """Create a store with 20 entries across tiers and scopes."""
    store = MemoryStore(tmp_path)

    # Architectural (project scope)
    store.save(key="lang-python", value="We use Python 3.12 as the primary language", tier="architectural", source="human")
    store.save(key="db-postgres", value="PostgreSQL 17 is the primary database", tier="architectural", source="human")
    store.save(key="api-framework", value="FastAPI powers all HTTP endpoints", tier="architectural", source="human")
    store.save(key="deploy-aws", value="We deploy to AWS ECS Fargate containers", tier="architectural", source="agent")
    store.save(key="ci-github", value="GitHub Actions handles CI/CD pipelines", tier="architectural", source="agent")

    # Pattern (project scope)
    store.save(key="test-pytest", value="All tests use pytest with strict coverage", tier="pattern", source="human")
    store.save(key="lint-ruff", value="Ruff is used for linting and formatting", tier="pattern", source="human")
    store.save(key="type-mypy", value="Mypy strict mode for type checking", tier="pattern", source="agent")
    store.save(key="err-structured", value="Use structured logging via structlog", tier="pattern", source="agent")
    store.save(key="pr-review", value="All PRs require at least one review", tier="pattern", source="human")

    # Procedural (project scope)
    store.save(key="release-process", value="Cut release branches on Thursdays", tier="procedural", source="human")
    store.save(key="hotfix-process", value="Hotfixes go directly to main via cherry-pick", tier="procedural", source="human")
    store.save(key="onboard-steps", value="New devs follow the onboarding checklist in docs", tier="procedural", source="agent")

    # Context (session scope)
    store.save(key="session-auth", value="Discussing auth module refactoring today", tier="context", source="agent", scope="session")
    store.save(key="session-perf", value="Investigating slow query performance", tier="context", source="agent", scope="session")

    # Context (branch scope)
    store.save(key="branch-auth-v2", value="Rewriting auth with OAuth2 on feature-auth", tier="context", source="agent", scope="branch", branch="feature-auth")
    store.save(key="branch-api-v3", value="API v3 migration on feature-api", tier="context", source="agent", scope="branch", branch="feature-api")

    # Shared scope
    store.save(key="shared-convention", value="All repos use conventional commits", tier="pattern", source="human", scope="shared")
    store.save(key="shared-docker", value="All services use multi-stage Docker builds", tier="pattern", source="human", scope="shared")
    store.save(key="shared-monitoring", value="Grafana dashboards for all services", tier="pattern", source="human", scope="shared")

    return store


class TestRecallWithFilters:
    """Test recall with various filter combinations."""

    def test_recall_finds_matching_entries(self, populated_store):
        orch = RecallOrchestrator(populated_store)
        result = orch.recall("python language tech stack")
        assert isinstance(result, RecallResult)
        assert result.memory_count > 0
        keys = [m["key"] for m in result.memories]
        assert "lang-python" in keys

    def test_recall_with_dedupe_window(self, populated_store):
        orch = RecallOrchestrator(populated_store)

        # First call without dedupe
        result1 = orch.recall("python language")
        keys1 = [m["key"] for m in result1.memories]

        # Second call with dedupe excluding first result
        cfg = RecallConfig(dedupe_window=keys1)
        orch2 = RecallOrchestrator(populated_store, config=cfg)
        result2 = orch2.recall("python language")
        keys2 = [m["key"] for m in result2.memories]

        # Deduped keys should not appear in second result
        for k in keys1:
            assert k not in keys2

    def test_recall_scope_filter_excludes_session(self, populated_store):
        cfg = RecallConfig(scope_filter=MemoryScope.project)
        orch = RecallOrchestrator(populated_store, config=cfg)
        result = orch.recall("auth refactoring session discussing")
        keys = [m["key"] for m in result.memories]
        assert "session-auth" not in keys

    def test_recall_branch_filter(self, populated_store):
        cfg = RecallConfig(branch="feature-auth")
        orch = RecallOrchestrator(populated_store, config=cfg)
        result = orch.recall("auth OAuth2 rewrite branch API migration")
        keys = [m["key"] for m in result.memories]
        # branch-api-v3 is scoped to feature-api, should be excluded
        assert "branch-api-v3" not in keys


class TestRecallThenCapture:
    """Test the full recall → capture round-trip."""

    def test_recall_then_capture_creates_entries(self, populated_store):
        orch = RecallOrchestrator(populated_store)

        # Recall
        result = orch.recall("what database do we use?")
        assert isinstance(result, RecallResult)

        # Capture new facts from a response
        response = (
            "We decided to add Redis as a caching layer in front of PostgreSQL. "
            "Key decision: cache invalidation uses a 5-minute TTL."
        )
        new_keys = orch.capture(response)
        assert len(new_keys) > 0

        # Verify entries exist in the store
        for key in new_keys:
            entry = populated_store.get(key)
            assert entry is not None

    def test_capture_no_duplicates_on_repeat(self, populated_store):
        orch = RecallOrchestrator(populated_store)
        response = "We decided to use gRPC for inter-service communication."
        keys1 = orch.capture(response)
        keys2 = orch.capture(response)
        assert len(keys1) > 0
        assert keys2 == []


class TestTokenBudget:
    """Test token budget enforcement."""

    def test_small_budget_truncates(self, populated_store):
        cfg = RecallConfig(max_tokens=50)
        orch = RecallOrchestrator(populated_store, config=cfg)
        result = orch.recall("python testing deploy AWS PostgreSQL")
        # With 50 tokens, can only fit 1-2 entries max
        assert result.memory_count <= 2


class TestStoreConvenienceMethod:
    """Test MemoryStore.recall() convenience method end-to-end."""

    def test_store_recall_round_trip(self, populated_store):
        result = populated_store.recall("what is our tech stack?")
        assert isinstance(result, RecallResult)
        assert result.recall_time_ms > 0

    def test_store_recall_with_override(self, populated_store):
        result = populated_store.recall("tech stack", engagement_level="low")
        assert result.memory_count == 0


class TestPersistenceRoundTrip:
    """Verify recall results survive store restart."""

    def test_capture_persists_across_restart(self, tmp_path):
        # Create store, capture facts
        store1 = MemoryStore(tmp_path)
        store1.save(key="base-fact", value="We use Python", tier="architectural", source="human")
        orch1 = RecallOrchestrator(store1)
        new_keys = orch1.capture("We decided to adopt Rust for performance-critical services.")
        assert len(new_keys) > 0
        store1.close()

        # Restart store from same directory
        store2 = MemoryStore(tmp_path)
        for key in new_keys:
            entry = store2.get(key)
            assert entry is not None, f"Entry {key} not found after restart"
        store2.close()
