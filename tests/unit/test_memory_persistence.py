"""Unit tests for memory persistence layer (Epic 23, Story 2)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from tapps_brain.models import (
    MemoryEntry,
    MemoryScope,
    MemorySource,
    MemoryTier,
)
from tapps_brain.persistence import MemoryPersistence

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


@pytest.fixture()
def persistence(tmp_path: Path) -> Generator[MemoryPersistence, None, None]:
    """Create a MemoryPersistence instance backed by a temp directory."""
    p = MemoryPersistence(tmp_path)
    yield p
    p.close()


@pytest.fixture()
def sample_entry() -> MemoryEntry:
    """Create a sample MemoryEntry for testing."""
    return MemoryEntry(
        key="test-key",
        value="Test value for persistence",
        tier=MemoryTier.pattern,
        source=MemorySource.agent,
        tags=["python", "testing"],
    )


class TestMemoryPersistence:
    """Tests for MemoryPersistence."""

    def test_save_and_get_roundtrip(
        self, persistence: MemoryPersistence, sample_entry: MemoryEntry
    ) -> None:
        persistence.save(sample_entry)
        loaded = persistence.get("test-key")
        assert loaded is not None
        assert loaded.key == "test-key"
        assert loaded.value == "Test value for persistence"
        assert loaded.tier == MemoryTier.pattern
        assert loaded.tags == ["python", "testing"]

    def test_get_nonexistent_returns_none(self, persistence: MemoryPersistence) -> None:
        assert persistence.get("nonexistent") is None

    def test_save_replaces_existing(self, persistence: MemoryPersistence) -> None:
        entry1 = MemoryEntry(key="k1", value="original")
        persistence.save(entry1)

        entry2 = MemoryEntry(key="k1", value="updated")
        persistence.save(entry2)

        loaded = persistence.get("k1")
        assert loaded is not None
        assert loaded.value == "updated"
        assert persistence.count() == 1

    def test_delete(self, persistence: MemoryPersistence, sample_entry: MemoryEntry) -> None:
        persistence.save(sample_entry)
        assert persistence.delete("test-key") is True
        assert persistence.get("test-key") is None
        assert persistence.count() == 0

    def test_delete_nonexistent_returns_false(self, persistence: MemoryPersistence) -> None:
        assert persistence.delete("nonexistent") is False

    def test_list_all_no_filters(self, persistence: MemoryPersistence) -> None:
        persistence.save(MemoryEntry(key="k1", value="v1"))
        persistence.save(MemoryEntry(key="k2", value="v2"))
        entries = persistence.list_all()
        assert len(entries) == 2

    def test_list_all_filter_by_tier(self, persistence: MemoryPersistence) -> None:
        persistence.save(MemoryEntry(key="a1", value="v", tier=MemoryTier.architectural))
        persistence.save(MemoryEntry(key="p1", value="v", tier=MemoryTier.pattern))
        entries = persistence.list_all(tier="architectural")
        assert len(entries) == 1
        assert entries[0].key == "a1"

    def test_list_all_filter_by_scope(self, persistence: MemoryPersistence) -> None:
        persistence.save(MemoryEntry(key="proj1", value="v", scope=MemoryScope.project))
        persistence.save(
            MemoryEntry(
                key="br1",
                value="v",
                scope=MemoryScope.branch,
                branch="main",
            )
        )
        entries = persistence.list_all(scope="project")
        assert len(entries) == 1
        assert entries[0].key == "proj1"

    def test_list_all_filter_by_tags(self, persistence: MemoryPersistence) -> None:
        persistence.save(MemoryEntry(key="k1", value="v", tags=["python", "testing"]))
        persistence.save(MemoryEntry(key="k2", value="v", tags=["rust"]))
        entries = persistence.list_all(tags=["python"])
        assert len(entries) == 1
        assert entries[0].key == "k1"

    def test_search_fts5(self, persistence: MemoryPersistence) -> None:
        persistence.save(MemoryEntry(key="architecture-decision", value="Use SQLite for storage"))
        persistence.save(MemoryEntry(key="coding-pattern", value="Always use type hints"))
        results = persistence.search("SQLite")
        assert len(results) >= 1
        assert any(r.key == "architecture-decision" for r in results)

    def test_search_empty_query_returns_empty(self, persistence: MemoryPersistence) -> None:
        persistence.save(MemoryEntry(key="k1", value="v1"))
        assert persistence.search("") == []
        assert persistence.search("   ") == []

    def test_load_all(self, persistence: MemoryPersistence) -> None:
        persistence.save(MemoryEntry(key="k1", value="v1"))
        persistence.save(MemoryEntry(key="k2", value="v2"))
        all_entries = persistence.load_all()
        assert len(all_entries) == 2

    def test_count(self, persistence: MemoryPersistence) -> None:
        assert persistence.count() == 0
        persistence.save(MemoryEntry(key="k1", value="v1"))
        assert persistence.count() == 1
        persistence.save(MemoryEntry(key="k2", value="v2"))
        assert persistence.count() == 2

    def test_schema_version(self, persistence: MemoryPersistence) -> None:
        # v15 Bayesian confidence counters (GitHub #35)
        assert persistence.get_schema_version() == 16

    def test_wal_mode_enabled(self, tmp_path: Path) -> None:
        p = MemoryPersistence(tmp_path)
        row = p._conn.execute("PRAGMA journal_mode").fetchone()
        assert row[0] == "wal"
        p.close()

    def test_audit_log_created(
        self, persistence: MemoryPersistence, sample_entry: MemoryEntry
    ) -> None:
        persistence.save(sample_entry)
        audit_path = persistence._audit_path
        assert audit_path.exists()
        lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) >= 1
        record = json.loads(lines[0])
        assert record["action"] == "save"
        assert record["key"] == "test-key"

    def test_audit_log_records_delete(
        self, persistence: MemoryPersistence, sample_entry: MemoryEntry
    ) -> None:
        persistence.save(sample_entry)
        persistence.delete("test-key")
        lines = persistence._audit_path.read_text(encoding="utf-8").strip().splitlines()
        actions = [json.loads(line)["action"] for line in lines]
        assert "save" in actions
        assert "delete" in actions

    def test_persists_across_instances(self, tmp_path: Path) -> None:
        p1 = MemoryPersistence(tmp_path)
        p1.save(MemoryEntry(key="survive", value="across restart"))
        p1.close()

        p2 = MemoryPersistence(tmp_path)
        loaded = p2.get("survive")
        assert loaded is not None
        assert loaded.value == "across restart"
        p2.close()

    def test_confidence_and_source_preserved(self, persistence: MemoryPersistence) -> None:
        entry = MemoryEntry(
            key="conf-test",
            value="v",
            source=MemorySource.human,
            confidence=0.85,
        )
        persistence.save(entry)
        loaded = persistence.get("conf-test")
        assert loaded is not None
        assert loaded.confidence == 0.85
        assert loaded.source == MemorySource.human

    def test_branch_scope_preserved(self, persistence: MemoryPersistence) -> None:
        entry = MemoryEntry(
            key="branch-test",
            value="v",
            scope=MemoryScope.branch,
            branch="feature-x",
        )
        persistence.save(entry)
        loaded = persistence.get("branch-test")
        assert loaded is not None
        assert loaded.scope == MemoryScope.branch
        assert loaded.branch == "feature-x"

    def test_close_is_safe(self, tmp_path: Path) -> None:
        """Closing persistence should not raise."""
        p = MemoryPersistence(tmp_path)
        p.close()

    def test_save_entry_with_embedding(self, persistence: MemoryPersistence) -> None:
        """Entries with embeddings roundtrip through save/get."""
        entry = MemoryEntry(
            key="emb-test",
            value="embedding roundtrip",
            embedding=[0.1, 0.2, 0.3],
        )
        persistence.save(entry)
        loaded = persistence.get("emb-test")
        assert loaded is not None
        assert loaded.embedding == [0.1, 0.2, 0.3]

    def test_save_entry_without_embedding(self, persistence: MemoryPersistence) -> None:
        """Entries without embeddings get None on load."""
        entry = MemoryEntry(key="no-emb", value="no embedding")
        persistence.save(entry)
        loaded = persistence.get("no-emb")
        assert loaded is not None
        assert loaded.embedding is None


class TestSchemaMigrations:
    """Tests for schema migration paths."""

    def _create_v1_db(self, db_path: str) -> None:
        """Manually create a v1 schema database."""
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version "
            "(version INTEGER NOT NULL, migrated_at TEXT NOT NULL)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                tier TEXT NOT NULL DEFAULT 'pattern',
                confidence REAL NOT NULL DEFAULT 0.6,
                source TEXT NOT NULL DEFAULT 'agent',
                source_agent TEXT NOT NULL DEFAULT 'unknown',
                scope TEXT NOT NULL DEFAULT 'project',
                tags TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_accessed TEXT NOT NULL,
                access_count INTEGER NOT NULL DEFAULT 0,
                branch TEXT,
                last_reinforced TEXT,
                reinforce_count INTEGER NOT NULL DEFAULT 0,
                contradicted INTEGER NOT NULL DEFAULT 0,
                contradiction_reason TEXT,
                seeded_from TEXT,
                PRIMARY KEY (key)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_tier ON memories(tier)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(scope)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_confidence ON memories(confidence)")
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
            USING fts5(key, value, tags, content=memories, content_rowid=rowid)
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                INSERT INTO memories_fts(rowid, key, value, tags)
                VALUES (new.rowid, new.key, new.value, new.tags);
            END
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, key, value, tags)
                VALUES ('delete', old.rowid, old.key, old.value, old.tags);
            END
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, key, value, tags)
                VALUES ('delete', old.rowid, old.key, old.value, old.tags);
                INSERT INTO memories_fts(rowid, key, value, tags)
                VALUES (new.rowid, new.key, new.value, new.tags);
            END
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS archived_memories (
                key TEXT NOT NULL, value TEXT NOT NULL, tier TEXT NOT NULL,
                confidence REAL NOT NULL, source TEXT NOT NULL,
                source_agent TEXT NOT NULL, scope TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, last_accessed TEXT NOT NULL,
                access_count INTEGER NOT NULL DEFAULT 0, branch TEXT,
                last_reinforced TEXT, reinforce_count INTEGER NOT NULL DEFAULT 0,
                contradicted INTEGER NOT NULL DEFAULT 0, contradiction_reason TEXT,
                seeded_from TEXT, archived_at TEXT NOT NULL
            )
        """)
        now = datetime.now(tz=UTC).isoformat()
        conn.execute(
            "INSERT INTO schema_version (version, migrated_at) VALUES (?, ?)",
            (1, now),
        )
        conn.commit()
        conn.close()

    def test_migrate_v1_to_current(self, tmp_path: Path) -> None:
        """Opening a v1 DB should migrate it all the way to current schema."""
        store_dir = tmp_path / ".tapps-brain" / "memory"
        store_dir.mkdir(parents=True)
        db_path = str(store_dir / "memory.db")
        self._create_v1_db(db_path)

        p = MemoryPersistence(tmp_path)
        assert p.get_schema_version() == 16

        # Verify v2 migration: embedding column exists
        row = p._conn.execute("PRAGMA table_info(memories)").fetchall()
        columns = [r[1] for r in row]
        assert "embedding" in columns

        # Verify v3 migration: session_index table exists
        tables = [
            r[0]
            for r in p._conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        ]
        assert "session_index" in tables

        # Verify v5 migration: temporal columns exist
        assert "valid_at" in columns
        assert "invalid_at" in columns
        assert "superseded_by" in columns

        # Verify v4 migration: relations table exists
        assert "relations" in tables

        # Verify v8 migration: integrity_hash column exists
        assert "integrity_hash" in columns

        # Verify v9 migration: feedback_events table exists
        assert "feedback_events" in tables
        # Verify v10+ migration: diagnostics_history table exists
        assert "diagnostics_history" in tables
        # Verify v16: project-local memory_group column (GitHub #49)
        assert "memory_group" in columns
        p.close()

    def test_migrate_v2_to_v4(self, tmp_path: Path) -> None:
        """A v2 DB (with embedding column) should migrate to current version."""
        store_dir = tmp_path / ".tapps-brain" / "memory"
        store_dir.mkdir(parents=True)
        db_path = str(store_dir / "memory.db")

        # Create v1, then manually add v2 migration
        self._create_v1_db(db_path)
        conn = sqlite3.connect(db_path)
        conn.execute("ALTER TABLE memories ADD COLUMN embedding TEXT")
        now = datetime.now(tz=UTC).isoformat()
        conn.execute(
            "INSERT INTO schema_version (version, migrated_at) VALUES (?, ?)",
            (2, now),
        )
        conn.commit()
        conn.close()

        p = MemoryPersistence(tmp_path)
        assert p.get_schema_version() == 16

        tables = [
            r[0]
            for r in p._conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        ]
        assert "session_index" in tables
        assert "relations" in tables
        p.close()

    def test_migrate_v3_to_v4(self, tmp_path: Path) -> None:
        """A v3 DB should migrate to current version (adds relations table and beyond)."""
        store_dir = tmp_path / ".tapps-brain" / "memory"
        store_dir.mkdir(parents=True)
        db_path = str(store_dir / "memory.db")

        self._create_v1_db(db_path)
        conn = sqlite3.connect(db_path)
        conn.execute("ALTER TABLE memories ADD COLUMN embedding TEXT")
        now = datetime.now(tz=UTC).isoformat()
        conn.execute(
            "INSERT INTO schema_version (version, migrated_at) VALUES (?, ?)",
            (2, now),
        )
        # Create session_index for v3
        conn.execute("""
            CREATE TABLE IF NOT EXISTS session_index (
                session_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (session_id, chunk_index)
            )
        """)
        conn.execute(
            "INSERT INTO schema_version (version, migrated_at) VALUES (?, ?)",
            (3, now),
        )
        conn.commit()
        conn.close()

        p = MemoryPersistence(tmp_path)
        assert p.get_schema_version() == 16
        tables = [
            r[0]
            for r in p._conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        ]
        assert "relations" in tables
        p.close()

    def test_v1_to_v2_duplicate_column_is_idempotent(self, tmp_path: Path) -> None:
        """Re-running v1->v2 migration when embedding column already exists."""
        store_dir = tmp_path / ".tapps-brain" / "memory"
        store_dir.mkdir(parents=True)
        db_path = str(store_dir / "memory.db")

        self._create_v1_db(db_path)
        # Pre-add embedding column to simulate partial migration
        conn = sqlite3.connect(db_path)
        conn.execute("ALTER TABLE memories ADD COLUMN embedding TEXT")
        conn.commit()
        conn.close()

        # Opening should not raise even though column already exists
        p = MemoryPersistence(tmp_path)
        assert p.get_schema_version() == 16
        p.close()

    def test_v1_data_survives_migration(self, tmp_path: Path) -> None:
        """Data inserted at v1 is still readable after migration to v4."""
        store_dir = tmp_path / ".tapps-brain" / "memory"
        store_dir.mkdir(parents=True)
        db_path = str(store_dir / "memory.db")

        self._create_v1_db(db_path)

        # Insert a row into the v1 schema
        conn = sqlite3.connect(db_path)
        now = datetime.now(tz=UTC).isoformat()
        conn.execute(
            "INSERT INTO memories "
            "(key, value, tier, confidence, source, source_agent, scope, tags, "
            "created_at, updated_at, last_accessed, access_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "old-key",
                "old value",
                "pattern",
                0.6,
                "agent",
                "unknown",
                "project",
                "[]",
                now,
                now,
                now,
                0,
            ),
        )
        conn.commit()
        conn.close()

        p = MemoryPersistence(tmp_path)
        loaded = p.get("old-key")
        assert loaded is not None
        assert loaded.value == "old value"
        assert loaded.embedding is None
        p.close()

    def test_migrate_v7_to_v8(self, tmp_path: Path) -> None:
        """A v7 DB should migrate through v8–v11 (integrity, feedback, diagnostics, flywheel)."""
        store_dir = tmp_path / ".tapps-brain" / "memory"
        store_dir.mkdir(parents=True)
        db_path = str(store_dir / "memory.db")

        # Build a v7 DB by creating from v1 and recording version 7 directly.
        self._create_v1_db(db_path)
        conn = sqlite3.connect(db_path)
        now = datetime.now(tz=UTC).isoformat()
        # Apply v2-v7 columns manually so the schema is in a real v7 state.
        conn.execute("ALTER TABLE memories ADD COLUMN embedding TEXT")
        conn.execute("ALTER TABLE memories ADD COLUMN valid_at TEXT")
        conn.execute("ALTER TABLE memories ADD COLUMN invalid_at TEXT")
        conn.execute("ALTER TABLE memories ADD COLUMN superseded_by TEXT")
        conn.execute("ALTER TABLE memories ADD COLUMN agent_scope TEXT DEFAULT 'private'")
        for ver in range(2, 8):
            conn.execute(
                "INSERT INTO schema_version (version, migrated_at) VALUES (?, ?)",
                (ver, now),
            )
        conn.commit()
        conn.close()

        p = MemoryPersistence(tmp_path)
        assert p.get_schema_version() == 16

        # Verify the integrity_hash column was added by the v7→v8 migration.
        row = p._conn.execute("PRAGMA table_info(memories)").fetchall()
        columns = [r[1] for r in row]
        assert "integrity_hash" in columns
        tables = [
            r[0]
            for r in p._conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        ]
        assert "feedback_events" in tables
        assert "diagnostics_history" in tables
        p.close()


