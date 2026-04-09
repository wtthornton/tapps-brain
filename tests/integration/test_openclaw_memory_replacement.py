"""Integration tests for OpenClaw memory replacement (EPIC-026, story-026.6).

Tests the complete memory replacement flow:
- memory_search tool backed by tapps-brain returns results from the store
- memory_get tool backed by tapps-brain returns single entries from the store
- save → search → get round-trip via the store's public API
- bidirectional MEMORY.md sync (export then re-import)
- memory slot active: all data comes from tapps-brain

Story: STORY-026.6 from EPIC-026
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from tapps_brain.markdown_sync import get_sync_state, sync_from_markdown, sync_to_markdown
from tapps_brain.store import MemoryStore

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path: Path) -> MemoryStore:
    """Create a real MemoryStore backed by SQLite in a temp directory."""
    s = MemoryStore(tmp_path / "store")
    yield s  # type: ignore[misc]
    s.close()


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    """Return a clean workspace directory (simulates an OpenClaw workspace)."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 1. memory_search — results from tapps-brain
# ---------------------------------------------------------------------------


class TestMemorySearchBackedByTappsBrain:
    """memory_search should surface entries saved via the MemoryStore."""

    def test_search_returns_saved_entry(self, store: MemoryStore) -> None:
        store.save("sqlite-wal-mode", "SQLite uses WAL mode for concurrency.", tier="architectural")

        results = store.search("WAL mode concurrency")
        keys = [e.key for e in results]

        assert "sqlite-wal-mode" in keys

    def test_search_multiple_entries_ranked(self, store: MemoryStore) -> None:
        store.save("bm25-ranking", "BM25 ranks results by term frequency.", tier="pattern")
        store.save("decay-scoring", "Exponential decay applies to old entries.", tier="pattern")
        store.save(
            "vector-search", "Optional FAISS embeddings for vector similarity.", tier="pattern"
        )

        results = store.search("BM25 ranking frequency")
        assert len(results) >= 1
        # The BM25 entry should appear
        assert any(e.key == "bm25-ranking" for e in results)

    def test_search_empty_store_returns_empty(self, store: MemoryStore) -> None:
        results = store.search("does not exist anywhere")
        assert results == []

    def test_search_tier_filter_limits_results(self, store: MemoryStore) -> None:
        store.save("arch-entry", "Core architecture decision.", tier="architectural")
        store.save("ctx-entry", "Core session context.", tier="context")

        arch_results = store.search("core", tier="architectural")
        ctx_results = store.search("core", tier="context")

        arch_keys = [e.key for e in arch_results]
        ctx_keys = [e.key for e in ctx_results]

        assert "arch-entry" in arch_keys
        assert "ctx-entry" not in arch_keys

        assert "ctx-entry" in ctx_keys
        assert "arch-entry" not in ctx_keys

    def test_search_returns_openlaw_compatible_fields(self, store: MemoryStore) -> None:
        """Results include fields needed for OpenClaw memory_search format."""
        store.save("oc-compat", "Value for OpenClaw.", tier="pattern", tags=["oc"])

        results = store.search("OpenClaw")
        assert len(results) >= 1

        entry = next(e for e in results if e.key == "oc-compat")
        # Fields required for the memory_search → OpenClaw format mapping
        assert entry.key is not None  # key → path
        assert entry.value is not None  # value → text
        assert entry.confidence >= 0.0  # confidence → score
        assert isinstance(entry.tags, list)

    def test_search_after_delete_excludes_deleted(self, store: MemoryStore) -> None:
        store.save("temp-fact", "Temporary architectural fact.", tier="architectural")
        assert store.delete("temp-fact")

        results = store.search("temporary architectural")
        assert not any(e.key == "temp-fact" for e in results)


# ---------------------------------------------------------------------------
# 2. memory_get — single entry from tapps-brain
# ---------------------------------------------------------------------------


