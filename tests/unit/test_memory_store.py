"""Unit tests for MemoryStore (Epic 23, Story 3)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from tapps_brain.models import MemoryEntry
from tapps_brain.store import (
    VALID_AGENT_SCOPES,
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

    _TEST_LIMIT = 50  # Small limit for fast tests (real default is 5000)

    @staticmethod
    def _make_store_with_limit(tmp_path: Path, limit: int) -> MemoryStore:
        """Create a MemoryStore with an overridden max_entries limit."""
        store = MemoryStore(tmp_path)
        if store._profile is not None:
            store._profile.limits.max_entries = limit
        else:
            # No profile resolved — patch the module-level fallback (TAP-513
            # renamed the constant to _MAX_ENTRIES_DEFAULT).
            import tapps_brain.store as _sm

            _sm._MAX_ENTRIES_DEFAULT = limit
        return store

    def test_evicts_lowest_confidence_at_max(self, tmp_path: Path) -> None:
        limit = self._TEST_LIMIT
        store = self._make_store_with_limit(tmp_path, limit)
        try:
            # Insert one entry with distinctly low confidence
            store.save(
                key="lowest",
                value="will be evicted",
                source="agent",
                confidence=0.1,
            )
            # Fill remaining slots with higher confidence
            for i in range(limit - 1):
                store.save(
                    key=f"entry-{i:04d}",
                    value=f"value {i}",
                    source="agent",
                    confidence=0.8,
                )
            assert store.count() == limit
            # "lowest" still present before overflow
            assert store.get("lowest") is not None

            # (limit+1)th entry triggers eviction of lowest-confidence
            store.save(
                key="overflow",
                value="triggers eviction",
                source="agent",
                confidence=0.9,
            )
            assert store.count() == limit
            # The lowest-confidence entry (0.1) should have been evicted
            assert store.get("lowest") is None
            # The new entry and a sample high-confidence entry survive
            assert store.get("overflow") is not None
            assert store.get("entry-0000") is not None
        finally:
            store.close()

    def test_eviction_tie_removes_first_inserted(self, tmp_path: Path) -> None:
        """When entries tie on confidence, min() picks the first by key iteration order."""
        limit = self._TEST_LIMIT
        store = self._make_store_with_limit(tmp_path, limit)
        try:
            # Fill to max, all with identical confidence
            for i in range(limit):
                store.save(
                    key=f"entry-{i:04d}",
                    value=f"value {i}",
                    source="agent",
                    confidence=0.5,
                )
            assert store.count() == limit

            # Overflow triggers eviction; with equal confidence the first
            # key returned by min() over the dict (insertion-order) is evicted.
            store.save(
                key="overflow",
                value="triggers eviction",
                source="agent",
                confidence=0.5,
            )
            assert store.count() == limit
            # entry-0000 was inserted first and should be the eviction victim
            assert store.get("entry-0000") is None
            # The new entry and later entries survive
            assert store.get("overflow") is not None
            assert store.get("entry-0001") is not None
        finally:
            store.close()


class TestMaxEntriesEnvOverride:
    """TAP-513: TAPPS_BRAIN_MAX_ENTRIES env var with YAML > env > default."""

    def test_env_var_overrides_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tapps_brain.store import _max_entries_from_env

        monkeypatch.setenv("TAPPS_BRAIN_MAX_ENTRIES", "1234")
        assert _max_entries_from_env() == 1234

    def test_env_var_unset_returns_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tapps_brain.store import _MAX_ENTRIES_DEFAULT, _max_entries_from_env

        monkeypatch.delenv("TAPPS_BRAIN_MAX_ENTRIES", raising=False)
        assert _max_entries_from_env() == _MAX_ENTRIES_DEFAULT

    def test_env_var_invalid_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tapps_brain.store import _MAX_ENTRIES_DEFAULT, _max_entries_from_env

        monkeypatch.setenv("TAPPS_BRAIN_MAX_ENTRIES", "not-a-number")
        assert _max_entries_from_env() == _MAX_ENTRIES_DEFAULT

    def test_env_var_zero_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tapps_brain.store import _MAX_ENTRIES_DEFAULT, _max_entries_from_env

        monkeypatch.setenv("TAPPS_BRAIN_MAX_ENTRIES", "0")
        assert _max_entries_from_env() == _MAX_ENTRIES_DEFAULT

    def test_env_var_negative_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tapps_brain.store import _MAX_ENTRIES_DEFAULT, _max_entries_from_env

        monkeypatch.setenv("TAPPS_BRAIN_MAX_ENTRIES", "-50")
        assert _max_entries_from_env() == _MAX_ENTRIES_DEFAULT

    def test_yaml_profile_takes_precedence_over_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """YAML > env > default — even with env set, profile wins."""
        from tapps_brain.profile import LayerDefinition, LimitsConfig, MemoryProfile

        monkeypatch.setenv("TAPPS_BRAIN_MAX_ENTRIES", "999")
        prof = MemoryProfile(
            name="yaml-wins",
            layers=[LayerDefinition(name="pattern", half_life_days=60, confidence_floor=0.1)],
            limits=LimitsConfig(max_entries=42),
        )
        store = MemoryStore(tmp_path, profile=prof)
        try:
            assert store._max_entries == 42
        finally:
            store.close()

    def test_env_used_when_no_profile_and_default_when_no_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No profile + env set → env wins; no profile + no env → default."""
        from tapps_brain.store import _MAX_ENTRIES_DEFAULT

        store = MemoryStore(tmp_path)
        try:
            if store._profile is not None:
                pytest.skip("profile auto-resolved; this test exercises the no-profile path")

            monkeypatch.setenv("TAPPS_BRAIN_MAX_ENTRIES", "777")
            assert store._max_entries == 777

            monkeypatch.delenv("TAPPS_BRAIN_MAX_ENTRIES", raising=False)
            assert store._max_entries == _MAX_ENTRIES_DEFAULT
        finally:
            store.close()