class TestSessionIndex:
    """Tests for session_index operations."""

    def test_save_and_search_session_chunks(self, tmp_path: Path) -> None:
        p = MemoryPersistence(tmp_path)
        stored = p.save_session_chunks("sess-1", ["hello world", "foo bar"])
        assert stored == 2
        assert p.count_session_chunks() == 2

        results = p.search_session_index("hello")
        assert len(results) >= 1
        assert results[0]["session_id"] == "sess-1"
        p.close()

    def test_save_session_chunks_empty(self, tmp_path: Path) -> None:
        p = MemoryPersistence(tmp_path)
        assert p.save_session_chunks("sess-1", []) == 0
        assert p.save_session_chunks("", ["content"]) == 0
        assert p.save_session_chunks("  ", ["content"]) == 0
        p.close()

    def test_save_session_chunks_respects_max(self, tmp_path: Path) -> None:
        p = MemoryPersistence(tmp_path)
        chunks = [f"chunk {i}" for i in range(100)]
        stored = p.save_session_chunks("sess-1", chunks, max_chunks=5)
        assert stored == 5
        p.close()

    def test_save_session_chunks_truncates_content(self, tmp_path: Path) -> None:
        p = MemoryPersistence(tmp_path)
        long_content = "hello world repeated " * 50  # >1000 chars
        stored = p.save_session_chunks("sess-1", [long_content], max_chars_per_chunk=50)
        assert stored == 1
        # Verify stored content is truncated by reading from DB directly
        row = p._conn.execute(
            "SELECT content FROM session_index WHERE session_id = ?",
            ("sess-1",),
        ).fetchone()
        assert row is not None
        assert len(row[0]) <= 50
        p.close()

    def test_save_session_chunks_skips_empty_content(self, tmp_path: Path) -> None:
        p = MemoryPersistence(tmp_path)
        stored = p.save_session_chunks("sess-1", ["", "  ", "valid"])
        assert stored == 1
        p.close()

    def test_search_session_index_empty_query(self, tmp_path: Path) -> None:
        p = MemoryPersistence(tmp_path)
        assert p.search_session_index("") == []
        assert p.search_session_index("   ") == []
        p.close()

    def test_delete_expired_session_chunks(self, tmp_path: Path) -> None:
        p = MemoryPersistence(tmp_path)
        p.save_session_chunks("sess-1", ["old content"])
        # Delete with ttl_days=0 should delete everything
        deleted = p.delete_expired_session_chunks(ttl_days=0)
        assert deleted >= 1
        assert p.count_session_chunks() == 0
        p.close()

    def test_count_session_chunks_empty(self, tmp_path: Path) -> None:
        p = MemoryPersistence(tmp_path)
        assert p.count_session_chunks() == 0
        p.close()