class TestMemoryGetBackedByTappsBrain:
    """memory_get should retrieve a specific entry by key from the store."""

    def test_get_existing_entry_returns_entry(self, store: MemoryStore) -> None:
        store.save("my-key", "My stored value.", tier="pattern")

        entry = store.get("my-key")

        assert entry is not None
        assert entry.key == "my-key"
        assert entry.value == "My stored value."

    def test_get_missing_key_returns_none(self, store: MemoryStore) -> None:
        entry = store.get("nonexistent-key")
        assert entry is None

    def test_get_returns_correct_tier(self, store: MemoryStore) -> None:
        store.save("arch-k", "Arch value.", tier="architectural")
        store.save("ctx-k", "Context value.", tier="context")

        arch = store.get("arch-k")
        ctx = store.get("ctx-k")

        assert arch is not None
        assert str(arch.tier) == "architectural"

        assert ctx is not None
        assert str(ctx.tier) == "context"

    def test_get_supports_key_with_path_prefix(self, store: MemoryStore) -> None:
        """Simulate the OpenClaw path → key extraction: 'memory/my-key.md' → 'my-key'."""
        store.save("my-entry", "Entry content.", tier="procedural")

        # Simulate path stripping as done in the TS plugin
        path = "memory/my-entry.md"
        key = path.removeprefix("memory/").removesuffix(".md")

        entry = store.get(key)
        assert entry is not None
        assert entry.value == "Entry content."

    def test_get_returns_tags(self, store: MemoryStore) -> None:
        store.save("tagged-entry", "Has tags.", tier="pattern", tags=["alpha", "beta"])

        entry = store.get("tagged-entry")
        assert entry is not None
        assert "alpha" in entry.tags
        assert "beta" in entry.tags

    def test_get_entry_is_json_serialisable(self, store: MemoryStore) -> None:
        """Ensure the retrieved entry can be serialised for MCP responses."""
        store.save("json-safe", "Serialisable value.", tier="pattern")

        entry = store.get("json-safe")
        assert entry is not None

        raw = json.dumps(entry.model_dump(mode="json"))
        decoded: dict[str, Any] = json.loads(raw)
        assert decoded["key"] == "json-safe"
        assert decoded["value"] == "Serialisable value."


# ---------------------------------------------------------------------------
# 3. save → search → get round-trip
# ---------------------------------------------------------------------------


class TestSaveSearchGetRoundTrip:
    """End-to-end: save an entry, search for it, retrieve it by key."""

    def test_basic_round_trip(self, store: MemoryStore) -> None:
        store.save("round-trip-key", "Round-trip test value.", tier="pattern")

        search_results = store.search("round-trip test")
        assert any(e.key == "round-trip-key" for e in search_results)

        entry = store.get("round-trip-key")
        assert entry is not None
        assert entry.value == "Round-trip test value."

    def test_round_trip_all_tiers(self, store: MemoryStore) -> None:
        tiers = ["architectural", "pattern", "procedural", "context"]
        for tier in tiers:
            store.save(f"rt-{tier}", f"Value for tier {tier}.", tier=tier)

        for tier in tiers:
            results = store.search(f"tier {tier}")
            assert any(e.key == f"rt-{tier}" for e in results), f"Missing result for tier={tier}"

            entry = store.get(f"rt-{tier}")
            assert entry is not None
            assert str(entry.tier) == tier

    def test_round_trip_persists_across_store_reopen(self, tmp_path: Path) -> None:
        store_dir = tmp_path / "persistent_store"
        store1 = MemoryStore(store_dir)
        try:
            store1.save("persistent-key", "Persists across reopens.", tier="architectural")
        finally:
            store1.close()

        store2 = MemoryStore(store_dir)
        try:
            results = store2.search("persists reopens")
            assert any(e.key == "persistent-key" for e in results)

            entry = store2.get("persistent-key")
            assert entry is not None
            assert entry.value == "Persists across reopens."
        finally:
            store2.close()

    def test_round_trip_with_tags(self, store: MemoryStore) -> None:
        store.save(
            "tagged-rt",
            "Tagged round-trip entry.",
            tier="pattern",
            tags=["oc-search", "integration"],
        )

        results = store.search("tagged round-trip")
        assert any(e.key == "tagged-rt" for e in results)

        entry = store.get("tagged-rt")
        assert entry is not None
        assert "oc-search" in entry.tags

    def test_round_trip_high_confidence_recall(self, store: MemoryStore) -> None:
        store.save(
            "conf-key",
            "High-confidence architectural decision.",
            tier="architectural",
            confidence=0.95,
        )

        results = store.search("high-confidence architectural")
        entry_from_search = next((e for e in results if e.key == "conf-key"), None)
        assert entry_from_search is not None
        assert entry_from_search.confidence >= 0.9

        entry_from_get = store.get("conf-key")
        assert entry_from_get is not None
        assert entry_from_get.confidence >= 0.9


# ---------------------------------------------------------------------------
# 4. Bidirectional MEMORY.md sync
# ---------------------------------------------------------------------------


