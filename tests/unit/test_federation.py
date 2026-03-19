"""Tests for tapps_brain.federation — cross-project memory federation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

from tapps_brain.federation import (
    FederatedSearchResult,
    FederatedStore,
    FederationConfig,
    FederationProject,
    FederationSubscription,
    add_subscription,
    federated_search,
    load_federation_config,
    register_project,
    save_federation_config,
    sync_from_hub,
    sync_to_hub,
    unregister_project,
)
from tapps_brain.models import MemoryEntry, MemoryScope, MemorySource, MemoryTier
from tests.factories import make_entry

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_federation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect federation hub dir to tmp_path to avoid real filesystem pollution."""
    monkeypatch.setattr("tapps_brain.federation._DEFAULT_HUB_DIR", tmp_path)


@pytest.fixture()
def hub_store(tmp_path: Path) -> FederatedStore:
    """Create a FederatedStore backed by tmp_path."""
    store = FederatedStore(db_path=tmp_path / "federated.db")
    yield store
    store.close()


def _make_entry(
    key: str = "test-key",
    value: str = "test value",
    tier: MemoryTier = MemoryTier.pattern,
    confidence: float = 0.8,
    source: MemorySource = MemorySource.agent,
    scope: MemoryScope = MemoryScope.shared,
    tags: list[str] | None = None,
) -> MemoryEntry:
    """Helper to create a MemoryEntry with sensible defaults."""
    return make_entry(
        key=key,
        value=value,
        tier=tier,
        confidence=confidence,
        source=source,
        scope=scope,
        tags=tags,
    )


@dataclass
class MockMemoryStore:
    """Minimal mock implementing the MemoryStore interface used by federation."""

    _entries: dict[str, MemoryEntry]

    def __init__(self, entries: list[MemoryEntry] | None = None) -> None:
        self._entries = {e.key: e for e in (entries or [])}
        self._saved: list[dict[str, Any]] = []

    def list_all(self, scope: str | None = None) -> list[MemoryEntry]:
        if scope is None:
            return list(self._entries.values())
        return [e for e in self._entries.values() if e.scope.value == scope]

    def get(self, key: str) -> MemoryEntry | None:
        return self._entries.get(key)

    def save(self, **kwargs: Any) -> MemoryEntry:
        self._saved.append(kwargs)
        entry = _make_entry(
            key=kwargs["key"],
            value=kwargs["value"],
            scope=MemoryScope.project,
        )
        self._entries[entry.key] = entry
        return entry

    def search(self, query: str) -> list[MemoryEntry]:
        results = []
        for entry in self._entries.values():
            if query.lower() in entry.key.lower() or query.lower() in entry.value.lower():
                results.append(entry)
        return results


# ===========================================================================
# 1. FederationConfig model
# ===========================================================================


class TestFederationConfigModel:
    """Test FederationConfig, FederationProject, FederationSubscription models."""

    def test_defaults(self) -> None:
        config = FederationConfig()
        assert config.hub_path == ""
        assert config.projects == []
        assert config.subscriptions == []

    def test_serialization_round_trip(self) -> None:
        config = FederationConfig(
            hub_path="/custom/path",
            projects=[
                FederationProject(
                    project_id="proj-a",
                    project_root="/tmp/a",
                    registered_at="2026-01-01T00:00:00+00:00",
                    tags=["python", "web"],
                )
            ],
            subscriptions=[
                FederationSubscription(
                    subscriber="proj-a",
                    sources=["proj-b"],
                    tag_filter=["api"],
                    min_confidence=0.7,
                )
            ],
        )
        data = config.model_dump(mode="json")
        restored = FederationConfig(**data)
        assert restored.hub_path == "/custom/path"
        assert len(restored.projects) == 1
        assert restored.projects[0].project_id == "proj-a"
        assert restored.projects[0].tags == ["python", "web"]
        assert len(restored.subscriptions) == 1
        assert restored.subscriptions[0].min_confidence == 0.7

    def test_project_registration_model(self) -> None:
        project = FederationProject(
            project_id="my-proj",
            project_root="/home/user/my-proj",
        )
        assert project.registered_at == ""
        assert project.tags == []

    def test_subscription_defaults(self) -> None:
        sub = FederationSubscription(subscriber="proj-a")
        assert sub.sources == []
        assert sub.tag_filter == []
        assert sub.min_confidence == 0.5

    def test_subscription_min_confidence_bounds(self) -> None:
        with pytest.raises(ValueError):
            FederationSubscription(subscriber="proj-a", min_confidence=-0.1)
        with pytest.raises(ValueError):
            FederationSubscription(subscriber="proj-a", min_confidence=1.1)


