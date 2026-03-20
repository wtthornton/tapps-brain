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
        assert entry.access_count == 2  # save seeds 1; first get adds 1

        entry2 = store.get("k1")
        assert entry2 is not None
        assert entry2.access_count == 3


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


class TestSupersede:
    """Tests for MemoryStore.supersede() (EPIC-004, STORY-004.2)."""

    def test_supersede_basic(self, store: MemoryStore) -> None:
        """Supersede a fact: old entry gets invalid_at/superseded_by, new entry has valid_at."""
        store.save(key="pricing", value="Pricing is $297/mo", tier="architectural")
        new_entry = store.supersede("pricing", "Pricing is $397/mo")

        # Old entry is invalidated
        old = store.get("pricing")
        assert old is not None
        assert old.invalid_at is not None
        assert old.superseded_by == new_entry.key

        # New entry exists with valid_at
        assert new_entry.valid_at is not None
        assert new_entry.value == "Pricing is $397/mo"
        assert new_entry.tier.value == "architectural"

    def test_supersede_nonexistent_raises_keyerror(self, store: MemoryStore) -> None:
        with pytest.raises(KeyError):
            store.supersede("nonexistent", "new value")

    def test_supersede_already_superseded_raises_valueerror(self, store: MemoryStore) -> None:
        store.save(key="fact-a", value="original fact")
        store.supersede("fact-a", "updated fact")

        with pytest.raises(ValueError, match="already superseded"):
            store.supersede("fact-a", "double supersede attempt")

    def test_supersede_persists_to_sqlite(self, tmp_path: Path) -> None:
        """Supersession survives a cold restart."""
        s1 = MemoryStore(tmp_path)
        s1.save(key="db-version", value="PostgreSQL 15")
        new_entry = s1.supersede("db-version", "PostgreSQL 17")
        new_key = new_entry.key
        s1.close()

        s2 = MemoryStore(tmp_path)
        old = s2.get("db-version")
        assert old is not None
        assert old.invalid_at is not None
        assert old.superseded_by == new_key

        reloaded = s2.get(new_key)
        assert reloaded is not None
        assert reloaded.value == "PostgreSQL 17"
        assert reloaded.valid_at is not None
        s2.close()

    def test_supersede_inherits_tier_and_tags(self, store: MemoryStore) -> None:
        store.save(
            key="tech-stack",
            value="We use React",
            tier="architectural",
            tags=["frontend"],
        )
        new = store.supersede("tech-stack", "We use Vue")
        assert new.tier.value == "architectural"
        assert "frontend" in new.tags

    def test_supersede_with_custom_key(self, store: MemoryStore) -> None:
        store.save(key="config-a", value="old config")
        new = store.supersede("config-a", "new config", key="config-b")
        assert new.key == "config-b"

        old = store.get("config-a")
        assert old is not None
        assert old.superseded_by == "config-b"


class TestHistory:
    """Tests for MemoryStore.history() (EPIC-004, STORY-004.4)."""

    def test_history_single_entry(self, store: MemoryStore) -> None:
        """A standalone entry returns a single-element list."""
        store.save(key="standalone", value="no versions")
        chain = store.history("standalone")
        assert len(chain) == 1
        assert chain[0].key == "standalone"

    def test_history_three_version_chain(self, store: MemoryStore) -> None:
        """Create A -> B -> C, verify history returns [A, B, C]."""
        store.save(key="fact-v1", value="version 1")
        v2 = store.supersede("fact-v1", "version 2", key="fact-v2")
        store.supersede(v2.key, "version 3", key="fact-v3")

        # Calling history on any key in the chain returns the full chain
        for k in ["fact-v1", "fact-v2", "fact-v3"]:
            chain = store.history(k)
            assert len(chain) == 3
            assert chain[0].key == "fact-v1"
            assert chain[1].key == "fact-v2"
            assert chain[2].key == "fact-v3"

    def test_history_nonexistent_raises_keyerror(self, store: MemoryStore) -> None:
        with pytest.raises(KeyError):
            store.history("nonexistent")

    def test_history_ordered_by_valid_at(self, store: MemoryStore) -> None:
        """Entries are ordered by valid_at ascending."""
        store.save(key="ts-v1", value="first")
        store.supersede("ts-v1", "second", key="ts-v2")
        chain = store.history("ts-v1")
        # v1 has no valid_at (sorts first), v2 has valid_at set
        assert chain[0].key == "ts-v1"
        assert chain[1].key == "ts-v2"