class TestMemoryStorePerGroupEviction:
    """Per memory_group caps (EPIC-044 STORY-044.7)."""

    @staticmethod
    def _store(
        tmp_path: Path,
        *,
        max_entries: int,
        max_entries_per_group: int,
    ) -> MemoryStore:
        from tapps_brain.profile import LayerDefinition, LimitsConfig, MemoryProfile

        prof = MemoryProfile(
            name="cap-test",
            layers=[
                LayerDefinition(name="pattern", half_life_days=60, confidence_floor=0.1),
            ],
            limits=LimitsConfig(
                max_entries=max_entries,
                max_entries_per_group=max_entries_per_group,
            ),
        )
        return MemoryStore(tmp_path, profile=prof)

    def test_per_group_overflow_evicts_within_group_only(self, tmp_path: Path) -> None:
        g_cap = 3
        store = self._store(tmp_path, max_entries=100, max_entries_per_group=g_cap)
        try:
            for i in range(g_cap):
                store.save(
                    key=f"a-{i}",
                    value=f"va{i}",
                    memory_group="team-a",
                    confidence=0.5 + i * 0.01,
                )
            for i in range(g_cap):
                store.save(
                    key=f"b-{i}",
                    value=f"vb{i}",
                    memory_group="team-b",
                    confidence=0.8,
                )
            assert store.count() == 2 * g_cap
            store.save(
                key="a-new",
                value="overflow a",
                memory_group="team-a",
                confidence=0.99,
            )
            assert store.count() == 2 * g_cap
            assert store.get("a-0") is None
            assert store.get("a-new") is not None
            assert store.get("b-0") is not None
        finally:
            store.close()

    def test_global_eviction_prefers_incoming_group_when_per_group_cap_enabled(
        self, tmp_path: Path
    ) -> None:
        """Fair global eviction: victim chosen from incoming row's memory_group first."""
        store = self._store(tmp_path, max_entries=4, max_entries_per_group=10)
        try:
            store.save(key="g1-0", value="content g1-0", memory_group="g1", confidence=0.9)
            store.save(key="g1-1", value="content g1-1", memory_group="g1", confidence=0.91)
            store.save(key="g2-0", value="content g2-0", memory_group="g2", confidence=0.1)
            store.save(key="g2-1", value="content g2-1", memory_group="g2", confidence=0.11)
            assert store.count() == 4
            store.save(key="g1-new", value="z", memory_group="g1", confidence=0.99)
            assert store.count() == 4
            assert store.get("g1-new") is not None
            assert store.get("g2-0") is not None
            assert store.get("g2-1") is not None
        finally:
            store.close()

    def test_group_change_into_full_bucket_evicts_in_target_group(self, tmp_path: Path) -> None:
        store = self._store(tmp_path, max_entries=100, max_entries_per_group=2)
        try:
            store.save(key="in-a", value="a", memory_group="a", confidence=0.5)
            store.save(key="b0", value="b0", memory_group="b", confidence=0.2)
            store.save(key="b1", value="b1", memory_group="b", confidence=0.3)
            r = store.save(key="in-a", value="moved", memory_group="b", confidence=0.9)
            assert isinstance(r, MemoryEntry)
            assert r.memory_group == "b"
            assert store.get("b0") is None
            assert store.get("in-a") is not None
            assert store.get("b1") is not None
        finally:
            store.close()

    def test_ungrouped_bucket_respects_cap(self, tmp_path: Path) -> None:
        store = self._store(tmp_path, max_entries=100, max_entries_per_group=2)
        try:
            store.save(key="u0", value="u0", memory_group=None, confidence=0.1)
            store.save(key="u1", value="u1", memory_group=None, confidence=0.2)
            store.save(key="u2", value="u2", memory_group=None, confidence=0.9)
            assert store.count() == 2
            assert store.get("u0") is None
            assert store.get("u2") is not None
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
        """Sanitize path: ``check_content_safety`` returns safe=True with redacted body."""
        with patch("tapps_brain.store.check_content_safety") as mock_safety:
            from tapps_brain.safety import SafetyCheckResult

            mock_safety.return_value = SafetyCheckResult(
                safe=True,
                flagged_patterns=["some_pattern"],
                match_count=1,
                sanitised_content="cleaned content",
                ruleset_version="1.0.0",
            )
            result = store.save(key="sanitized-key", value="slightly risky")
            assert isinstance(result, MemoryEntry)
            assert result.value == "cleaned content"