class TestRelations:
    """Tests for relations table operations."""

    def test_save_and_list_relations(self, tmp_path: Path) -> None:
        p = MemoryPersistence(tmp_path)
        p.save_relation("moduleA", "depends_on", "moduleB", ["key1"], 0.9)
        relations = p.list_relations()
        assert len(relations) == 1
        assert relations[0]["subject"] == "moduleA"
        assert relations[0]["predicate"] == "depends_on"
        assert relations[0]["object_entity"] == "moduleB"
        assert relations[0]["source_entry_keys"] == ["key1"]
        assert relations[0]["confidence"] == 0.9
        assert "created_at" in relations[0]
        p.close()

    def test_save_relation_replaces(self, tmp_path: Path) -> None:
        p = MemoryPersistence(tmp_path)
        p.save_relation("A", "uses", "B", ["k1"], 0.5)
        p.save_relation("A", "uses", "B", ["k1", "k2"], 0.9)
        assert p.count_relations() == 1
        rel = p.list_relations()[0]
        assert rel["source_entry_keys"] == ["k1", "k2"]
        assert rel["confidence"] == 0.9
        p.close()

    def test_count_relations(self, tmp_path: Path) -> None:
        p = MemoryPersistence(tmp_path)
        assert p.count_relations() == 0
        p.save_relation("A", "uses", "B", [], 0.8)
        assert p.count_relations() == 1
        p.save_relation("C", "uses", "D", [], 0.8)
        assert p.count_relations() == 2
        p.close()

    def test_list_relations_empty(self, tmp_path: Path) -> None:
        p = MemoryPersistence(tmp_path)
        assert p.list_relations() == []
        p.close()

    def test_save_relations_batch(self, tmp_path: Path) -> None:
        from tapps_brain.relations import RelationEntry

        p = MemoryPersistence(tmp_path)
        rels = [
            RelationEntry(
                subject="moduleA",
                predicate="uses",
                object_entity="moduleB",
                source_entry_keys=[],
                confidence=0.9,
            ),
            RelationEntry(
                subject="moduleA",
                predicate="depends on",
                object_entity="moduleC",
                source_entry_keys=["other_key"],
                confidence=0.7,
            ),
        ]
        saved = p.save_relations("entry_key_1", rels)
        assert saved == 2
        assert p.count_relations() == 2
        # Verify key was added to source_entry_keys
        loaded = p.load_relations("entry_key_1")
        assert len(loaded) == 2
        for r in loaded:
            assert "entry_key_1" in r["source_entry_keys"]
        # The second relation should also have "other_key"
        dep_rel = next(r for r in loaded if r["predicate"] == "depends on")
        assert "other_key" in dep_rel["source_entry_keys"]
        p.close()

    def test_save_relations_empty_list(self, tmp_path: Path) -> None:
        p = MemoryPersistence(tmp_path)
        saved = p.save_relations("key1", [])
        assert saved == 0
        assert p.count_relations() == 0
        p.close()

    def test_load_relations_filters_by_key(self, tmp_path: Path) -> None:
        p = MemoryPersistence(tmp_path)
        p.save_relation("A", "uses", "B", ["key1"], 0.8)
        p.save_relation("C", "uses", "D", ["key2"], 0.8)
        p.save_relation("E", "uses", "F", ["key1", "key2"], 0.8)

        key1_rels = p.load_relations("key1")
        assert len(key1_rels) == 2
        subjects = {r["subject"] for r in key1_rels}
        assert subjects == {"A", "E"}

        key2_rels = p.load_relations("key2")
        assert len(key2_rels) == 2

        key3_rels = p.load_relations("key3")
        assert len(key3_rels) == 0
        p.close()

    def test_delete_relations_by_key(self, tmp_path: Path) -> None:
        p = MemoryPersistence(tmp_path)
        p.save_relation("A", "uses", "B", ["key1"], 0.8)
        p.save_relation("C", "uses", "D", ["key2"], 0.8)
        p.save_relation("E", "uses", "F", ["key1", "key2"], 0.8)

        deleted = p.delete_relations("key1")
        assert deleted == 2
        # Only the key2-only relation remains
        assert p.count_relations() == 1
        remaining = p.list_relations()
        assert remaining[0]["subject"] == "C"
        p.close()

    def test_delete_relations_no_match(self, tmp_path: Path) -> None:
        p = MemoryPersistence(tmp_path)
        p.save_relation("A", "uses", "B", ["key1"], 0.8)
        deleted = p.delete_relations("nonexistent")
        assert deleted == 0
        assert p.count_relations() == 1
        p.close()

    def test_save_relations_deduplicates_source_keys(self, tmp_path: Path) -> None:
        from tapps_brain.relations import RelationEntry

        p = MemoryPersistence(tmp_path)
        rel = RelationEntry(
            subject="A",
            predicate="uses",
            object_entity="B",
            source_entry_keys=["key1"],
            confidence=0.8,
        )
        # Save with key1 — should not duplicate "key1" in source_entry_keys
        p.save_relations("key1", [rel])
        loaded = p.load_relations("key1")
        assert len(loaded) == 1
        assert loaded[0]["source_entry_keys"].count("key1") == 1
        p.close()


