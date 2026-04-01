"""Integration tests for cross-project federation with real MemoryStore + SQLite.

Uses real MemoryStore instances (no mocks), real SQLite/FTS5, and real
federation hub. All databases use tmp_path for isolation.

Story: STORY-002.5 from EPIC-002
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tapps_brain.federation import (
    FederatedStore,
    add_subscription,
    federated_search,
    load_federation_config,
    register_project,
    sync_from_hub,
    sync_to_hub,
    unregister_project,
)
from tapps_brain.store import MemoryStore

if TYPE_CHECKING:
    from pathlib import Path

    from tapps_brain.models import MemoryEntry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_federation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect federation config dir to tmp_path so tests never touch real fs."""
    monkeypatch.setattr("tapps_brain.federation._DEFAULT_HUB_DIR", tmp_path)


@pytest.fixture()
def hub_path(tmp_path: Path) -> Path:
    """Path for the shared federation hub database."""
    return tmp_path / "hub" / "federated.db"


@pytest.fixture()
def hub_store(hub_path: Path) -> FederatedStore:
    """Create a FederatedStore backed by a temp directory."""
    store = FederatedStore(db_path=hub_path)
    yield store
    store.close()


@pytest.fixture()
def store_a(tmp_path: Path) -> MemoryStore:
    """MemoryStore for Project A."""
    s = MemoryStore(tmp_path / "project_a")
    yield s
    s.close()


@pytest.fixture()
def store_b(tmp_path: Path) -> MemoryStore:
    """MemoryStore for Project B."""
    s = MemoryStore(tmp_path / "project_b")
    yield s
    s.close()


def _register_both(tmp_path: Path) -> None:
    """Register both projects in the federation config."""
    register_project("project-a", str(tmp_path / "project_a"), tags=["python"])
    register_project("project-b", str(tmp_path / "project_b"), tags=["python"])


# ---------------------------------------------------------------------------
# 1. Two-project publish and sync round-trip
# ---------------------------------------------------------------------------


class TestPublishAndSync:
    """Project A publishes shared entries, Project B syncs them from the hub."""

    def test_publish_then_sync_delivers_entries(
        self,
        tmp_path: Path,
        store_a: MemoryStore,
        store_b: MemoryStore,
        hub_store: FederatedStore,
    ) -> None:
        # Project A saves shared-scope entries
        store_a.save(
            key="api-pattern", value="Use REST conventions", scope="shared", tier="pattern"
        )
        store_a.save(
            key="db-pattern", value="Use connection pooling", scope="shared", tier="pattern"
        )
        # Also a project-scoped entry that should NOT be published
        store_a.save(key="local-only", value="Not shared", scope="project", tier="context")

        _register_both(tmp_path)
        add_subscription("project-b", sources=["project-a"])

        # Sync A -> hub
        pub_result = sync_to_hub(
            store_a, hub_store, project_id="project-a", project_root=str(tmp_path / "project_a")
        )
        assert pub_result["published"] == 2

        # Sync hub -> B
        config = load_federation_config()
        pull_result = sync_from_hub(store_b, hub_store, project_id="project-b", config=config)
        assert pull_result["imported"] == 2
        assert pull_result["conflicts"] == 0

        # Verify entries arrived in B
        api = store_b.get("api-pattern")
        assert api is not None
        assert "REST conventions" in api.value
        assert "federated" in api.tags
        assert "from:project-a" in api.tags

        db = store_b.get("db-pattern")
        assert db is not None

        # local-only should NOT be in B
        assert store_b.get("local-only") is None

    def test_publish_then_sync_preserves_memory_group(
        self,
        tmp_path: Path,
        store_a: MemoryStore,
        store_b: MemoryStore,
        hub_store: FederatedStore,
    ) -> None:
        store_a.save(
            key="grp-k",
            value="grouped shared memory",
            scope="shared",
            tier="pattern",
            memory_group="team-fed",
        )
        _register_both(tmp_path)
        add_subscription("project-b", sources=["project-a"])

        sync_to_hub(
            store_a, hub_store, project_id="project-a", project_root=str(tmp_path / "project_a")
        )
        hub_rows = hub_store.get_project_entries("project-a")
        assert len(hub_rows) == 1
        assert hub_rows[0].get("memory_group") == "team-fed"

        config = load_federation_config()
        pull = sync_from_hub(store_b, hub_store, project_id="project-b", config=config)
        assert pull["imported"] == 1

        got = store_b.get("grp-k")
        assert got is not None
        assert got.memory_group == "team-fed"

    def test_hub_contains_published_entries(
        self,
        tmp_path: Path,
        store_a: MemoryStore,
        hub_store: FederatedStore,
    ) -> None:
        store_a.save(
            key="arch-decision", value="Monorepo layout", scope="shared", tier="architectural"
        )
        _register_both(tmp_path)

        sync_to_hub(store_a, hub_store, project_id="project-a")

        entries = hub_store.get_project_entries("project-a")
        assert len(entries) == 1
        assert entries[0]["key"] == "arch-decision"