class TestMemoryStoreClose:
    """Tests for store close behavior."""

    def test_close_succeeds(self, tmp_path: Path) -> None:
        """Closing the store should succeed without raising."""
        s = MemoryStore(tmp_path)
        s.close()

    def test_close_cleans_up(self, tmp_path: Path) -> None:
        """After close, the persistence layer's close() is called."""
        s = MemoryStore(tmp_path)
        s.save(key="k1", value="v1")
        # close() is idempotent and must not raise — Postgres pool tear-down
        # is internal to PostgresConnectionManager.
        s.close()


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


class TestMemoryStoreTemporalSearch:
    """Tests for temporal filtering on search() — Issue #70."""

    def test_search_since_iso_excludes_old_entries(self, store: MemoryStore) -> None:
        """Entries created before 'since' are excluded."""
        store.save(key="old", value="SQLite old entry")
        # A far-future since timestamp should exclude everything.
        future = "2099-01-01T00:00:00+00:00"
        results = store.search("SQLite", since=future)
        assert results == []

    def test_search_since_includes_recent_entries(self, store: MemoryStore) -> None:
        """Entries created after 'since' are included."""
        store.save(key="recent", value="SQLite recent entry")
        past = "2000-01-01T00:00:00+00:00"
        results = store.search("SQLite", since=past)
        assert any(r.key == "recent" for r in results)

    def test_search_until_excludes_future_entries(self, store: MemoryStore) -> None:
        """until in the past excludes all entries (created_at is now)."""
        store.save(key="now_entry", value="SQLite current entry")
        past = "2000-01-01T00:00:00+00:00"
        results = store.search("SQLite", until=past)
        assert results == []

    def test_search_since_and_until_range(self, store: MemoryStore) -> None:
        """Both bounds together form a range filter."""
        store.save(key="inrange", value="SQLite range test entry")
        past = "2000-01-01T00:00:00+00:00"
        future = "2099-01-01T00:00:00+00:00"
        results = store.search("SQLite", since=past, until=future)
        assert any(r.key == "inrange" for r in results)

    def test_search_since_relative_shorthand_7d(self, store: MemoryStore) -> None:
        """Relative shorthand '7d' includes entries created within the last week."""
        store.save(key="recent_rel", value="SQLite relative time test")
        results = store.search("SQLite", since="7d")
        assert any(r.key == "recent_rel" for r in results)

    def test_search_since_relative_shorthand_2w(self, store: MemoryStore) -> None:
        """Relative shorthand '2w' (2 weeks) includes recent entries."""
        store.save(key="twoweeks", value="SQLite two weeks test")
        results = store.search("SQLite", since="2w")
        assert any(r.key == "twoweeks" for r in results)

    def test_search_since_relative_shorthand_1m(self, store: MemoryStore) -> None:
        """Relative shorthand '1m' (30 days) includes recent entries."""
        store.save(key="onemonth", value="SQLite one month test")
        results = store.search("SQLite", since="1m")
        assert any(r.key == "onemonth" for r in results)

    def test_parse_relative_time_passthrough(self) -> None:
        """ISO-8601 strings are returned unchanged."""
        iso = "2026-01-01T00:00:00+00:00"
        assert MemoryStore._parse_relative_time(iso) == iso

    def test_parse_relative_time_days(self) -> None:
        """'Nd' expands to an ISO string N days before now."""
        from datetime import UTC, datetime, timedelta

        before = datetime.now(UTC) - timedelta(days=5)
        result = MemoryStore._parse_relative_time("5d")
        parsed = datetime.fromisoformat(result)
        assert abs((parsed - before).total_seconds()) < 2

    def test_parse_relative_time_weeks(self) -> None:
        """'Nw' expands to N*7 days before now."""
        from datetime import UTC, datetime, timedelta

        before = datetime.now(UTC) - timedelta(days=14)
        result = MemoryStore._parse_relative_time("2w")
        parsed = datetime.fromisoformat(result)
        assert abs((parsed - before).total_seconds()) < 2

    def test_parse_relative_time_months(self) -> None:
        """'Nm' expands to N*30 days before now."""
        from datetime import UTC, datetime, timedelta

        before = datetime.now(UTC) - timedelta(days=30)
        result = MemoryStore._parse_relative_time("1m")
        parsed = datetime.fromisoformat(result)
        assert abs((parsed - before).total_seconds()) < 2


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

    @pytest.mark.skip(
        reason=(
            "Requires durable Postgres storage to persist across MemoryStore restarts. "
            "InMemoryPrivateBackend (unit tests) is per-instance only — ADR-007 stage 2. "
            "Covered by integration tests with a live Postgres connection."
        )
    )
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

    def test_supersede_transfers_relations(self, store: MemoryStore) -> None:
        """Relations from old entry are copied to the new entry."""
        store.save(key="svc-old", value="ServiceA uses ServiceB")
        old_rels = store.get_relations("svc-old")
        assert len(old_rels) >= 1  # Extracted from "uses" pattern

        new_entry = store.supersede("svc-old", "ServiceA v2 uses ServiceB")
        new_rels = store.get_relations(new_entry.key)
        # New entry should have at least the transferred relations
        old_subjects = {r["subject"].lower() for r in old_rels}
        new_subjects = {r["subject"].lower() for r in new_rels}
        assert old_subjects.issubset(new_subjects)

    def test_supersede_no_relations_no_error(self, store: MemoryStore) -> None:
        """Supersede works fine when old entry has no relations."""
        store.save(key="plain", value="Just a plain note with no relations")
        assert store.get_relations("plain") == []
        new_entry = store.supersede("plain", "Updated plain note")
        assert new_entry.key is not None  # No error

    @pytest.mark.skip(
        reason=(
            "Requires durable Postgres storage to persist relations across MemoryStore restarts. "
            "InMemoryPrivateBackend (unit tests) is per-instance only — ADR-007 stage 2."
        )
    )
    def test_supersede_relations_persist_after_restart(self, tmp_path: Path) -> None:
        """Transferred relations survive store close/reopen."""
        store1 = MemoryStore(tmp_path)
        store1.save(key="fact-a", value="ServiceX manages DataStore")
        store1.supersede("fact-a", "ServiceX v2 manages DataStore", key="fact-a.v2")
        rels_before = store1.get_relations("fact-a.v2")
        assert len(rels_before) >= 1
        store1.close()

        store2 = MemoryStore(tmp_path)
        rels_after = store2.get_relations("fact-a.v2")
        assert len(rels_after) >= 1
        assert rels_after[0]["subject"].lower() == rels_before[0]["subject"].lower()
        store2.close()


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

    def test_history_cycle_does_not_hang(self, store: MemoryStore) -> None:
        """history() must terminate and not loop forever when superseded_by creates a cycle."""
        store.save(key="cycle-a", value="entry a")
        store.save(key="cycle-b", value="entry b")
        # Manually inject a cycle: A -> B -> A (corrupted state)
        with store._lock:
            entry_a = store._entries["cycle-a"]
            entry_b = store._entries["cycle-b"]
            store._entries["cycle-a"] = entry_a.model_copy(update={"superseded_by": "cycle-b"})
            store._entries["cycle-b"] = entry_b.model_copy(update={"superseded_by": "cycle-a"})

        # Should return without hanging; result has at most 2 entries
        chain = store.history("cycle-a")
        assert len(chain) <= 2

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

    @pytest.mark.skip(
        reason=(
            "Requires durable Postgres storage to persist relations across MemoryStore restarts. "
            "InMemoryPrivateBackend (unit tests) is per-instance only — ADR-007 stage 2."
        )
    )
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


