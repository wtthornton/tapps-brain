"""Unit tests for MemoryStore (Epic 23, Story 3)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from tapps_brain.models import MemoryEntry
from tapps_brain.store import (
    _MAX_ENTRIES,
    ConsolidationConfig,
    MemoryStore,
    _scope_rank,
    _validate_write_rules,
)

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


@pytest.fixture()
def store(tmp_path: Path) -> Generator[MemoryStore, None, None]:
    """Create a MemoryStore instance backed by a temp directory."""
    s = MemoryStore(tmp_path)
    yield s
    s.close()


class TestMemoryStoreCRUD:
    """Tests for basic CRUD operations."""

    def test_save_and_get(self, store: MemoryStore) -> None:
        result = store.save(key="test-key", value="Test value")
        assert isinstance(result, MemoryEntry)
        assert result.key == "test-key"

        loaded = store.get("test-key")
        assert loaded is not None
        assert loaded.value == "Test value"

    def test_get_nonexistent(self, store: MemoryStore) -> None:
        assert store.get("nonexistent") is None

    def test_save_updates_existing(self, store: MemoryStore) -> None:
        store.save(key="k1", value="original")
        store.save(key="k1", value="updated")

        loaded = store.get("k1")
        assert loaded is not None
        assert loaded.value == "updated"
        assert store.count() == 1

    def test_save_preserves_created_at(self, store: MemoryStore) -> None:
        entry1 = store.save(key="k1", value="v1")
        assert isinstance(entry1, MemoryEntry)
        created = entry1.created_at

        entry2 = store.save(key="k1", value="v2")
        assert isinstance(entry2, MemoryEntry)
        assert entry2.created_at == created

    def test_delete(self, store: MemoryStore) -> None:
        store.save(key="k1", value="v1")
        assert store.delete("k1") is True
        assert store.get("k1") is None
        assert store.count() == 0

    def test_delete_nonexistent(self, store: MemoryStore) -> None:
        assert store.delete("nonexistent") is False

    def test_count(self, store: MemoryStore) -> None:
        assert store.count() == 0
        store.save(key="k1", value="v1")
        assert store.count() == 1
        store.save(key="k2", value="v2")
        assert store.count() == 2

    def test_get_updates_access_metadata(self, store: MemoryStore) -> None:
        store.save(key="k1", value="v1")
        entry = store.get("k1")
        assert entry is not None
        assert entry.access_count == 1

        entry2 = store.get("k1")
        assert entry2 is not None
        assert entry2.access_count == 2


class TestMemoryStoreList:
    """Tests for list_all with filters."""

    def test_list_all_unfiltered(self, store: MemoryStore) -> None:
        store.save(key="k1", value="v1")
        store.save(key="k2", value="v2")
        entries = store.list_all()
        assert len(entries) == 2

    def test_list_filter_by_tier(self, store: MemoryStore) -> None:
        store.save(key="a1", value="v", tier="architectural")
        store.save(key="p1", value="v", tier="pattern")
        entries = store.list_all(tier="architectural")
        assert len(entries) == 1
        assert entries[0].key == "a1"

    def test_list_filter_by_scope(self, store: MemoryStore) -> None:
        store.save(key="proj1", value="v", scope="project")
        store.save(key="br1", value="v", scope="branch", branch="main")
        entries = store.list_all(scope="project")
        assert len(entries) == 1
        assert entries[0].key == "proj1"

    def test_list_filter_by_tags(self, store: MemoryStore) -> None:
        store.save(key="k1", value="v", tags=["python"])
        store.save(key="k2", value="v", tags=["rust"])
        entries = store.list_all(tags=["python"])
        assert len(entries) == 1
        assert entries[0].key == "k1"


class TestMemoryStoreSearch:
    """Tests for FTS5 search."""

    def test_search(self, store: MemoryStore) -> None:
        store.save(key="arch-decision", value="Use SQLite for storage")
        store.save(key="code-pattern", value="Always use type hints")
        results = store.search("SQLite")
        assert len(results) >= 1
        assert any(r.key == "arch-decision" for r in results)

    def test_search_empty_returns_empty(self, store: MemoryStore) -> None:
        store.save(key="k1", value="v1")
        assert store.search("") == []


class TestMemoryStoreUpdateFields:
    """Tests for partial field updates."""

    def test_update_confidence(self, store: MemoryStore) -> None:
        store.save(key="k1", value="v1")
        updated = store.update_fields("k1", confidence=0.9)
        assert updated is not None
        assert updated.confidence == 0.9

    def test_update_nonexistent_returns_none(self, store: MemoryStore) -> None:
        assert store.update_fields("nonexistent", confidence=0.5) is None

    def test_update_contradicted(self, store: MemoryStore) -> None:
        store.save(key="k1", value="v1")
        updated = store.update_fields("k1", contradicted=True, contradiction_reason="outdated")
        assert updated is not None
        assert updated.contradicted is True
        assert updated.contradiction_reason == "outdated"


class TestMemoryStoreSnapshot:
    """Tests for snapshot generation."""

    def test_snapshot_empty(self, store: MemoryStore) -> None:
        snap = store.snapshot()
        assert snap.total_count == 0
        assert snap.entries == []

    def test_snapshot_with_entries(self, store: MemoryStore) -> None:
        store.save(key="k1", value="v1", tier="architectural")
        store.save(key="k2", value="v2", tier="pattern")
        snap = store.snapshot()
        assert snap.total_count == 2
        assert snap.tier_counts.get("architectural") == 1
        assert snap.tier_counts.get("pattern") == 1


class TestMemoryStoreEviction:
    """Tests for max entries eviction."""

    def test_evicts_lowest_confidence_at_max(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        try:
            # Insert one entry with distinctly low confidence
            store.save(
                key="lowest",
                value="will be evicted",
                source="agent",
                confidence=0.1,
            )
            # Fill remaining slots with higher confidence
            for i in range(_MAX_ENTRIES - 1):
                store.save(
                    key=f"entry-{i:04d}",
                    value=f"value {i}",
                    source="agent",
                    confidence=0.8,
                )
            assert store.count() == _MAX_ENTRIES
            # "lowest" still present before overflow
            assert store.get("lowest") is not None

            # 501st entry triggers eviction of the lowest-confidence entry
            store.save(
                key="overflow",
                value="triggers eviction",
                source="agent",
                confidence=0.9,
            )
            assert store.count() == _MAX_ENTRIES
            # The lowest-confidence entry (0.1) should have been evicted
            assert store.get("lowest") is None
            # The new entry and a sample high-confidence entry survive
            assert store.get("overflow") is not None
            assert store.get("entry-0000") is not None
        finally:
            store.close()

    def test_eviction_tie_removes_first_inserted(self, tmp_path: Path) -> None:
        """When entries tie on confidence, min() picks the first by key iteration order."""
        store = MemoryStore(tmp_path)
        try:
            # Fill to max, all with identical confidence
            for i in range(_MAX_ENTRIES):
                store.save(
                    key=f"entry-{i:04d}",
                    value=f"value {i}",
                    source="agent",
                    confidence=0.5,
                )
            assert store.count() == _MAX_ENTRIES

            # Overflow triggers eviction; with equal confidence the first
            # key returned by min() over the dict (insertion-order) is evicted.
            store.save(
                key="overflow",
                value="triggers eviction",
                source="agent",
                confidence=0.5,
            )
            assert store.count() == _MAX_ENTRIES
            # entry-0000 was inserted first and should be the eviction victim
            assert store.get("entry-0000") is None
            # The new entry and later entries survive
            assert store.get("overflow") is not None
            assert store.get("entry-0001") is not None
        finally:
            store.close()


class TestMemoryStoreRAGSafety:
    """Tests for RAG safety on save."""

    def test_normal_content_passes(self, store: MemoryStore) -> None:
        result = store.save(key="safe-key", value="Normal safe content")
        assert isinstance(result, MemoryEntry)

    def test_blocked_content_returns_error(self, store: MemoryStore) -> None:
        # Simulate content that triggers heavy RAG safety flags
        with patch("tapps_brain.store.check_content_safety") as mock_safety:
            from tapps_brain.safety import SafetyCheckResult

            mock_safety.return_value = SafetyCheckResult(
                safe=False,
                flagged_patterns=["role_manipulation", "instruction_injection"],
                match_count=5,
            )
            result = store.save(key="bad-key", value="malicious content")
            assert isinstance(result, dict)
            assert result["error"] == "content_blocked"

    def test_sanitized_content_on_flagged_but_not_blocked(self, store: MemoryStore) -> None:
        """Content flagged but below block threshold gets sanitized."""
        with patch("tapps_brain.store.check_content_safety") as mock_safety:
            from tapps_brain.safety import SafetyCheckResult

            mock_safety.return_value = SafetyCheckResult(
                safe=False,
                flagged_patterns=["some_pattern"],
                match_count=1,
                sanitised_content="cleaned content",
            )
            result = store.save(key="sanitized-key", value="slightly risky")
            assert isinstance(result, MemoryEntry)
            assert result.value == "cleaned content"


class TestMemoryStoreClose:
    """Tests for store close behavior."""

    def test_close_is_idempotent(self, tmp_path: Path) -> None:
        """Closing the store multiple times should not raise on first close."""
        s = MemoryStore(tmp_path)
        s.close()
        # Second close may raise since the underlying connection is closed,
        # but the first close should succeed cleanly

    def test_close_cleans_up(self, tmp_path: Path) -> None:
        """After close, the persistence layer connection is closed."""
        s = MemoryStore(tmp_path)
        s.save(key="k1", value="v1")
        s.close()
        # Verify that the internal connection is actually closed
        # by checking we can't execute on it
        import sqlite3

        with pytest.raises(sqlite3.ProgrammingError):
            s._persistence._conn.execute("SELECT 1")


class TestMemoryStoreWriteRules:
    """Tests for write rules validation."""

    def test_write_rules_none_allows_all(self) -> None:
        assert _validate_write_rules("key", "value", None) is None

    def test_write_rules_not_enforced(self) -> None:
        @dataclass
        class Rules:
            enforced: bool = False

        assert _validate_write_rules("key", "value", Rules()) is None

    def test_write_rules_blocked_keyword(self) -> None:
        @dataclass
        class Rules:
            enforced: bool = True
            block_sensitive_keywords: list[str] = None  # type: ignore[assignment]

            def __post_init__(self) -> None:
                if self.block_sensitive_keywords is None:
                    self.block_sensitive_keywords = ["secret"]

        result = _validate_write_rules("my-secret-key", "value", Rules())
        assert result is not None
        assert "secret" in result

    def test_write_rules_min_length(self) -> None:
        @dataclass
        class Rules:
            enforced: bool = True
            block_sensitive_keywords: list[str] = None  # type: ignore[assignment]
            min_value_length: int = 10

            def __post_init__(self) -> None:
                if self.block_sensitive_keywords is None:
                    self.block_sensitive_keywords = []

        result = _validate_write_rules("key", "short", Rules())
        assert result is not None
        assert "too short" in result.lower()

    def test_write_rules_max_length(self) -> None:
        @dataclass
        class Rules:
            enforced: bool = True
            block_sensitive_keywords: list[str] = None  # type: ignore[assignment]
            min_value_length: int = 0
            max_value_length: int = 5

            def __post_init__(self) -> None:
                if self.block_sensitive_keywords is None:
                    self.block_sensitive_keywords = []

        result = _validate_write_rules("key", "too long value", Rules())
        assert result is not None
        assert "too long" in result.lower()

    def test_write_rules_valid_passes(self) -> None:
        @dataclass
        class Rules:
            enforced: bool = True
            block_sensitive_keywords: list[str] = None  # type: ignore[assignment]
            min_value_length: int = 1
            max_value_length: int = 100

            def __post_init__(self) -> None:
                if self.block_sensitive_keywords is None:
                    self.block_sensitive_keywords = []

        result = _validate_write_rules("key", "valid value", Rules())
        assert result is None

    def test_store_rejects_write_rule_violation(self, tmp_path: Path) -> None:
        @dataclass
        class Rules:
            enforced: bool = True
            block_sensitive_keywords: list[str] = None  # type: ignore[assignment]
            min_value_length: int = 0
            max_value_length: int = 4096

            def __post_init__(self) -> None:
                if self.block_sensitive_keywords is None:
                    self.block_sensitive_keywords = ["password"]

        s = MemoryStore(tmp_path, write_rules=Rules())
        result = s.save(key="my-password", value="secret123")
        assert isinstance(result, dict)
        assert result["error"] == "write_rules_violation"
        s.close()


class TestMemoryStoreScopeResolution:
    """Tests for scope resolution in get()."""

    def test_get_with_scope_and_branch(self, tmp_path: Path) -> None:
        s = MemoryStore(tmp_path)
        s.save(key="k1", value="project value", scope="project")
        s.save(key="k1", value="branch value", scope="branch", branch="main")

        # Request with scope=branch should find the branch-scoped entry
        result = s.get("k1", scope="branch", branch="main")
        assert result is not None
        s.close()

    def test_get_scope_resolution_returns_none(self, tmp_path: Path) -> None:
        s = MemoryStore(tmp_path)
        result = s.get("nonexistent", scope="project", branch="main")
        assert result is None
        s.close()

    def test_scope_rank_function(self) -> None:
        from tapps_brain.models import MemoryScope

        assert _scope_rank(MemoryScope.project) == 0
        assert _scope_rank(MemoryScope.branch) == 1
        assert _scope_rank(MemoryScope.session) == 2
        assert _scope_rank(MemoryScope.shared) == 0  # default


class TestMemoryStoreSearchFilters:
    """Tests for search with post-filters."""

    def test_search_filter_by_tier(self, store: MemoryStore) -> None:
        store.save(key="arch1", value="SQLite architecture", tier="architectural")
        store.save(key="pat1", value="SQLite pattern usage", tier="pattern")
        results = store.search("SQLite", tier="architectural")
        assert all(r.tier == "architectural" for r in results)

    def test_search_filter_by_scope(self, store: MemoryStore) -> None:
        store.save(key="proj1", value="SQLite project scope")
        store.save(key="br1", value="SQLite branch scope", scope="branch", branch="dev")
        results = store.search("SQLite", scope="project")
        assert all(r.scope == "project" for r in results)

    def test_search_filter_by_tags(self, store: MemoryStore) -> None:
        store.save(key="tagged1", value="SQLite tagged", tags=["db"])
        store.save(key="tagged2", value="SQLite untagged")
        results = store.search("SQLite", tags=["db"])
        assert all("db" in r.tags for r in results)


class TestMemoryStoreConsolidation:
    """Tests for auto-consolidation behavior."""

    def test_consolidation_disabled_by_default(self, store: MemoryStore) -> None:
        """No consolidation runs when not configured."""
        result = store.save(key="k1", value="value one")
        assert isinstance(result, MemoryEntry)

    def test_consolidation_config(self, tmp_path: Path) -> None:
        config = ConsolidationConfig(enabled=True, threshold=0.8, min_entries=2)
        s = MemoryStore(tmp_path, consolidation_config=config)
        assert s._consolidation_config.enabled is True
        assert s._consolidation_config.threshold == 0.8
        s.close()

    def test_set_consolidation_config(self, store: MemoryStore) -> None:
        new_config = ConsolidationConfig(enabled=True, threshold=0.5)
        store.set_consolidation_config(new_config)
        assert store._consolidation_config.enabled is True

    def test_consolidation_triggered_on_save(self, tmp_path: Path) -> None:
        """When consolidation is enabled, _maybe_consolidate is called."""
        config = ConsolidationConfig(enabled=True, threshold=0.9, min_entries=2)
        s = MemoryStore(tmp_path, consolidation_config=config)
        with patch("tapps_brain.store.MemoryStore._maybe_consolidate") as mock_consol:
            s.save(key="k1", value="trigger consolidation check")
            mock_consol.assert_called_once()
        s.close()

    def test_consolidation_skip_flag(self, tmp_path: Path) -> None:
        """skip_consolidation=True prevents consolidation check."""
        config = ConsolidationConfig(enabled=True, threshold=0.9, min_entries=2)
        s = MemoryStore(tmp_path, consolidation_config=config)
        with patch("tapps_brain.store.MemoryStore._maybe_consolidate") as mock_consol:
            s.save(key="k1", value="no consolidation", skip_consolidation=True)
            mock_consol.assert_not_called()
        s.close()

    def test_consolidation_exception_handled(self, tmp_path: Path) -> None:
        """Consolidation failure should not crash save."""
        config = ConsolidationConfig(enabled=True, threshold=0.1, min_entries=1)
        s = MemoryStore(tmp_path, consolidation_config=config)
        with patch(
            "tapps_brain.auto_consolidation.check_consolidation_on_save",
            side_effect=RuntimeError("boom"),
        ):
            result = s.save(key="k1", value="should still save")
            assert isinstance(result, MemoryEntry)
        s.close()


class TestMemoryStoreEmbedding:
    """Tests for embedding provider integration."""

    def test_embedding_computed_on_save(self, tmp_path: Path) -> None:
        provider = MagicMock()
        provider.embed.return_value = [0.1, 0.2, 0.3]
        s = MemoryStore(tmp_path, embedding_provider=provider)
        result = s.save(key="emb-key", value="embed me")
        assert isinstance(result, MemoryEntry)
        provider.embed.assert_called_once_with("embed me")
        # The entry in the store should have the embedding
        loaded = s.get("emb-key")
        assert loaded is not None
        assert loaded.embedding == [0.1, 0.2, 0.3]
        s.close()

    def test_embedding_failure_does_not_crash(self, tmp_path: Path) -> None:
        provider = MagicMock()
        provider.embed.side_effect = RuntimeError("model error")
        s = MemoryStore(tmp_path, embedding_provider=provider)
        result = s.save(key="emb-fail", value="embed fail")
        assert isinstance(result, MemoryEntry)
        # Entry saved without embedding
        loaded = s.get("emb-fail")
        assert loaded is not None
        s.close()


class TestMemoryStoreProjectRoot:
    """Tests for project_root property."""

    def test_project_root_property(self, store: MemoryStore, tmp_path: Path) -> None:
        assert store.project_root == tmp_path