# ---------------------------------------------------------------------------
# 2. Local-wins conflict resolution
# ---------------------------------------------------------------------------


class TestConflictResolution:
    """When both projects have the same key, local always wins on sync."""

    def test_local_entry_prevents_import(
        self,
        tmp_path: Path,
        store_a: MemoryStore,
        store_b: MemoryStore,
        hub_store: FederatedStore,
    ) -> None:
        # Both projects have the same key
        store_a.save(key="shared-key", value="from project A", scope="shared", tier="pattern")
        store_b.save(
            key="shared-key", value="from project B (local)", scope="project", tier="pattern"
        )

        _register_both(tmp_path)
        add_subscription("project-b", sources=["project-a"])

        sync_to_hub(store_a, hub_store, project_id="project-a")

        config = load_federation_config()
        result = sync_from_hub(store_b, hub_store, project_id="project-b", config=config)
        assert result["conflicts"] == 1
        assert result["imported"] == 0

        # B's local version is preserved
        entry = store_b.get("shared-key")
        assert entry is not None
        assert entry.value == "from project B (local)"

    def test_both_projects_publish_same_key_coexist_in_hub(
        self,
        tmp_path: Path,
        store_a: MemoryStore,
        store_b: MemoryStore,
        hub_store: FederatedStore,
    ) -> None:
        store_a.save(key="shared-key", value="A version", scope="shared", tier="pattern")
        store_b.save(key="shared-key", value="B version", scope="shared", tier="pattern")

        _register_both(tmp_path)

        sync_to_hub(store_a, hub_store, project_id="project-a")
        sync_to_hub(store_b, hub_store, project_id="project-b")

        # Hub has both (composite PK is project_id + key)
        stats = hub_store.get_stats()
        assert stats["total_entries"] == 2

        a_entries = hub_store.get_project_entries("project-a")
        b_entries = hub_store.get_project_entries("project-b")
        assert a_entries[0]["value"] == "A version"
        assert b_entries[0]["value"] == "B version"


# ---------------------------------------------------------------------------
# 3. Federated search with local boost
# ---------------------------------------------------------------------------


class TestFederatedSearch:
    """federated_search combines local + hub results; local gets 1.2x boost."""

    def test_local_results_boosted_above_hub(
        self,
        tmp_path: Path,
        store_a: MemoryStore,
        hub_store: FederatedStore,
    ) -> None:
        # Local entry
        store_a.save(
            key="auth-pattern",
            value="JWT authentication pattern",
            scope="project",
            tier="pattern",
            confidence=0.8,
        )

        # Hub entry from another project with same confidence
        hub_store.publish(
            "project-b",
            [
                _make_hub_entry(
                    key="auth-hub",
                    value="OAuth authentication pattern",
                    confidence=0.8,
                ),
            ],
        )

        results = federated_search(
            "authentication",
            local_store=store_a,
            federated_store=hub_store,
            project_id="project-a",
        )

        local_results = [r for r in results if r.source == "local"]
        hub_results = [r for r in results if r.source == "federated"]

        assert len(local_results) >= 1, "Expected at least one local result"
        assert len(hub_results) >= 1, "Expected at least one hub result"
        # Local score = 0.8 * 1.2 = 0.96; hub score = 0.8
        for lr in local_results:
            assert lr.relevance_score == pytest.approx(0.8 * 1.2, abs=0.01)
        for hr in hub_results:
            assert hr.relevance_score == pytest.approx(0.8, abs=0.01)

        # Local should rank first
        assert results[0].source == "local"

    def test_dedup_local_wins_on_same_key(
        self,
        tmp_path: Path,
        store_a: MemoryStore,
        hub_store: FederatedStore,
    ) -> None:
        store_a.save(
            key="dup-key",
            value="local version of dup",
            scope="project",
            tier="pattern",
        )

        hub_store.publish(
            "project-b",
            [_make_hub_entry(key="dup-key", value="hub version of dup", confidence=0.95)],
        )

        results = federated_search(
            "dup",
            local_store=store_a,
            federated_store=hub_store,
            project_id="project-a",
        )

        dup_results = [r for r in results if r.key == "dup-key"]
        assert len(dup_results) == 1
        assert dup_results[0].source == "local"


# ---------------------------------------------------------------------------
# 4. Subscription filters
# ---------------------------------------------------------------------------