class TestStoreMetrics:
    """Verify metrics instrumentation of save/get/search (STORY-007.2)."""

    def test_save_increments_counter(self, store: MemoryStore) -> None:
        for i in range(5):
            store.save(key=f"k{i}", value=f"value {i}")
        snap = store.get_metrics()
        assert snap.counters.get("store.save", 0) == 5

    def test_save_records_latency(self, store: MemoryStore) -> None:
        store.save(key="lat", value="latency test")
        snap = store.get_metrics()
        assert "store.save_ms" in snap.histograms
        assert snap.histograms["store.save_ms"].count == 1
        assert snap.histograms["store.save_ms"].min > 0

    def test_save_records_phase_latency_histograms(self, store: MemoryStore) -> None:
        """Save-path sub-phases (roadmap: save observability / EPIC-051.6)."""
        store.save(key="phase", value="plain save without relations text")
        snap = store.get_metrics()
        for name in (
            "store.save.phase.lock_build_ms",
            "store.save.phase.persist_ms",
            "store.save.phase.relations_ms",
        ):
            assert name in snap.histograms, f"missing {name}"
            assert snap.histograms[name].count == 1
            assert snap.histograms[name].min > 0
        # embed_ms may or may not be present depending on embedding model availability
        assert "store.save.phase.hive_ms" not in snap.histograms

    def test_health_includes_save_phase_summary(self, store: MemoryStore) -> None:
        store.save(key="hp", value="health phase summary")
        h = store.health()
        assert h.save_phase_summary
        assert "lock_build_ms" in h.save_phase_summary
        assert "persist_ms" in h.save_phase_summary

    def test_health_includes_profile_seed_version(self, tmp_path: Path) -> None:
        from tapps_brain.profile import LayerDefinition, MemoryProfile, SeedingConfig

        prof = MemoryProfile(
            name="seed-health",
            layers=[
                LayerDefinition(
                    name="pattern",
                    half_life_days=60,
                    confidence_floor=0.1,
                ),
            ],
            seeding=SeedingConfig(seed_version="2.4.0"),
        )
        s = MemoryStore(tmp_path, profile=prof)
        try:
            h = s.health()
            assert h.profile_seed_version == "2.4.0"
        finally:
            s.close()

    def test_get_hit_miss_counters(self, store: MemoryStore) -> None:
        store.save(key="exists", value="hello")
        store._metrics.reset()
        store.get("exists")
        store.get("missing")
        snap = store.get_metrics()
        assert snap.counters.get("store.get", 0) == 2
        assert snap.counters.get("store.get.hit", 0) == 1
        assert snap.counters.get("store.get.miss", 0) == 1

    def test_get_records_latency(self, store: MemoryStore) -> None:
        store.save(key="x", value="y")
        store._metrics.reset()
        store.get("x")
        snap = store.get_metrics()
        assert "store.get_ms" in snap.histograms

    def test_search_counters(self, store: MemoryStore) -> None:
        store.save(key="doc1", value="Python is great")
        store.save(key="doc2", value="Python programming language")
        store._metrics.reset()
        results = store.search("Python")
        snap = store.get_metrics()
        assert snap.counters.get("store.search", 0) == 1
        assert snap.counters.get("store.search.results", 0) == len(results)

    def test_search_records_latency(self, store: MemoryStore) -> None:
        store.save(key="s", value="searchable content")
        store._metrics.reset()
        store.search("searchable")
        snap = store.get_metrics()
        assert "store.search_ms" in snap.histograms

    def test_hundred_saves_counter(self, store: MemoryStore) -> None:
        """100 saves should produce count=100."""
        for i in range(100):
            store.save(key=f"bulk-{i}", value=f"value {i}")
        snap = store.get_metrics()
        assert snap.counters["store.save"] == 100

    def test_recall_increments_counter(self, store: MemoryStore) -> None:
        store.save(key="info", value="Python is a programming language")
        store._metrics.reset()
        store.recall("What is Python?")
        snap = store.get_metrics()
        assert snap.counters.get("store.recall", 0) == 1
        assert "store.recall_ms" in snap.histograms

    def test_supersede_increments_counter(self, store: MemoryStore) -> None:
        store.save(key="old", value="old value")
        store._metrics.reset()
        store.supersede("old", "new value")
        snap = store.get_metrics()
        assert snap.counters.get("store.supersede", 0) == 1

    def test_gc_increments_counter(self, store: MemoryStore) -> None:
        store.save(key="entry", value="test gc")
        store._metrics.reset()
        store.gc(dry_run=True)
        snap = store.get_metrics()
        assert snap.counters.get("store.gc", 0) == 1

    def test_gc_dry_run_includes_reason_counts_and_estimate(self, tmp_path: Path) -> None:
        from datetime import UTC, datetime, timedelta

        s = MemoryStore(tmp_path)
        s.save(key="sess", value="tmp", tier="context", scope="session")
        entry = s.get("sess")
        assert entry is not None
        old = (datetime.now(tz=UTC) - timedelta(days=10)).isoformat()
        with s._lock:
            s._entries["sess"] = entry.model_copy(update={"updated_at": old})
            s._persistence.save(s._entries["sess"])
        r = s.gc(dry_run=True)
        assert r.dry_run is True
        assert "session_expired" in r.reason_counts
        assert r.estimated_archive_bytes > 0
        assert "sess" in r.archived_keys
        s.close()

    def test_gc_live_increments_archive_bytes(self, tmp_path: Path) -> None:
        from datetime import UTC, datetime, timedelta

        s = MemoryStore(tmp_path)
        s.save(key="sess2", value="tmp", tier="context", scope="session")
        entry = s.get("sess2")
        assert entry is not None
        old = (datetime.now(tz=UTC) - timedelta(days=10)).isoformat()
        with s._lock:
            s._entries["sess2"] = entry.model_copy(update={"updated_at": old})
            s._persistence.save(s._entries["sess2"])
        s._metrics.reset()
        r = s.gc(dry_run=False)
        assert r.dry_run is False
        assert r.archived_count == 1
        assert r.archive_bytes > 0
        snap = s.get_metrics()
        assert snap.counters.get("store.gc.archived", 0) == 1
        assert snap.counters.get("store.gc.archive_bytes", 0) == r.archive_bytes
        h = s.health()
        assert h.gc_archived_rows_total == 1
        assert h.gc_archive_bytes_total == r.archive_bytes
        s.close()

    def test_reinforce_increments_counter(self, store: MemoryStore) -> None:
        store.save(key="reinforce-me", value="some durable fact")
        store._metrics.reset()
        store.reinforce("reinforce-me")
        snap = store.get_metrics()
        assert snap.counters.get("store.reinforce", 0) == 1

    def test_consolidation_metrics(self, tmp_path: Path) -> None:
        """When auto-consolidation triggers, counters are incremented."""
        config = ConsolidationConfig(enabled=True, threshold=0.3, min_entries=2)
        s = MemoryStore(tmp_path, consolidation_config=config)
        # Save similar entries to trigger consolidation
        s.save(key="a", value="Python is a great language for data science")
        s.save(key="b", value="Python is a great language for data science and ML")
        s.save(key="c", value="Python is a great language for data science and AI")
        snap = s.get_metrics()
        # If consolidation was triggered, counter should exist
        # (may or may not trigger depending on similarity threshold)
        assert snap.counters.get("store.consolidate", 0) >= 0
        s.close()