class TestAuditLogTruncation:
    """Tests for audit log truncation behavior."""

    def test_audit_log_truncation(self, tmp_path: Path) -> None:
        """Audit log truncates when exceeding max lines."""
        p = MemoryPersistence(tmp_path)
        # Write many entries to trigger audit log growth
        # We can write directly to the audit log to simulate large logs
        audit_path = p._audit_path
        # Write 10001 lines
        lines = []
        for i in range(10_001):
            record = {"action": "save", "key": f"k-{i}", "timestamp": "2024-01-01"}
            lines.append(json.dumps(record))
        audit_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        # Trigger a save which calls _maybe_truncate_audit
        entry = MemoryEntry(key="trunc-test", value="trigger truncation")
        p.save(entry)

        result_lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(result_lines) <= 10_001  # save adds one then truncates
        p.close()

    def test_audit_log_write_failure(self, tmp_path: Path) -> None:
        """Audit log failure should not crash save."""
        p = MemoryPersistence(tmp_path)
        # Make audit path a directory to cause OSError
        p._audit_path.mkdir(parents=True, exist_ok=True)
        # This should not raise
        entry = MemoryEntry(key="fail-audit", value="should not crash")
        p.save(entry)
        loaded = p.get("fail-audit")
        assert loaded is not None
        p.close()