class TestSubscriptionFilters:
    """Subscription tag_filter and min_confidence are respected during sync."""

    def test_tag_filter_blocks_unmatched(
        self,
        tmp_path: Path,
        store_a: MemoryStore,
        store_b: MemoryStore,
        hub_store: FederatedStore,
    ) -> None:
        store_a.save(
            key="api-entry", value="API pattern", scope="shared", tier="pattern", tags=["api"]
        )
        store_a.save(
            key="ui-entry", value="UI pattern", scope="shared", tier="pattern", tags=["ui"]
        )

        _register_both(tmp_path)
        add_subscription("project-b", sources=["project-a"], tag_filter=["api"])

        sync_to_hub(store_a, hub_store, project_id="project-a")
        config = load_federation_config()
        result = sync_from_hub(store_b, hub_store, project_id="project-b", config=config)

        assert result["imported"] == 1
        assert result["skipped"] >= 1

        assert store_b.get("api-entry") is not None
        assert store_b.get("ui-entry") is None

    def test_min_confidence_blocks_low_confidence(
        self,
        tmp_path: Path,
        store_a: MemoryStore,
        store_b: MemoryStore,
        hub_store: FederatedStore,
    ) -> None:
        store_a.save(
            key="high-conf",
            value="High confidence pattern",
            scope="shared",
            tier="pattern",
            confidence=0.9,
        )
        store_a.save(
            key="low-conf",
            value="Low confidence pattern",
            scope="shared",
            tier="pattern",
            confidence=0.3,
        )

        _register_both(tmp_path)
        add_subscription("project-b", sources=["project-a"], min_confidence=0.7)

        sync_to_hub(store_a, hub_store, project_id="project-a")
        config = load_federation_config()
        result = sync_from_hub(store_b, hub_store, project_id="project-b", config=config)

        assert result["imported"] == 1
        assert result["skipped"] >= 1

        assert store_b.get("high-conf") is not None
        assert store_b.get("low-conf") is None

    def test_combined_tag_and_confidence_filters(
        self,
        tmp_path: Path,
        store_a: MemoryStore,
        store_b: MemoryStore,
        hub_store: FederatedStore,
    ) -> None:
        # Only this entry passes both filters
        store_a.save(
            key="passes-both",
            value="High conf API pattern",
            scope="shared",
            tier="pattern",
            confidence=0.9,
            tags=["api"],
        )
        # Fails confidence
        store_a.save(
            key="fails-conf",
            value="Low conf API pattern",
            scope="shared",
            tier="pattern",
            confidence=0.3,
            tags=["api"],
        )
        # Fails tag
        store_a.save(
            key="fails-tag",
            value="High conf UI pattern",
            scope="shared",
            tier="pattern",
            confidence=0.9,
            tags=["ui"],
        )

        _register_both(tmp_path)
        add_subscription("project-b", sources=["project-a"], tag_filter=["api"], min_confidence=0.7)

        sync_to_hub(store_a, hub_store, project_id="project-a")
        config = load_federation_config()
        result = sync_from_hub(store_b, hub_store, project_id="project-b", config=config)

        assert result["imported"] == 1
        assert store_b.get("passes-both") is not None
        assert store_b.get("fails-conf") is None
        assert store_b.get("fails-tag") is None


# ---------------------------------------------------------------------------
# 5. Unregister project
# ---------------------------------------------------------------------------


class TestUnregisterProject:
    """Unregistering removes the project and its subscriptions from config."""

    def test_unregister_removes_project_and_subscriptions(self, tmp_path: Path) -> None:
        _register_both(tmp_path)
        add_subscription("project-a", sources=["project-b"])
        add_subscription("project-b", sources=["project-a"])

        config = unregister_project("project-a")

        project_ids = [p.project_id for p in config.projects]
        assert "project-a" not in project_ids
        assert "project-b" in project_ids

        subscriber_ids = [s.subscriber for s in config.subscriptions]
        assert "project-a" not in subscriber_ids
        # project-b's subscription is still present
        assert "project-b" in subscriber_ids

    def test_unregister_persists_to_disk(self, tmp_path: Path) -> None:
        _register_both(tmp_path)
        unregister_project("project-a")

        # Reload from disk
        config = load_federation_config()
        project_ids = [p.project_id for p in config.projects]
        assert "project-a" not in project_ids
        assert "project-b" in project_ids


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_hub_entry(
    key: str = "test-key",
    value: str = "test value",
    confidence: float = 0.8,
    tags: list[str] | None = None,
) -> MemoryEntry:
    """Create a MemoryEntry suitable for publishing to the hub."""
    from tapps_brain.models import MemoryScope
    from tests.factories import make_entry

    return make_entry(
        key=key,
        value=value,
        confidence=confidence,
        scope=MemoryScope.shared,
        tags=tags,
    )