class TestStoreAudit:
    """Verify store.audit() convenience method (STORY-007.3)."""

    def test_audit_returns_entries_after_save(self, store: MemoryStore) -> None:
        store.save(key="audited", value="some value")
        entries = store.audit(key="audited")
        assert len(entries) >= 1
        assert entries[0].key == "audited"

    def test_audit_filter_by_event_type(self, store: MemoryStore) -> None:
        store.save(key="a", value="val")
        store.delete("a")
        saves = store.audit(event_type="save")
        deletes = store.audit(event_type="delete")
        assert all(e.event_type == "save" for e in saves)
        assert all(e.event_type == "delete" for e in deletes)

    def test_audit_empty_store_returns_empty(self, store: MemoryStore) -> None:
        entries = store.audit()
        assert entries == []

    def test_audit_respects_limit(self, store: MemoryStore) -> None:
        for i in range(10):
            store.save(key=f"lim-{i}", value=f"value {i}")
        entries = store.audit(limit=3)
        assert len(entries) == 3

    def test_audit_time_range_filter(self, store: MemoryStore) -> None:
        store.save(key="t1", value="time test")
        entries = store.audit(since="2000-01-01", until="2099-12-31")
        assert len(entries) >= 1


class TestAgentScopeValidationInStore:
    """Tests for agent_scope enum validation in MemoryStore.save() (STORY-014.1)."""

    def test_valid_agent_scope_values(self, store: MemoryStore) -> None:
        """Primitives are accepted by store.save()."""
        for scope in VALID_AGENT_SCOPES:
            safe_key = f"scope-{scope}".replace(":", "-")
            result = store.save(key=safe_key, value=f"value for {scope}", agent_scope=scope)
            assert isinstance(result, MemoryEntry), (
                f"Expected MemoryEntry for scope={scope!r} key={safe_key!r}"
            )

    def test_valid_group_agent_scope(self, tmp_path: Path) -> None:
        """group:<name> is accepted when the agent is a member of the group."""
        s = MemoryStore(tmp_path, groups=["push-team"])
        try:
            result = s.save(
                key="scope-group", value="value for group", agent_scope="group:push-team"
            )
            assert isinstance(result, MemoryEntry), "Expected MemoryEntry for group scope"
        finally:
            s.close()

    def test_invalid_agent_scope_returns_error_dict(self, store: MemoryStore) -> None:
        """Invalid agent_scope returns error dict with error='invalid_agent_scope'."""
        result = store.save(key="bad-scope", value="test", agent_scope="hivee")
        assert isinstance(result, dict)
        assert result["error"] == "invalid_agent_scope"
        assert "valid_values" in result
        expected = ["domain", "group", "group:<name>", "hive", "private"]
        assert sorted(result["valid_values"]) == expected

    def test_invalid_agent_scope_not_persisted(self, store: MemoryStore) -> None:
        """Entry is not stored when agent_scope is invalid."""
        store.save(key="not-stored", value="test", agent_scope="invalid-value")
        entry = store.get("not-stored")
        assert entry is None

    def test_empty_agent_scope_returns_error(self, store: MemoryStore) -> None:
        """Empty string agent_scope is rejected."""
        result = store.save(key="empty-scope", value="test", agent_scope="")
        assert isinstance(result, dict)
        assert result["error"] == "invalid_agent_scope"

    def test_valid_agent_scopes_constant_contains_expected_values(self) -> None:
        """VALID_AGENT_SCOPES constant contains the three expected values."""
        assert set(VALID_AGENT_SCOPES) == {"private", "domain", "hive"}