class TestRowToEntryEdgeCases:
    """Tests for _row_to_entry edge cases."""

    def test_invalid_tags_json(self, tmp_path: Path) -> None:
        """Invalid tags JSON should default to empty list."""
        p = MemoryPersistence(tmp_path)
        now = datetime.now(tz=UTC).isoformat()
        # Insert with invalid tags JSON directly
        p._conn.execute(
            "INSERT INTO memories "
            "(key, value, tier, confidence, source, source_agent, scope, tags, "
            "created_at, updated_at, last_accessed, access_count, embedding) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "bad-tags",
                "v",
                "pattern",
                0.6,
                "agent",
                "unknown",
                "project",
                "NOT-JSON",
                now,
                now,
                now,
                0,
                None,
            ),
        )
        p._conn.commit()
        loaded = p.get("bad-tags")
        assert loaded is not None
        assert loaded.tags == []
        p.close()

    def test_invalid_embedding_json(self, tmp_path: Path) -> None:
        """Invalid embedding JSON should result in None."""
        p = MemoryPersistence(tmp_path)
        now = datetime.now(tz=UTC).isoformat()
        p._conn.execute(
            "INSERT INTO memories "
            "(key, value, tier, confidence, source, source_agent, scope, tags, "
            "created_at, updated_at, last_accessed, access_count, embedding) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "bad-emb",
                "v",
                "pattern",
                0.6,
                "agent",
                "unknown",
                "project",
                "[]",
                now,
                now,
                now,
                0,
                "NOT-JSON",
            ),
        )
        p._conn.commit()
        loaded = p.get("bad-emb")
        assert loaded is not None
        assert loaded.embedding is None
        p.close()

    def test_non_numeric_embedding_json(self, tmp_path: Path) -> None:
        """Embedding JSON that is not a list of numbers should result in None."""
        p = MemoryPersistence(tmp_path)
        now = datetime.now(tz=UTC).isoformat()
        p._conn.execute(
            "INSERT INTO memories "
            "(key, value, tier, confidence, source, source_agent, scope, tags, "
            "created_at, updated_at, last_accessed, access_count, embedding) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "str-emb",
                "v",
                "pattern",
                0.6,
                "agent",
                "unknown",
                "project",
                "[]",
                now,
                now,
                now,
                0,
                '["a", "b"]',
            ),
        )
        p._conn.commit()
        loaded = p.get("str-emb")
        assert loaded is not None
        assert loaded.embedding is None
        p.close()

    def test_empty_tags_string(self, tmp_path: Path) -> None:
        """Empty tags string should default to empty list."""
        p = MemoryPersistence(tmp_path)
        now = datetime.now(tz=UTC).isoformat()
        p._conn.execute(
            "INSERT INTO memories "
            "(key, value, tier, confidence, source, source_agent, scope, tags, "
            "created_at, updated_at, last_accessed, access_count, embedding) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "empty-tags",
                "v",
                "pattern",
                0.6,
                "agent",
                "unknown",
                "project",
                "",
                now,
                now,
                now,
                0,
                None,
            ),
        )
        p._conn.commit()
        loaded = p.get("empty-tags")
        assert loaded is not None
        assert loaded.tags == []
        p.close()