# ===========================================================================
# 2. Config file management
# ===========================================================================


class TestConfigFileManagement:
    """Tests for load/save/register/unregister config operations."""

    def test_load_missing_file_returns_defaults(self) -> None:
        config = load_federation_config()
        assert config.projects == []
        assert config.subscriptions == []

    def test_save_and_reload_round_trip(self) -> None:
        config = FederationConfig(hub_path="/test/path")
        config.projects.append(
            FederationProject(
                project_id="proj-x",
                project_root="/tmp/x",
                registered_at="2026-03-01T00:00:00+00:00",
            )
        )
        save_federation_config(config)
        reloaded = load_federation_config()
        assert reloaded.hub_path == "/test/path"
        assert len(reloaded.projects) == 1
        assert reloaded.projects[0].project_id == "proj-x"

    def test_register_project_creates_entry(self) -> None:
        config = register_project("proj-a", "/tmp/a", tags=["python"])
        assert len(config.projects) == 1
        assert config.projects[0].project_id == "proj-a"
        assert config.projects[0].tags == ["python"]
        assert config.projects[0].registered_at != ""

    def test_register_project_updates_existing(self) -> None:
        register_project("proj-a", "/tmp/a", tags=["old"])
        config = register_project("proj-a", "/tmp/a-new", tags=["new"])
        assert len(config.projects) == 1
        assert config.projects[0].project_root == "/tmp/a-new"
        assert config.projects[0].tags == ["new"]

    def test_register_project_idempotent_without_tags(self) -> None:
        register_project("proj-a", "/tmp/a", tags=["keep"])
        config = register_project("proj-a", "/tmp/a")
        # tags=None should not overwrite existing tags
        assert config.projects[0].tags == ["keep"]

    def test_unregister_project_removes_entry(self) -> None:
        register_project("proj-a", "/tmp/a")
        register_project("proj-b", "/tmp/b")
        config = unregister_project("proj-a")
        assert len(config.projects) == 1
        assert config.projects[0].project_id == "proj-b"

    def test_unregister_removes_subscriptions(self) -> None:
        register_project("proj-a", "/tmp/a")
        register_project("proj-b", "/tmp/b")
        add_subscription("proj-a", sources=["proj-b"])
        config = unregister_project("proj-a")
        assert len(config.subscriptions) == 0

    def test_unregister_nonexistent_is_safe(self) -> None:
        config = unregister_project("nonexistent")
        assert config.projects == []

    def test_add_subscription_valid(self) -> None:
        register_project("proj-a", "/tmp/a")
        register_project("proj-b", "/tmp/b")
        config = add_subscription("proj-a", sources=["proj-b"], min_confidence=0.7)
        assert len(config.subscriptions) == 1
        assert config.subscriptions[0].subscriber == "proj-a"
        assert config.subscriptions[0].sources == ["proj-b"]
        assert config.subscriptions[0].min_confidence == 0.7

    def test_add_subscription_replaces_existing(self) -> None:
        register_project("proj-a", "/tmp/a")
        register_project("proj-b", "/tmp/b")
        add_subscription("proj-a", sources=["proj-b"], min_confidence=0.5)
        config = add_subscription("proj-a", sources=["proj-b"], min_confidence=0.9)
        assert len(config.subscriptions) == 1
        assert config.subscriptions[0].min_confidence == 0.9

    def test_add_subscription_unknown_subscriber_raises(self) -> None:
        with pytest.raises(ValueError, match="not registered"):
            add_subscription("unknown-proj")

    def test_add_subscription_unknown_source_raises(self) -> None:
        register_project("proj-a", "/tmp/a")
        with pytest.raises(ValueError, match="Unknown source"):
            add_subscription("proj-a", sources=["nonexistent"])

    def test_add_subscription_no_sources_means_all(self) -> None:
        register_project("proj-a", "/tmp/a")
        config = add_subscription("proj-a")
        assert config.subscriptions[0].sources == []

    def test_add_subscription_with_tag_filter(self) -> None:
        register_project("proj-a", "/tmp/a")
        config = add_subscription("proj-a", tag_filter=["api", "core"])
        assert config.subscriptions[0].tag_filter == ["api", "core"]