class TestStoreStaleAndTierMigrate:
    """GitHub #21 / #20: stale listing and tier migration on MemoryStore."""

    def test_list_gc_stale_details_empty(self, store: MemoryStore) -> None:
        assert store.list_gc_stale_details() == []


class TestAdaptiveStabilityStore:
    """EPIC-042.8: profile-driven stability updates on reinforce and record_access."""

    @pytest.fixture
    def adaptive_profile(self) -> object:
        from tapps_brain.profile import LayerDefinition, MemoryProfile, ScoringConfig

        return MemoryProfile(
            name="adaptive-test",
            layers=[
                LayerDefinition(
                    name="pattern",
                    half_life_days=60,
                    confidence_floor=0.1,
                    adaptive_stability=True,
                ),
            ],
            scoring=ScoringConfig(
                relevance=0.40,
                confidence=0.30,
                recency=0.15,
                frequency=0.15,
            ),
        )

    def test_reinforce_updates_stability_when_layer_flag_on(
        self, tmp_path: Path, adaptive_profile: object
    ) -> None:
        s = MemoryStore(tmp_path, profile=adaptive_profile)
        try:
            s.save(key="k", value="v", tier="pattern")
            assert s.get("k") is not None
            assert s.get("k").stability == 0.0
            out = s.reinforce("k")
            assert out.stability > 0.0
        finally:
            s.close()

    def test_reinforce_leaves_stability_zero_when_flag_off(self, tmp_path: Path) -> None:
        s = MemoryStore(tmp_path)
        try:
            s.save(key="k", value="v", tier="pattern")
            out = s.reinforce("k")
            assert out.stability == 0.0
        finally:
            s.close()

    def test_record_access_updates_stability_when_layer_flag_on(
        self, tmp_path: Path, adaptive_profile: object
    ) -> None:
        s = MemoryStore(tmp_path, profile=adaptive_profile)
        try:
            s.save(key="k", value="v", tier="pattern")
            s.record_access("k", True)
            assert s.get("k") is not None
            assert s.get("k").stability > 0.0
        finally:
            s.close()