class TestBidirectionalSync:
    """Export entries to MEMORY.md, edit the file, re-import — verify round-trip."""

    def test_export_then_import_round_trip(self, store: MemoryStore, workspace: Path) -> None:
        store.save("sync-arch", "Core sync architecture.", tier="architectural")
        store.save("sync-pat", "Sync coding pattern.", tier="pattern")

        export_result = sync_to_markdown(store, workspace)
        assert export_result["exported"] == 2

        # Fresh store — import from the generated MEMORY.md
        store2 = MemoryStore(workspace / "store2")
        try:
            import_result = sync_from_markdown(store2, workspace)
            assert import_result["imported"] == 2
            assert import_result["skipped"] == 0

            arch = store2.get("sync-arch")
            assert arch is not None
            assert "Core sync architecture." in arch.value
            assert str(arch.tier) == "architectural"

            pat = store2.get("sync-pat")
            assert pat is not None
            assert "Sync coding pattern." in pat.value
            assert str(pat.tier) == "pattern"
        finally:
            store2.close()

    def test_tapps_brain_wins_on_conflict(self, store: MemoryStore, workspace: Path) -> None:
        """Existing entries are never overwritten on import."""
        store.save("conflict-key", "TAPPS-BRAIN VALUE", tier="pattern")

        # Write MEMORY.md with a conflicting value
        (workspace / "MEMORY.md").write_text(
            "# Memory\n\n### conflict-key\n\nEXTERNAL OVERWRITE VALUE.\n",
            encoding="utf-8",
        )

        result = sync_from_markdown(store, workspace)
        assert result["skipped"] == 1
        assert result["imported"] == 0

        entry = store.get("conflict-key")
        assert entry is not None
        assert entry.value == "TAPPS-BRAIN VALUE"  # tapps-brain won

    def test_sync_state_updated_after_export(self, store: MemoryStore, workspace: Path) -> None:
        store.save("state-key", "State check value.", tier="context")

        sync_to_markdown(store, workspace)
        state = get_sync_state(workspace)

        assert "last_sync_to" in state
        # Must be a valid ISO-8601 timestamp
        from datetime import datetime

        dt = datetime.fromisoformat(state["last_sync_to"])
        assert dt.tzinfo is not None

    def test_sync_state_updated_after_import(self, store: MemoryStore, workspace: Path) -> None:
        (workspace / "MEMORY.md").write_text(
            "# Memory\n\n## import-key\n\nImport test.\n",
            encoding="utf-8",
        )

        sync_from_markdown(store, workspace)
        state = get_sync_state(workspace)

        assert "last_sync_from" in state
        from datetime import datetime

        dt = datetime.fromisoformat(state["last_sync_from"])
        assert dt.tzinfo is not None

    def test_daily_notes_imported_as_context_tier(
        self, store: MemoryStore, workspace: Path
    ) -> None:
        memory_dir = workspace / "memory"
        memory_dir.mkdir()
        (memory_dir / "2026-03-20.md").write_text(
            "# Daily Note\n\nToday we fixed the retry logic.\n",
            encoding="utf-8",
        )

        result = sync_from_markdown(store, workspace)
        assert result["daily_notes"] >= 1

        entry = store.get("daily-2026-03-20")
        assert entry is not None
        assert str(entry.tier) == "context"
        assert "retry logic" in entry.value

    def test_re_export_idempotent_file(self, store: MemoryStore, workspace: Path) -> None:
        """Exporting twice produces identical MEMORY.md content."""
        store.save("idem-key", "Idempotent value.", tier="pattern")

        sync_to_markdown(store, workspace)
        content1 = (workspace / "MEMORY.md").read_text(encoding="utf-8")

        sync_to_markdown(store, workspace)
        content2 = (workspace / "MEMORY.md").read_text(encoding="utf-8")

        assert content1 == content2


# ---------------------------------------------------------------------------
# 5. Migration from mock workspace data
# ---------------------------------------------------------------------------


class TestMemorySlotFromTappsBrain:
    """When tapps-brain is the active memory slot, all data comes from its store.

    Since the TS plugin is not testable in Python, these tests verify the
    underlying Python API that the plugin calls: the store's search/get are the
    sole source of truth.
    """

    def test_memory_slot_agent_save_takes_precedence(self, store: MemoryStore) -> None:
        """Agent-saved entries override imported entries if they already exist."""
        # Simulate an agent saving a fact (tapps-brain always wins)
        store.save("agent-key", "AGENT VALUE", tier="pattern", source="agent")

        # Simulate a subsequent import attempt (should be skipped)
        from tapps_brain.markdown_sync import _parse_memory_md_sections

        sections = _parse_memory_md_sections("# Memory\n\n### agent-key\n\nIMPORT OVERWRITE.\n")
        assert len(sections) == 1
        key, value, tier = sections[0]
        assert key == "agent-key"

        # Only save if key absent (tapps-brain wins rule)
        if store.get(key) is None:
            store.save(key, value, tier=tier)

        entry = store.get("agent-key")
        assert entry is not None
        assert entry.value == "AGENT VALUE"  # agent value preserved

    def test_memory_slot_get_returns_none_for_missing_key(self, store: MemoryStore) -> None:
        """memory_get graceful degradation: returns None for missing keys."""
        assert store.get("missing-key") is None

    def test_memory_slot_list_shows_all_sources(self, store: MemoryStore) -> None:
        """list_all returns entries from all sources."""
        store.save("agent-entry", "Agent saved this.", tier="pattern", source="agent")
        store.save("system-entry", "System saved this.", tier="pattern", source="system")

        all_entries = store.list_all()
        keys = [e.key for e in all_entries]

        assert "agent-entry" in keys
        assert "system-entry" in keys