# ===========================================================================
# 3. FederatedStore
# ===========================================================================


class TestFederatedStore:
    """Tests for the SQLite-backed federated hub store."""

    def test_schema_creation(self, hub_store: FederatedStore) -> None:
        stats = hub_store.get_stats()
        assert stats["total_entries"] == 0
        assert stats["projects"] == {}

    def test_publish_entries(self, hub_store: FederatedStore) -> None:
        entries = [
            _make_entry(key="pattern-a", value="Use dependency injection"),
            _make_entry(key="pattern-b", value="Prefer composition over inheritance"),
        ]
        count = hub_store.publish("proj-a", entries, project_root="/tmp/a")
        assert count == 2

        stats = hub_store.get_stats()
        assert stats["total_entries"] == 2
        assert stats["projects"]["proj-a"] == 2

    def test_publish_upserts_on_same_key(self, hub_store: FederatedStore) -> None:
        hub_store.publish("proj-a", [_make_entry(key="pattern-a", value="v1")])
        hub_store.publish("proj-a", [_make_entry(key="pattern-a", value="v2")])

        entries = hub_store.get_project_entries("proj-a")
        assert len(entries) == 1
        assert entries[0]["value"] == "v2"

    def test_unpublish_specific_keys(self, hub_store: FederatedStore) -> None:
        hub_store.publish(
            "proj-a",
            [
                _make_entry(key="keep-me", value="keep"),
                _make_entry(key="remove-me", value="remove"),
            ],
        )
        removed = hub_store.unpublish("proj-a", keys=["remove-me"])
        assert removed == 1

        entries = hub_store.get_project_entries("proj-a")
        assert len(entries) == 1
        assert entries[0]["key"] == "keep-me"

    def test_unpublish_all_for_project(self, hub_store: FederatedStore) -> None:
        hub_store.publish("proj-a", [_make_entry(key="a1", value="val")])
        hub_store.publish("proj-b", [_make_entry(key="b1", value="val")])
        removed = hub_store.unpublish("proj-a")
        assert removed == 1

        assert hub_store.get_stats()["total_entries"] == 1

    def test_search_like_fallback(self, hub_store: FederatedStore) -> None:
        hub_store.publish(
            "proj-a",
            [
                _make_entry(key="api-pattern", value="REST API pattern"),
                _make_entry(key="db-pattern", value="Database pattern"),
            ],
        )
        # Search should find matching entries (FTS5 or LIKE)
        results = hub_store.search("api")
        assert len(results) >= 1
        assert any(r["key"] == "api-pattern" for r in results)

    def test_search_with_min_confidence(self, hub_store: FederatedStore) -> None:
        hub_store.publish(
            "proj-a",
            [
                _make_entry(key="high-conf", value="high confidence", confidence=0.9),
                _make_entry(key="low-conf", value="low confidence", confidence=0.2),
            ],
        )
        results = hub_store.search("confidence", min_confidence=0.5)
        keys = [r["key"] for r in results]
        assert "high-conf" in keys
        assert "low-conf" not in keys

    def test_search_with_project_filter(self, hub_store: FederatedStore) -> None:
        hub_store.publish("proj-a", [_make_entry(key="from-a", value="a value")])
        hub_store.publish("proj-b", [_make_entry(key="from-b", value="b value")])
        results = hub_store.search("value", project_ids=["proj-a"])
        assert all(r["project_id"] == "proj-a" for r in results)

    def test_search_with_tag_filter(self, hub_store: FederatedStore) -> None:
        hub_store.publish(
            "proj-a",
            [
                _make_entry(key="tagged", value="has tags", tags=["api", "core"]),
                _make_entry(key="untagged", value="no relevant tags", tags=["other"]),
            ],
        )
        results = hub_store.search("tags", tags=["api"])
        keys = [r["key"] for r in results]
        assert "tagged" in keys
        assert "untagged" not in keys

    def test_get_project_entries(self, hub_store: FederatedStore) -> None:
        hub_store.publish("proj-a", [_make_entry(key="a1", value="val1")])
        hub_store.publish("proj-a", [_make_entry(key="a2", value="val2")])
        hub_store.publish("proj-b", [_make_entry(key="b1", value="val3")])

        entries = hub_store.get_project_entries("proj-a")
        assert len(entries) == 2
        assert all(e["project_id"] == "proj-a" for e in entries)

    def test_get_project_entries_empty(self, hub_store: FederatedStore) -> None:
        entries = hub_store.get_project_entries("nonexistent")
        assert entries == []

    def test_get_stats(self, hub_store: FederatedStore) -> None:
        hub_store.publish(
            "proj-a",
            [_make_entry(key="a1", value="v1"), _make_entry(key="a2", value="v2")],
        )
        hub_store.publish("proj-b", [_make_entry(key="b1", value="v1")])

        stats = hub_store.get_stats()
        assert stats["total_entries"] == 3
        assert stats["projects"]["proj-a"] == 2
        assert stats["projects"]["proj-b"] == 1
        assert len(stats["meta"]) == 2

    def test_composite_pk_different_projects(self, hub_store: FederatedStore) -> None:
        """Same key from different projects should coexist."""
        hub_store.publish("proj-a", [_make_entry(key="shared-key", value="from A")])
        hub_store.publish("proj-b", [_make_entry(key="shared-key", value="from B")])

        stats = hub_store.get_stats()
        assert stats["total_entries"] == 2

        a_entries = hub_store.get_project_entries("proj-a")
        b_entries = hub_store.get_project_entries("proj-b")
        assert a_entries[0]["value"] == "from A"
        assert b_entries[0]["value"] == "from B"

    def test_search_respects_limit(self, hub_store: FederatedStore) -> None:
        entries = [_make_entry(key=f"entry-{i:03d}", value=f"entry value {i}") for i in range(10)]
        hub_store.publish("proj-a", entries)
        results = hub_store.search("entry", limit=3)
        assert len(results) <= 3