class TestEscapeFtsQuery:
    """Tests for FTS query escaping."""

    def test_escape_tokens(self) -> None:
        result = MemoryPersistence._escape_fts_query("hello world")
        assert result == '"hello" "world"'

    def test_escape_empty(self) -> None:
        assert MemoryPersistence._escape_fts_query("") == ""
        assert MemoryPersistence._escape_fts_query("   ") == ""


class TestTemporalPersistence:
    """Tests for bi-temporal field persistence (EPIC-004, STORY-004.1)."""

    def test_temporal_fields_round_trip(self, tmp_path: Path) -> None:
        """Temporal fields survive save -> get round-trip."""
        p = MemoryPersistence(tmp_path)
        entry = MemoryEntry(
            key="temporal-test",
            value="test value",
            valid_at="2026-01-01T00:00:00+00:00",
            invalid_at="2026-12-31T23:59:59+00:00",
            superseded_by="temporal-test-v2",
        )
        p.save(entry)
        loaded = p.get("temporal-test")
        assert loaded is not None
        assert loaded.valid_at == "2026-01-01T00:00:00+00:00"
        assert loaded.invalid_at == "2026-12-31T23:59:59+00:00"
        assert loaded.superseded_by == "temporal-test-v2"
        p.close()

    def test_temporal_fields_default_none(self, tmp_path: Path) -> None:
        """Entries without temporal fields have None values."""
        p = MemoryPersistence(tmp_path)
        entry = MemoryEntry(key="no-temporal", value="plain entry")
        p.save(entry)
        loaded = p.get("no-temporal")
        assert loaded is not None
        assert loaded.valid_at is None
        assert loaded.invalid_at is None
        assert loaded.superseded_by is None
        p.close()