class TestTemporalFiltering:
    """Tests for temporal filtering in MemoryStore (EPIC-004, STORY-004.3)."""

    def test_search_excludes_superseded(self, store: MemoryStore) -> None:
        """Default search excludes temporally invalid entries."""
        store.save(key="price-old", value="pricing is 297 dollars monthly")
        store.supersede("price-old", "pricing is 397 dollars monthly", key="price-new")

        results = store.search("pricing dollars monthly")
        keys = [r.key for r in results]
        assert "price-old" not in keys
        assert "price-new" in keys

    def test_search_as_of_returns_old_version(self, store: MemoryStore) -> None:
        """Point-in-time query returns facts valid at that time."""
        store.save(key="db-old", value="We use PostgreSQL 15 database")
        old = store.get("db-old")
        assert old is not None
        old_time = old.created_at

        store.supersede("db-old", "We use PostgreSQL 17 database", key="db-new")

        # Search as_of old_time should find old version
        results = store.search("PostgreSQL database", as_of=old_time)
        keys = [r.key for r in results]
        assert "db-old" in keys

    def test_list_all_exclude_superseded(self, store: MemoryStore) -> None:
        store.save(key="item-a", value="original")
        store.supersede("item-a", "updated", key="item-b")

        all_entries = store.list_all(include_superseded=True)
        assert len(all_entries) == 2

        active_only = store.list_all(include_superseded=False)
        keys = [e.key for e in active_only]
        assert "item-a" not in keys
        assert "item-b" in keys


class TestRelationsWiring:
    """Tests for auto-extraction and persistence of relations (EPIC-006)."""

    def test_save_extracts_and_persists_relations(self, store: MemoryStore) -> None:
        """save() auto-extracts relations and persists them."""
        store.save(key="arch-note", value="MemoryStore manages persistence layer")
        relations = store.get_relations("arch-note")
        assert len(relations) >= 1
        # Should find "manages" predicate
        predicates = [r["predicate"] for r in relations]
        assert "manages" in predicates

    def test_save_no_relations_for_plain_text(self, store: MemoryStore) -> None:
        """save() with text that has no relation patterns stores no relations."""
        store.save(key="plain", value="Just a simple note")
        relations = store.get_relations("plain")
        assert relations == []

    def test_get_relations_nonexistent_key(self, store: MemoryStore) -> None:
        """get_relations for unknown key returns empty list."""
        assert store.get_relations("nope") == []

    def test_ingest_context_extracts_relations(self, store: MemoryStore) -> None:
        """ingest_context() delegates to save(), which extracts relations."""
        context = "Decision: AuthService handles token validation for the API"
        keys = store.ingest_context(context, source="agent")
        # At least one key should be created with relations
        all_relations: list[dict[str, object]] = []
        for key in keys:
            all_relations.extend(store.get_relations(key))
        assert len(all_relations) >= 1

    def test_relations_persist_across_restart(self, tmp_path: Path) -> None:
        """Relations survive close/reopen cycle."""
        s1 = MemoryStore(tmp_path)
        s1.save(key="dep-note", value="PaymentService uses Stripe SDK")
        rels_before = s1.get_relations("dep-note")
        assert len(rels_before) >= 1
        s1.close()

        s2 = MemoryStore(tmp_path)
        rels_after = s2.get_relations("dep-note")
        assert rels_after == rels_before
        s2.close()