# ===========================================================================
# 4. Sync operations
# ===========================================================================


class TestSyncToHub:
    """Tests for sync_to_hub — publishing shared-scope entries."""

    def test_publishes_shared_scope_entries(self, hub_store: FederatedStore) -> None:
        shared_entry = _make_entry(
            key="shared-pattern",
            value="shared val",
            scope=MemoryScope.shared,
        )
        project_entry = _make_entry(
            key="project-only",
            value="project val",
            scope=MemoryScope.project,
        )
        mock_store = MockMemoryStore([shared_entry, project_entry])

        result = sync_to_hub(mock_store, hub_store, "proj-a", project_root="/tmp/a")
        assert result["published"] == 1

        entries = hub_store.get_project_entries("proj-a")
        assert len(entries) == 1
        assert entries[0]["key"] == "shared-pattern"

    def test_publishes_specific_keys(self, hub_store: FederatedStore) -> None:
        e1 = _make_entry(key="key-a", value="val a", scope=MemoryScope.shared)
        e2 = _make_entry(key="key-b", value="val b", scope=MemoryScope.shared)
        mock_store = MockMemoryStore([e1, e2])

        result = sync_to_hub(mock_store, hub_store, "proj-a", keys=["key-a"])
        assert result["published"] == 1

    def test_empty_store_returns_zero(self, hub_store: FederatedStore) -> None:
        mock_store = MockMemoryStore([])
        result = sync_to_hub(mock_store, hub_store, "proj-a")
        assert result == {"published": 0, "skipped": 0}