class TestTemporalMigration:
    """Tests for migrate_contradicted_to_temporal (EPIC-004, STORY-004.5)."""

    def test_migrate_contradicted_entries(self, tmp_path: Path) -> None:
        """Contradicted entries with 'consolidated into' get temporal fields."""
        p = MemoryPersistence(tmp_path)
        now = datetime.now(tz=UTC).isoformat()

        # Create entries that simulate consolidation
        for i in range(3):
            entry = MemoryEntry(
                key=f"src-{i}",
                value=f"source value {i}",
                contradicted=True,
                contradiction_reason="consolidated into merged-key",
                updated_at=now,
            )
            p.save(entry)

        count = p.migrate_contradicted_to_temporal()
        assert count == 3

        for i in range(3):
            loaded = p.get(f"src-{i}")
            assert loaded is not None
            assert loaded.invalid_at == now
            assert loaded.superseded_by == "merged-key"

        p.close()

    def test_migrate_idempotent(self, tmp_path: Path) -> None:
        """Running migration twice produces the same result."""
        p = MemoryPersistence(tmp_path)
        now = datetime.now(tz=UTC).isoformat()

        entry = MemoryEntry(
            key="idempotent-src",
            value="source value",
            contradicted=True,
            contradiction_reason="consolidated into target-key",
            updated_at=now,
        )
        p.save(entry)

        assert p.migrate_contradicted_to_temporal() == 1
        assert p.migrate_contradicted_to_temporal() == 0  # Already migrated
        p.close()

    def test_migrate_skips_non_consolidated(self, tmp_path: Path) -> None:
        """Entries contradicted for other reasons are left unchanged."""
        p = MemoryPersistence(tmp_path)
        entry = MemoryEntry(
            key="manual-contradiction",
            value="some fact",
            contradicted=True,
            contradiction_reason="manually invalidated by user",
        )
        p.save(entry)

        count = p.migrate_contradicted_to_temporal()
        assert count == 0

        loaded = p.get("manual-contradiction")
        assert loaded is not None
        assert loaded.invalid_at is None
        assert loaded.superseded_by is None
        p.close()