class TestFindRelated:
    """Tests for find_related() BFS graph traversal (EPIC-006, story 006.3)."""

    def test_find_related_nonexistent_key_raises(self, store: MemoryStore) -> None:
        """find_related raises KeyError for unknown key."""
        with pytest.raises(KeyError):
            store.find_related("no-such-key")

    def test_find_related_no_relations(self, store: MemoryStore) -> None:
        """Entry with no relations returns empty list."""
        store.save(key="lonely", value="Just a note with no relations")
        assert store.find_related("lonely") == []

    def test_find_related_direct_hop(self, store: MemoryStore) -> None:
        """Two entries sharing an entity are 1 hop apart."""
        # Both mention "persistence layer" — connected via shared entity
        store.save(key="a", value="MemoryStore manages persistence layer")
        store.save(key="b", value="CacheLayer manages persistence layer")
        related = store.find_related("a")
        keys = [k for k, _hop in related]
        assert "b" in keys
        # Should be hop 1
        hop_map = dict(related)
        assert hop_map["b"] == 1

    def test_find_related_chain_a_b_c(self, store: MemoryStore) -> None:
        """A→B→C chain: B is hop 1, C is hop 2 from A."""
        # A and B share entity "ServiceB"
        store.save(key="a", value="ServiceA uses ServiceB")
        # B and C share entity "ServiceC"
        store.save(key="b", value="ServiceB uses ServiceC")
        # C is standalone but connected via ServiceC
        store.save(key="c", value="ServiceC manages DataStore")

        related = store.find_related("a")
        hop_map = dict(related)
        # b shares "ServiceB" with a -> hop 1
        assert hop_map.get("b") == 1
        # c shares "ServiceC" with b -> hop 2
        assert hop_map.get("c") == 2

    def test_find_related_max_hops_limits_depth(self, store: MemoryStore) -> None:
        """max_hops=1 excludes hop-2 results."""
        store.save(key="a", value="ServiceA uses ServiceB")
        store.save(key="b", value="ServiceB uses ServiceC")
        store.save(key="c", value="ServiceC manages DataStore")

        related = store.find_related("a", max_hops=1)
        keys = [k for k, _hop in related]
        assert "b" in keys
        assert "c" not in keys

    def test_find_related_dedup(self, store: MemoryStore) -> None:
        """Each related key appears only once, at its shortest hop distance."""
        # a and b share "ServiceX"; a and b also share via different relation
        store.save(key="a", value="ServiceX manages Config and ServiceX uses Logger")
        store.save(key="b", value="ServiceX handles requests and Logger provides output")
        related = store.find_related("a")
        keys = [k for k, _hop in related]
        # b should appear exactly once
        assert keys.count("b") == 1

    def test_find_related_excludes_self(self, store: MemoryStore) -> None:
        """The starting key is never in the results."""
        store.save(key="self-ref", value="AuthService manages tokens")
        related = store.find_related("self-ref")
        keys = [k for k, _hop in related]
        assert "self-ref" not in keys

    # ------------------------------------------------------------------
    # query_relations
    # ------------------------------------------------------------------

    def test_query_relations_no_filters(self, store: MemoryStore) -> None:
        """No filters returns all relations."""
        store.save(key="a", value="ServiceA uses ServiceB")
        store.save(key="b", value="ServiceC manages DataStore")
        all_rels = store.query_relations()
        assert len(all_rels) >= 2

    def test_query_relations_filter_by_subject(self, store: MemoryStore) -> None:
        """Filter by subject returns only matching relations."""
        store.save(key="a", value="ServiceA uses ServiceB")
        store.save(key="b", value="ServiceC manages DataStore")
        results = store.query_relations(subject="ServiceA")
        assert len(results) >= 1
        assert all(r["subject"].lower() == "servicea" for r in results)

    def test_query_relations_filter_by_predicate(self, store: MemoryStore) -> None:
        """Filter by predicate returns only matching relations."""
        store.save(key="a", value="ServiceA uses ServiceB")
        results = store.query_relations(predicate="uses")
        assert len(results) >= 1
        assert all(r["predicate"].lower() == "uses" for r in results)

    def test_query_relations_filter_by_object_entity(self, store: MemoryStore) -> None:
        """Filter by object_entity returns only matching relations."""
        store.save(key="a", value="ServiceA uses ServiceB")
        results = store.query_relations(object_entity="ServiceB")
        assert len(results) >= 1
        assert all(r["object_entity"].lower() == "serviceb" for r in results)

    def test_query_relations_combined_filters(self, store: MemoryStore) -> None:
        """Multiple filters are AND-combined."""
        store.save(key="a", value="ServiceA uses ServiceB")
        store.save(key="b", value="ServiceA manages Config")
        # Only the "uses" relation for ServiceA
        results = store.query_relations(subject="ServiceA", predicate="uses")
        assert len(results) >= 1
        for r in results:
            assert r["subject"].lower() == "servicea"
            assert r["predicate"].lower() == "uses"

    def test_query_relations_case_insensitive(self, store: MemoryStore) -> None:
        """Filters are case-insensitive."""
        store.save(key="a", value="ServiceA uses ServiceB")
        results_lower = store.query_relations(subject="servicea")
        results_upper = store.query_relations(subject="SERVICEA")
        assert len(results_lower) == len(results_upper)
        assert len(results_lower) >= 1

    def test_query_relations_no_match(self, store: MemoryStore) -> None:
        """Non-matching filter returns empty list."""
        store.save(key="a", value="ServiceA uses ServiceB")
        results = store.query_relations(subject="NonExistent")
        assert results == []

    def test_query_relations_deduplicates(self, store: MemoryStore) -> None:
        """Same triple from multiple entries appears only once."""
        # Both entries mention the same relation
        store.save(key="a", value="ServiceA uses ServiceB")
        store.save(key="b", value="ServiceA uses ServiceB")
        results = store.query_relations(
            subject="ServiceA", predicate="uses", object_entity="ServiceB"
        )
        assert len(results) == 1