class TestSyncFromHub:
    """Tests for sync_from_hub — pulling subscribed entries."""

    def _setup_hub(self, hub_store: FederatedStore) -> None:
        """Populate hub with entries from proj-b."""
        hub_store.publish(
            "proj-b",
            [
                _make_entry(key="pattern-from-b", value="B pattern", confidence=0.8, tags=["api"]),
                _make_entry(key="low-conf-b", value="Low confidence", confidence=0.3, tags=["api"]),
                _make_entry(
                    key="untagged-b",
                    value="No matching tags",
                    confidence=0.8,
                    tags=["other"],
                ),
            ],
        )

    def test_imports_matching_entries(self, hub_store: FederatedStore) -> None:
        self._setup_hub(hub_store)
        register_project("proj-a", "/tmp/a")
        register_project("proj-b", "/tmp/b")
        add_subscription("proj-a", sources=["proj-b"])

        mock_store = MockMemoryStore([])
        config = load_federation_config()
        result = sync_from_hub(mock_store, hub_store, "proj-a", config=config)
        # default min_confidence=0.5 filters out low-conf-b (0.3)
        assert result["imported"] == 2
        assert result["skipped"] == 1
        assert result["conflicts"] == 0

    def test_conflict_resolution_local_wins(self, hub_store: FederatedStore) -> None:
        hub_store.publish(
            "proj-b",
            [_make_entry(key="conflicting-key", value="hub version", confidence=0.9)],
        )
        register_project("proj-a", "/tmp/a")
        register_project("proj-b", "/tmp/b")
        add_subscription("proj-a", sources=["proj-b"])

        local_entry = _make_entry(key="conflicting-key", value="local version")
        mock_store = MockMemoryStore([local_entry])
        config = load_federation_config()
        result = sync_from_hub(mock_store, hub_store, "proj-a", config=config)
        assert result["conflicts"] == 1
        assert result["imported"] == 0

    def test_tag_filtering(self, hub_store: FederatedStore) -> None:
        self._setup_hub(hub_store)
        register_project("proj-a", "/tmp/a")
        register_project("proj-b", "/tmp/b")
        add_subscription("proj-a", sources=["proj-b"], tag_filter=["api"])

        mock_store = MockMemoryStore([])
        config = load_federation_config()
        result = sync_from_hub(mock_store, hub_store, "proj-a", config=config)
        # "pattern-from-b" (api, conf 0.8) -> imported
        # "low-conf-b" (api, conf 0.3) -> imported (min_confidence default 0.5 filters)
        # "untagged-b" (other, conf 0.8) -> skipped (tag mismatch)
        assert result["skipped"] >= 1  # at least untagged-b skipped

    def test_min_confidence_filtering(self, hub_store: FederatedStore) -> None:
        self._setup_hub(hub_store)
        register_project("proj-a", "/tmp/a")
        register_project("proj-b", "/tmp/b")
        add_subscription("proj-a", sources=["proj-b"], min_confidence=0.5)

        mock_store = MockMemoryStore([])
        config = load_federation_config()
        result = sync_from_hub(mock_store, hub_store, "proj-a", config=config)
        # low-conf-b (0.3) should be skipped
        assert result["skipped"] >= 1

    def test_no_subscription_returns_zero(self, hub_store: FederatedStore) -> None:
        mock_store = MockMemoryStore([])
        config = FederationConfig()
        result = sync_from_hub(mock_store, hub_store, "proj-a", config=config)
        assert result == {"imported": 0, "skipped": 0, "conflicts": 0}

    def test_sync_adds_federated_tags(self, hub_store: FederatedStore) -> None:
        hub_store.publish(
            "proj-b",
            [_make_entry(key="tagged-entry", value="some value", confidence=0.8)],
        )
        register_project("proj-a", "/tmp/a")
        register_project("proj-b", "/tmp/b")
        add_subscription("proj-a", sources=["proj-b"])

        mock_store = MockMemoryStore([])
        config = load_federation_config()
        sync_from_hub(mock_store, hub_store, "proj-a", config=config)

        assert len(mock_store._saved) == 1
        saved = mock_store._saved[0]
        assert "federated" in saved["tags"]
        assert "from:proj-b" in saved["tags"]
        assert saved["source_agent"] == "federated:proj-b"


# ===========================================================================
# 5. Federated search
# ===========================================================================


class TestFederatedSearch:
    """Tests for federated_search — cross-store search with dedup and boost."""

    def test_local_results_get_boost(self, hub_store: FederatedStore) -> None:
        local_entry = _make_entry(key="local-pattern", value="local value", confidence=0.8)
        mock_store = MockMemoryStore([local_entry])

        hub_store.publish(
            "proj-b",
            [_make_entry(key="hub-pattern", value="hub value", confidence=0.8)],
        )

        results = federated_search("pattern", mock_store, hub_store, project_id="proj-a")

        local_results = [r for r in results if r.source == "local"]
        hub_results = [r for r in results if r.source == "federated"]

        assert len(local_results) >= 1
        # Local gets 1.2x boost: 0.8 * 1.2 = 0.96 vs hub 0.8
        for lr in local_results:
            assert lr.relevance_score == pytest.approx(0.8 * 1.2)
        for hr in hub_results:
            assert hr.relevance_score == pytest.approx(0.8)

    def test_deduplication_local_wins(self, hub_store: FederatedStore) -> None:
        local_entry = _make_entry(key="same-key", value="local version", confidence=0.7)
        mock_store = MockMemoryStore([local_entry])

        hub_store.publish(
            "proj-b",
            [_make_entry(key="same-key", value="hub version", confidence=0.9)],
        )

        results = federated_search("same", mock_store, hub_store, project_id="proj-a")

        same_key_results = [r for r in results if r.key == "same-key"]
        assert len(same_key_results) == 1
        assert same_key_results[0].source == "local"

    def test_results_sorted_by_relevance(self, hub_store: FederatedStore) -> None:
        entries = [
            _make_entry(key="low-entry", value="low relevance entry", confidence=0.3),
            _make_entry(key="high-entry", value="high relevance entry", confidence=0.9),
        ]
        mock_store = MockMemoryStore(entries)

        results = federated_search("entry", mock_store, hub_store, project_id="proj-a")
        if len(results) >= 2:
            assert results[0].relevance_score >= results[1].relevance_score

    def test_include_local_false(self, hub_store: FederatedStore) -> None:
        local_entry = _make_entry(key="local-only", value="local val", confidence=0.8)
        mock_store = MockMemoryStore([local_entry])

        hub_store.publish(
            "proj-b",
            [_make_entry(key="hub-only", value="hub val", confidence=0.8)],
        )

        results = federated_search(
            "val",
            mock_store,
            hub_store,
            project_id="proj-a",
            include_local=False,
        )
        assert all(r.source == "federated" for r in results)

    def test_include_hub_false(self, hub_store: FederatedStore) -> None:
        local_entry = _make_entry(key="local-only", value="local val", confidence=0.8)
        mock_store = MockMemoryStore([local_entry])

        hub_store.publish(
            "proj-b",
            [_make_entry(key="hub-only", value="hub val", confidence=0.8)],
        )

        results = federated_search(
            "val",
            mock_store,
            hub_store,
            project_id="proj-a",
            include_hub=False,
        )
        assert all(r.source == "local" for r in results)

    def test_max_results_respected(self, hub_store: FederatedStore) -> None:
        entries = [
            _make_entry(key=f"entry-{i:03d}", value=f"value {i}", confidence=0.8) for i in range(10)
        ]
        mock_store = MockMemoryStore(entries)

        results = federated_search(
            "value", mock_store, hub_store, project_id="proj-a", max_results=3
        )
        assert len(results) <= 3

    def test_empty_search_returns_empty(self, hub_store: FederatedStore) -> None:
        mock_store = MockMemoryStore([])
        results = federated_search("nonexistent", mock_store, hub_store, project_id="proj-a")
        assert results == []


# ===========================================================================
# 6. MemoryScope.shared
# ===========================================================================


class TestMemoryScopeShared:
    """Verify MemoryScope.shared is valid and usable in MemoryEntry."""

    def test_shared_scope_exists(self) -> None:
        assert MemoryScope.shared == "shared"

    def test_shared_scope_in_entry(self) -> None:
        entry = _make_entry(scope=MemoryScope.shared)
        assert entry.scope == MemoryScope.shared

    def test_shared_scope_serialization(self) -> None:
        entry = _make_entry(scope=MemoryScope.shared)
        data = entry.model_dump(mode="json")
        assert data["scope"] == "shared"


# ===========================================================================
# 7. FederatedSearchResult dataclass
# ===========================================================================


class TestFederatedSearchResult:
    """Tests for the FederatedSearchResult dataclass."""

    def test_defaults(self) -> None:
        result = FederatedSearchResult(key="k", value="v", source="local", project_id="p")
        assert result.confidence == 0.0
        assert result.tier == "pattern"
        assert result.tags == []
        assert result.relevance_score == 0.0

    def test_full_construction(self) -> None:
        result = FederatedSearchResult(
            key="k",
            value="v",
            source="federated",
            project_id="proj-b",
            confidence=0.9,
            tier="architectural",
            tags=["api"],
            relevance_score=0.95,
        )
        assert result.confidence == 0.9
        assert result.tags == ["api"]
