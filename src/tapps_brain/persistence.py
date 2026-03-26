"""SQLite-backed persistence layer for the shared memory subsystem.

Uses WAL journal mode for concurrent reads during writes, FTS5 for
full-text search, and schema versioning with forward migrations.
A JSONL audit log is maintained for debugging/compliance (append-only).
"""

from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from pathlib import Path

    from tapps_brain.relations import RelationEntry

from tapps_brain.models import MemoryEntry

logger = structlog.get_logger(__name__)

# Current schema version - bump when adding migrations.
_SCHEMA_VERSION = 13

# Previous schema versions for migration checks.
_SCHEMA_V2 = 2
_SCHEMA_V3 = 3
_SCHEMA_V4 = 4
_SCHEMA_V5 = 5
_SCHEMA_V6 = 6
_SCHEMA_V7 = 7
_SCHEMA_V8 = 8
_SCHEMA_V9 = 9
_SCHEMA_V10 = 10
_SCHEMA_V11 = 11
_SCHEMA_V12 = 12
_SCHEMA_V13 = 13

# Maximum JSONL audit log lines before truncation.
_MAX_AUDIT_LINES = 10_000


class MemoryPersistence:
    """SQLite-backed persistence for memory entries.

    Storage directory: ``{project_root}/{store_dir}/memory/``

    Default store_dir is ``.tapps-brain`` for standalone use.
    TappsMCP passes ``.tapps-mcp`` for backward compatibility.

    Files:
    - ``memory.db`` -- SQLite database (WAL mode, FTS5)
    - ``memory_log.jsonl`` -- append-only audit log
    """

    def __init__(self, project_root: Path, *, store_dir: str = ".tapps-brain") -> None:
        self._store_dir = project_root / store_dir / "memory"
        self._store_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self._store_dir / "memory.db"
        self._audit_path = self._store_dir / "memory_log.jsonl"
        self._lock = threading.Lock()
        # Cached after _ensure_schema() — schema never changes after startup.
        self._schema_version: int = 0

        try:
            self._conn = self._connect()
            self._ensure_schema()
        except sqlite3.DatabaseError as exc:
            _msg = f"Database corrupt: {self._db_path}. Back up and delete to recover."
            # Emit via stdlib logging so pytest caplog can capture it in tests
            logging.getLogger(__name__).error("database_corrupt: %s", _msg, exc_info=exc)
            logger.error(
                "database_corrupt",
                path=str(self._db_path),
                message=_msg,
            )
            raise

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    @property
    def store_dir(self) -> Path:
        """Return the on-disk storage directory path."""
        return self._store_dir

    @property
    def db_path(self) -> Path:
        """Return the path to the SQLite database file."""
        return self._db_path

    @property
    def audit_path(self) -> Path:
        """Return the path to the JSONL audit log."""
        return self._audit_path

    # ------------------------------------------------------------------
    # Connection and schema
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        """Open a SQLite connection with recommended pragmas."""
        conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        # NORMAL synchronous is safe with WAL and gives better write throughput.
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_schema(self) -> None:
        """Create tables if absent and apply forward migrations."""
        with self._lock:
            cur = self._conn.cursor()

            # Schema version table
            cur.execute(
                "CREATE TABLE IF NOT EXISTS schema_version "
                "(version INTEGER NOT NULL, migrated_at TEXT NOT NULL)"
            )

            # Check current version
            row = cur.execute("SELECT MAX(version) FROM schema_version").fetchone()
            current_version: int = row[0] if row[0] is not None else 0

            if current_version < 1:
                self._create_v1_schema(cur)

            if current_version < _SCHEMA_V2:
                self._migrate_v1_to_v2(cur)
            if current_version < _SCHEMA_V3:
                self._migrate_v2_to_v3(cur)
            if current_version < _SCHEMA_V4:
                self._migrate_v3_to_v4(cur)
            if current_version < _SCHEMA_V5:
                self._migrate_v4_to_v5(cur)
            if current_version < _SCHEMA_V6:
                self._migrate_v5_to_v6(cur)
            if current_version < _SCHEMA_V7:
                self._migrate_v6_to_v7(cur)
            if current_version < _SCHEMA_V8:
                self._migrate_v7_to_v8(cur)
            if current_version < _SCHEMA_V9:
                self._migrate_v8_to_v9(cur)
            if current_version < _SCHEMA_V10:
                self._migrate_v9_to_v10(cur)
            if current_version < _SCHEMA_V11:
                self._migrate_v10_to_v11(cur)
            if current_version < _SCHEMA_V12:
                self._migrate_v11_to_v12(cur)
            if current_version < _SCHEMA_V13:
                self._migrate_v12_to_v13(cur)

            self._conn.commit()

            # Cache the final schema version so save() avoids repeated DB lookups.
            row2 = cur.execute("SELECT MAX(version) FROM schema_version").fetchone()
            self._schema_version = int(row2[0]) if row2[0] is not None else 0

    def _create_v1_schema(self, cur: sqlite3.Cursor) -> None:
        """Create the initial v1 schema."""
        # Main memories table
        cur.execute("""
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

        # Indexes for common queries
        cur.execute("CREATE INDEX IF NOT EXISTS idx_memories_tier ON memories(tier)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(scope)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_memories_confidence ON memories(confidence)")

        # FTS5 full-text search index
        cur.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
            USING fts5(key, value, tags, content=memories, content_rowid=rowid)
        """)

        # Triggers to keep FTS in sync
        cur.execute("""
            CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                INSERT INTO memories_fts(rowid, key, value, tags)
                VALUES (new.rowid, new.key, new.value, new.tags);
            END
        """)
        cur.execute("""
            CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, key, value, tags)
                VALUES ('delete', old.rowid, old.key, old.value, old.tags);
            END
        """)
        cur.execute("""
            CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, key, value, tags)
                VALUES ('delete', old.rowid, old.key, old.value, old.tags);
                INSERT INTO memories_fts(rowid, key, value, tags)
                VALUES (new.rowid, new.key, new.value, new.tags);
            END
        """)

        # Reserved for Epic 24 GC
        cur.execute("""
            CREATE TABLE IF NOT EXISTS archived_memories (
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                tier TEXT NOT NULL,
                confidence REAL NOT NULL,
                source TEXT NOT NULL,
                source_agent TEXT NOT NULL,
                scope TEXT NOT NULL,
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
                archived_at TEXT NOT NULL
            )
        """)

        # Record schema version
        cur.execute(
            "INSERT INTO schema_version (version, migrated_at) VALUES (?, ?)",
            (1, datetime.now(tz=UTC).isoformat()),
        )

    def _migrate_v1_to_v2(self, cur: sqlite3.Cursor) -> None:
        """Add optional embedding column for semantic search (Epic 65.7).

        Stores JSON array of floats. Existing rows get NULL.
        """
        try:
            cur.execute("ALTER TABLE memories ADD COLUMN embedding TEXT")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                pass  # Column already exists
            else:
                raise

        cur.execute(
            "INSERT INTO schema_version (version, migrated_at) VALUES (?, ?)",
            (2, datetime.now(tz=UTC).isoformat()),
        )

    def _migrate_v2_to_v3(self, cur: sqlite3.Cursor) -> None:
        """Add session_index table for searchable session chunks (Epic 65.10)."""
        cur.execute("""
            CREATE TABLE IF NOT EXISTS session_index (
                session_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (session_id, chunk_index)
            )
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_index_session ON session_index(session_id)"
        )
        cur.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS session_index_fts
            USING fts5(session_id, content, content=session_index, content_rowid=rowid)
        """)
        cur.execute("""
            CREATE TRIGGER IF NOT EXISTS session_index_ai AFTER INSERT ON session_index BEGIN
                INSERT INTO session_index_fts(rowid, session_id, content)
                VALUES (new.rowid, new.session_id, new.content);
            END
        """)
        cur.execute("""
            CREATE TRIGGER IF NOT EXISTS session_index_ad AFTER DELETE ON session_index BEGIN
                INSERT INTO session_index_fts(session_index_fts, rowid, session_id, content)
                VALUES ('delete', old.rowid, old.session_id, old.content);
            END
        """)
        cur.execute("""
            CREATE TRIGGER IF NOT EXISTS session_index_au AFTER UPDATE ON session_index BEGIN
                INSERT INTO session_index_fts(session_index_fts, rowid, session_id, content)
                VALUES ('delete', old.rowid, old.session_id, old.content);
                INSERT INTO session_index_fts(rowid, session_id, content)
                VALUES (new.rowid, new.session_id, new.content);
            END
        """)
        cur.execute(
            "INSERT INTO schema_version (version, migrated_at) VALUES (?, ?)",
            (3, datetime.now(tz=UTC).isoformat()),
        )

    def _migrate_v3_to_v4(self, cur: sqlite3.Cursor) -> None:
        """Add relations table for entity/relationship extraction (Epic 65.12)."""
        cur.execute("""
            CREATE TABLE IF NOT EXISTS relations (
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object_entity TEXT NOT NULL,
                source_entry_keys TEXT NOT NULL DEFAULT '[]',
                confidence REAL NOT NULL DEFAULT 0.8,
                created_at TEXT NOT NULL,
                PRIMARY KEY (subject, predicate, object_entity)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_relations_subject ON relations(subject)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_relations_object ON relations(object_entity)")
        cur.execute(
            "INSERT INTO schema_version (version, migrated_at) VALUES (?, ?)",
            (4, datetime.now(tz=UTC).isoformat()),
        )

    def _migrate_v4_to_v5(self, cur: sqlite3.Cursor) -> None:
        """Add bi-temporal columns for validity windows (EPIC-004).

        Adds ``valid_at``, ``invalid_at``, ``superseded_by`` to memories
        and creates an index for temporal queries.
        """
        for col in ("valid_at", "invalid_at", "superseded_by"):
            try:
                cur.execute(f"ALTER TABLE memories ADD COLUMN {col} TEXT")
            except sqlite3.OperationalError as e:
                if "duplicate column name" not in str(e).lower():
                    raise

        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_temporal ON memories(valid_at, invalid_at)"
        )

        cur.execute(
            "INSERT INTO schema_version (version, migrated_at) VALUES (?, ?)",
            (5, datetime.now(tz=UTC).isoformat()),
        )

    def _migrate_v5_to_v6(self, cur: sqlite3.Cursor) -> None:
        """Schema v6 — observability hooks / version bump (EPIC-007).

        No SQLite shape changes; bumps version so tooling can detect v6 stores.
        """
        cur.execute(
            "INSERT INTO schema_version (version, migrated_at) VALUES (?, ?)",
            (6, datetime.now(tz=UTC).isoformat()),
        )

    def _migrate_v6_to_v7(self, cur: sqlite3.Cursor) -> None:
        """Add agent_scope column for Hive propagation (EPIC-011).

        Adds ``agent_scope TEXT DEFAULT 'private'`` to memories table.
        Existing entries default to ``'private'`` (no Hive propagation).
        """
        try:
            cur.execute("ALTER TABLE memories ADD COLUMN agent_scope TEXT DEFAULT 'private'")
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise

        cur.execute(
            "INSERT INTO schema_version (version, migrated_at) VALUES (?, ?)",
            (7, datetime.now(tz=UTC).isoformat()),
        )

    def _migrate_v7_to_v8(self, cur: sqlite3.Cursor) -> None:
        """Add integrity_hash column for tamper detection (H4a).

        Stores HMAC-SHA256 hex digest computed over key|value|tier|source.
        Existing rows get NULL (hash computed on next save/update).
        """
        try:
            cur.execute("ALTER TABLE memories ADD COLUMN integrity_hash TEXT")
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise

        cur.execute(
            "INSERT INTO schema_version (version, migrated_at) VALUES (?, ?)",
            (8, datetime.now(tz=UTC).isoformat()),
        )

    def _migrate_v8_to_v9(self, cur: sqlite3.Cursor) -> None:
        """Add feedback_events table for EPIC-029 Feedback Collection.

        Creates ``feedback_events`` with columns for event_type, entry_key,
        session_id, utility_score, details (JSON), and timestamp.
        Indexes on event_type, timestamp, entry_key, and session_id for
        efficient query filtering.
        """
        cur.execute("""
            CREATE TABLE IF NOT EXISTS feedback_events (
                id            TEXT NOT NULL PRIMARY KEY,
                event_type    TEXT NOT NULL,
                entry_key     TEXT,
                session_id    TEXT,
                utility_score REAL,
                details       TEXT NOT NULL DEFAULT '{}',
                timestamp     TEXT NOT NULL
            )
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_feedback_event_type ON feedback_events(event_type)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_feedback_timestamp ON feedback_events(timestamp)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_feedback_entry_key ON feedback_events(entry_key)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_feedback_session_id ON feedback_events(session_id)"
        )
        cur.execute(
            "INSERT INTO schema_version (version, migrated_at) VALUES (?, ?)",
            (9, datetime.now(tz=UTC).isoformat()),
        )

    def _migrate_v9_to_v10(self, cur: sqlite3.Cursor) -> None:
        """Add diagnostics_history for EPIC-030 quality scorecard."""
        cur.execute("""
            CREATE TABLE IF NOT EXISTS diagnostics_history (
                id                TEXT NOT NULL PRIMARY KEY,
                recorded_at       TEXT NOT NULL,
                composite_score   REAL NOT NULL,
                dimension_scores  TEXT NOT NULL,
                circuit_state     TEXT NOT NULL DEFAULT 'closed',
                full_report_json  TEXT NOT NULL DEFAULT '{}'
            )
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_diag_hist_recorded ON diagnostics_history(recorded_at)"
        )
        cur.execute(
            "INSERT INTO schema_version (version, migrated_at) VALUES (?, ?)",
            (10, datetime.now(tz=UTC).isoformat()),
        )

    def _migrate_v10_to_v11(self, cur: sqlite3.Cursor) -> None:
        """EPIC-031 flywheel: feedback counts on memories + flywheel_meta KV."""
        cur.execute("""
            CREATE TABLE IF NOT EXISTS flywheel_meta (
                key   TEXT NOT NULL PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        cur.execute(
            "ALTER TABLE memories ADD COLUMN positive_feedback_count REAL NOT NULL DEFAULT 0"
        )
        cur.execute(
            "ALTER TABLE memories ADD COLUMN negative_feedback_count REAL NOT NULL DEFAULT 0"
        )
        cur.execute(
            "INSERT INTO schema_version (version, migrated_at) VALUES (?, ?)",
            (11, datetime.now(tz=UTC).isoformat()),
        )

    def _migrate_v11_to_v12(self, cur: sqlite3.Cursor) -> None:
        """Add provenance metadata columns to memories (GitHub #38).

        Tracks WHERE each memory came from: session, channel, message, trigger.
        Existing rows default to empty string (no provenance).
        """
        for col in (
            "source_session_id",
            "source_channel",
            "source_message_id",
            "triggered_by",
        ):
            try:
                cur.execute(
                    f"ALTER TABLE memories ADD COLUMN {col} TEXT NOT NULL DEFAULT ''"
                )
            except sqlite3.OperationalError as e:
                if "duplicate column name" not in str(e).lower():
                    raise

        cur.execute(
            "INSERT INTO schema_version (version, migrated_at) VALUES (?, ?)",
            (12, datetime.now(tz=UTC).isoformat()),
        )

    def _migrate_v12_to_v13(self, cur: sqlite3.Cursor) -> None:
        """Add valid_from and valid_until columns for temporal fact validity (GitHub #29, task 040.3).

        These are human-friendly aliases for the existing valid_at/invalid_at bi-temporal fields.
        They represent the validity window of a fact as reported by the user or source.
        """
        for col in ("valid_from", "valid_until"):
            try:
                cur.execute(
                    f"ALTER TABLE memories ADD COLUMN {col} TEXT NOT NULL DEFAULT ''"
                )
            except sqlite3.OperationalError as e:
                if "duplicate column name" not in str(e).lower():
                    raise

        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_valid_window "
            "ON memories(valid_from, valid_until)"
        )
        cur.execute(
            "INSERT INTO schema_version (version, migrated_at) VALUES (?, ?)",
            (13, datetime.now(tz=UTC).isoformat()),
        )

    def migrate_contradicted_to_temporal(self) -> int:
        """Migrate ``contradicted`` entries with "consolidated into" to temporal fields.

        For each entry where ``contradicted=1`` and ``contradiction_reason``
        matches ``consolidated into {key}``, sets ``invalid_at = updated_at``
        and ``superseded_by = {key}``.

        Returns:
            Number of entries migrated.
        """
        import re as _re

        pattern = _re.compile(r"consolidated into (\S+)", _re.IGNORECASE)
        count = 0

        with self._lock:
            rows = self._conn.execute(
                "SELECT key, updated_at, contradiction_reason, invalid_at "
                "FROM memories WHERE contradicted = 1 AND contradiction_reason IS NOT NULL"
            ).fetchall()

            for row in rows:
                # Skip if already migrated
                if row["invalid_at"] is not None:
                    continue
                reason = row["contradiction_reason"] or ""
                match = pattern.search(reason)
                if not match:
                    continue
                target_key = match.group(1)
                self._conn.execute(
                    "UPDATE memories SET invalid_at = ?, superseded_by = ? WHERE key = ?",
                    (row["updated_at"], target_key, row["key"]),
                )
                count += 1

            if count > 0:
                self._conn.commit()

        return count

    # ------------------------------------------------------------------
    # CRUD operations
    # ------------------------------------------------------------------

    def save(self, entry: MemoryEntry) -> None:
        """Insert or replace a memory entry."""
        tags_json = json.dumps(entry.tags, ensure_ascii=False)
        embedding_json: str | None = None
        if entry.embedding is not None:
            embedding_json = json.dumps(entry.embedding, ensure_ascii=False)

        columns = [
            "key",
            "value",
            "tier",
            "confidence",
            "source",
            "source_agent",
            "scope",
            "tags",
            "created_at",
            "updated_at",
            "last_accessed",
            "access_count",
            "branch",
            "last_reinforced",
            "reinforce_count",
            "contradicted",
            "contradiction_reason",
            "seeded_from",
        ]
        values: tuple[Any, ...] = (
            entry.key,
            entry.value,
            entry.tier.value if hasattr(entry.tier, "value") else str(entry.tier),
            entry.confidence,
            entry.source.value,
            entry.source_agent,
            entry.scope.value,
            tags_json,
            entry.created_at,
            entry.updated_at,
            entry.last_accessed,
            entry.access_count,
            entry.branch,
            entry.last_reinforced,
            entry.reinforce_count,
            1 if entry.contradicted else 0,
            entry.contradiction_reason,
            entry.seeded_from,
        )

        # Include embedding if schema supports it (v2+, column added in _migrate_v1_to_v2)
        schema_ver = self.get_schema_version()
        if schema_ver >= _SCHEMA_V2:
            columns.append("embedding")
            values = (*values, embedding_json)

        # Include temporal fields if schema supports it (v5+)
        if schema_ver >= _SCHEMA_V5:
            columns.extend(["valid_at", "invalid_at", "superseded_by"])
            values = (*values, entry.valid_at, entry.invalid_at, entry.superseded_by)

        # Include agent_scope if schema supports it (v7+)
        if schema_ver >= _SCHEMA_V7:
            columns.append("agent_scope")
            values = (*values, entry.agent_scope)

        # Compute and include integrity hash if schema supports it (v8+)
        if schema_ver >= _SCHEMA_V8:
            from tapps_brain.integrity import compute_integrity_hash

            tier_str = entry.tier.value if hasattr(entry.tier, "value") else str(entry.tier)
            source_str = entry.source.value if hasattr(entry.source, "value") else str(entry.source)
            integrity_hash = compute_integrity_hash(entry.key, entry.value, tier_str, source_str)
            columns.append("integrity_hash")
            values = (*values, integrity_hash)

        # Flywheel feedback tallies (v11+)
        if schema_ver >= _SCHEMA_V11:
            columns.extend(["positive_feedback_count", "negative_feedback_count"])
            values = (*values, entry.positive_feedback_count, entry.negative_feedback_count)

        # Provenance metadata (v12+, GitHub #38)
        if schema_ver >= _SCHEMA_V12:
            columns.extend(
                ["source_session_id", "source_channel", "source_message_id", "triggered_by"]
            )
            values = (
                *values,
                entry.source_session_id,
                entry.source_channel,
                entry.source_message_id,
                entry.triggered_by,
            )

        # Temporal validity window (v13+, GitHub #29)
        if schema_ver >= _SCHEMA_V13:
            columns.extend(["valid_from", "valid_until"])
            values = (*values, entry.valid_from, entry.valid_until)

        placeholders = ", ".join("?" * len(columns))

        cols = ", ".join(columns)
        with self._lock:
            self._conn.execute(
                f"INSERT OR REPLACE INTO memories ({cols}) VALUES ({placeholders})",
                values,
            )
            self._conn.commit()
        self._audit_log("save", entry.key)

    def get(self, key: str) -> MemoryEntry | None:
        """Retrieve a single memory entry by key."""
        with self._lock:
            row = self._conn.execute("SELECT * FROM memories WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        return self._row_to_entry(row)

    def list_all(
        self,
        tier: str | None = None,
        scope: str | None = None,
        tags: list[str] | None = None,
    ) -> list[MemoryEntry]:
        """List entries with optional filters."""
        query = "SELECT * FROM memories WHERE 1=1"
        params: list[Any] = []

        if tier is not None:
            query += " AND tier = ?"
            params.append(tier)
        if scope is not None:
            query += " AND scope = ?"
            params.append(scope)

        with self._lock:
            rows = self._conn.execute(query, params).fetchall()

        entries = [self._row_to_entry(r) for r in rows]

        # Filter by tags in Python (tags stored as JSON array)
        if tags:
            tag_set = set(tags)
            entries = [e for e in entries if tag_set.intersection(e.tags)]

        return entries

    def delete(self, key: str) -> bool:
        """Delete a memory entry by key. Returns True if deleted."""
        with self._lock:
            cur = self._conn.execute("DELETE FROM memories WHERE key = ?", (key,))
            self._conn.commit()
        deleted = cur.rowcount > 0
        if deleted:
            self._audit_log("delete", key)
        return deleted

    def search(self, query: str) -> list[MemoryEntry]:
        """Full-text search via FTS5 across key, value, and tags."""
        if not query.strip():
            return []

        # Escape FTS5 special characters for safety
        safe_query = self._escape_fts_query(query)
        if not safe_query:
            return []

        with self._lock:
            try:
                rows = self._conn.execute(
                    """
                    SELECT m.* FROM memories m
                    JOIN memories_fts fts ON m.rowid = fts.rowid
                    WHERE memories_fts MATCH ?
                    """,
                    (safe_query,),
                ).fetchall()
            except sqlite3.OperationalError:
                logger.debug("fts_search_failed", query=query)
                return []

        return [self._row_to_entry(r) for r in rows]

    def load_all(self) -> list[MemoryEntry]:
        """Load all entries (for cold-start into in-memory cache)."""
        with self._lock:
            rows = self._conn.execute("SELECT * FROM memories").fetchall()
        return [self._row_to_entry(r) for r in rows]

    def count(self) -> int:
        """Return the total number of memory entries."""
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM memories").fetchone()
        return int(row[0]) if row else 0

    def get_schema_version(self) -> int:
        """Return the current schema version (cached after startup)."""
        return self._schema_version

    def flywheel_meta_get(self, key: str) -> str | None:
        """Read a flywheel_meta value (EPIC-031); None if missing or pre-v11."""
        if self._schema_version < _SCHEMA_V11:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM flywheel_meta WHERE key = ?",
                (key,),
            ).fetchone()
        return str(row[0]) if row and row[0] is not None else None

    def flywheel_meta_set(self, key: str, value: str) -> None:
        """Upsert a flywheel_meta key (EPIC-031)."""
        if self._schema_version < _SCHEMA_V11:
            return
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO flywheel_meta (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
            self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # Session index (Epic 65.10)
    # ------------------------------------------------------------------

    def save_session_chunks(
        self,
        session_id: str,
        chunks: list[str],
        *,
        max_chunks: int = 50,
        max_chars_per_chunk: int = 500,
    ) -> int:
        """Store session chunks for indexing. Returns count stored."""
        if not chunks or not session_id.strip():
            return 0
        now = datetime.now(tz=UTC).isoformat()
        stored = 0
        with self._lock:
            for i, raw in enumerate(chunks[:max_chunks]):
                content = (raw or "")[:max_chars_per_chunk].strip()
                if not content:
                    continue
                try:
                    self._conn.execute(
                        """
                        INSERT OR REPLACE INTO session_index
                        (session_id, chunk_index, content, created_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (session_id, i, content, now),
                    )
                    stored += 1
                except sqlite3.IntegrityError:
                    logger.debug(
                        "session_chunk_integrity_error",
                        session_id=session_id,
                        chunk_index=i,
                    )
            self._conn.commit()
        return stored

    def search_session_index(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Full-text search session index.

        Returns list of {session_id, chunk_index, content, created_at}.
        """
        if not query or not query.strip():
            return []
        safe = self._escape_fts_query(query)
        if not safe:
            return []
        with self._lock:
            try:
                rows = self._conn.execute(
                    """
                    SELECT s.session_id, s.chunk_index, s.content, s.created_at
                    FROM session_index s
                    JOIN session_index_fts fts ON s.rowid = fts.rowid
                    WHERE session_index_fts MATCH ?
                    ORDER BY s.created_at DESC
                    LIMIT ?
                    """,
                    (safe, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                logger.debug("session_index_fts_search_failed", query=query)
                return []
        return [
            {
                "session_id": r["session_id"],
                "chunk_index": r["chunk_index"],
                "content": r["content"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def delete_expired_session_chunks(self, ttl_days: int) -> int:
        """Delete session chunks older than ttl_days. Returns count deleted."""
        from datetime import timedelta

        cutoff = (datetime.now(tz=UTC) - timedelta(days=ttl_days)).isoformat()
        with self._lock:
            cur = self._conn.execute("DELETE FROM session_index WHERE created_at < ?", (cutoff,))
            self._conn.commit()
            return cur.rowcount

    def count_session_chunks(self) -> int:
        """Return total session index chunk count."""
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM session_index").fetchone()
        return int(row[0]) if row else 0

    # ------------------------------------------------------------------
    # Relations (Epic 65.12)
    # ------------------------------------------------------------------

    def save_relation(
        self,
        subject: str,
        predicate: str,
        object_entity: str,
        source_entry_keys: list[str],
        confidence: float = 0.8,
    ) -> None:
        """Insert or replace a relation triple."""
        keys_json = json.dumps(source_entry_keys, ensure_ascii=False)
        now = datetime.now(tz=UTC).isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO relations "
                "(subject, predicate, object_entity, source_entry_keys, confidence, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (subject, predicate, object_entity, keys_json, confidence, now),
            )
            self._conn.commit()

    def list_relations(self) -> list[dict[str, Any]]:
        """Return all stored relations as dicts."""
        with self._lock:
            rows = self._conn.execute("SELECT * FROM relations").fetchall()
        results: list[dict[str, Any]] = []
        for r in rows:
            try:
                keys = json.loads(r["source_entry_keys"])
            except (json.JSONDecodeError, TypeError):
                keys = []
            results.append(
                {
                    "subject": r["subject"],
                    "predicate": r["predicate"],
                    "object_entity": r["object_entity"],
                    "source_entry_keys": keys,
                    "confidence": r["confidence"],
                    "created_at": r["created_at"],
                }
            )
        return results

    def count_relations(self) -> int:
        """Return the total number of stored relations."""
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM relations").fetchone()
        return int(row[0]) if row else 0

    def save_relations(self, key: str, relations: list[RelationEntry]) -> int:
        """Batch-save relations linked to a memory entry key.

        Each relation's ``source_entry_keys`` is ensured to contain *key*.
        Existing triples are updated (INSERT OR REPLACE).

        Returns:
            Number of relations saved.
        """
        if not relations:
            return 0
        now = datetime.now(tz=UTC).isoformat()
        count = 0
        with self._lock:
            for rel in relations:
                source_keys = list(dict.fromkeys([*rel.source_entry_keys, key]))
                keys_json = json.dumps(source_keys, ensure_ascii=False)
                self._conn.execute(
                    "INSERT OR REPLACE INTO relations "
                    "(subject, predicate, object_entity, source_entry_keys, "
                    "confidence, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        rel.subject,
                        rel.predicate,
                        rel.object_entity,
                        keys_json,
                        rel.confidence,
                        now,
                    ),
                )
                count += 1
            self._conn.commit()
        return count

    def load_relations(self, key: str) -> list[dict[str, Any]]:
        """Load all relations whose ``source_entry_keys`` contains *key*."""
        all_relations = self.list_relations()
        return [r for r in all_relations if key in r["source_entry_keys"]]

    def delete_relations(self, key: str) -> int:
        """Delete all relations whose ``source_entry_keys`` contains *key*.

        Returns:
            Number of relations deleted.
        """
        with self._lock:
            rows = self._conn.execute("SELECT * FROM relations").fetchall()
            count = 0
            for r in rows:
                try:
                    keys = json.loads(r["source_entry_keys"])
                except (json.JSONDecodeError, TypeError):
                    keys = []
                if key in keys:
                    self._conn.execute(
                        "DELETE FROM relations "
                        "WHERE subject = ? AND predicate = ? AND object_entity = ?",
                        (r["subject"], r["predicate"], r["object_entity"]),
                    )
                    count += 1
            if count > 0:
                self._conn.commit()
        return count

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> MemoryEntry:
        """Convert a SQLite Row to a MemoryEntry."""
        tags_raw = row["tags"]
        try:
            tags = json.loads(tags_raw) if tags_raw else []
        except (json.JSONDecodeError, TypeError):
            tags = []

        embedding: list[float] | None = None
        try:
            emb_raw = row["embedding"]
        except (KeyError, IndexError):
            emb_raw = None
        if emb_raw:
            try:
                parsed = json.loads(str(emb_raw))
                if isinstance(parsed, list) and all(isinstance(x, (int, float)) for x in parsed):
                    embedding = [float(x) for x in parsed]
            except (json.JSONDecodeError, TypeError):
                pass

        # Read temporal fields (v5+), gracefully handle missing columns
        valid_at: str | None = None
        invalid_at: str | None = None
        superseded_by: str | None = None
        try:
            valid_at = row["valid_at"]
            invalid_at = row["invalid_at"]
            superseded_by = row["superseded_by"]
        except (KeyError, IndexError):
            pass

        # Read agent_scope (v7+), gracefully handle missing column
        agent_scope: str = "private"
        try:
            val = row["agent_scope"]
            if val is not None:
                agent_scope = val
        except (KeyError, IndexError):
            pass

        # Read integrity_hash (v8+), gracefully handle missing column
        integrity_hash: str | None = None
        with contextlib.suppress(KeyError, IndexError):
            integrity_hash = row["integrity_hash"]

        pos_fb = 0.0
        neg_fb = 0.0
        with contextlib.suppress(KeyError, IndexError):
            pos_fb = float(row["positive_feedback_count"] or 0)
            neg_fb = float(row["negative_feedback_count"] or 0)

        # Read provenance metadata (v12+), gracefully handle missing columns
        source_session_id: str = ""
        source_channel: str = ""
        source_message_id: str = ""
        triggered_by: str = ""
        with contextlib.suppress(KeyError, IndexError):
            source_session_id = row["source_session_id"] or ""
            source_channel = row["source_channel"] or ""
            source_message_id = row["source_message_id"] or ""
            triggered_by = row["triggered_by"] or ""

        # Read temporal validity window (v13+, GitHub #29), gracefully handle missing columns
        valid_from: str = ""
        valid_until: str = ""
        with contextlib.suppress(KeyError, IndexError):
            valid_from = row["valid_from"] or ""
            valid_until = row["valid_until"] or ""

        return MemoryEntry(
            key=row["key"],
            value=row["value"],
            tier=row["tier"],
            confidence=row["confidence"],
            source=row["source"],
            source_agent=row["source_agent"],
            scope=row["scope"],
            tags=tags,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_accessed=row["last_accessed"],
            access_count=row["access_count"],
            branch=row["branch"],
            last_reinforced=row["last_reinforced"],
            reinforce_count=row["reinforce_count"],
            contradicted=bool(row["contradicted"]),
            contradiction_reason=row["contradiction_reason"],
            seeded_from=row["seeded_from"],
            embedding=embedding,
            valid_at=valid_at,
            invalid_at=invalid_at,
            superseded_by=superseded_by,
            agent_scope=agent_scope,
            integrity_hash=integrity_hash,
            positive_feedback_count=pos_fb,
            negative_feedback_count=neg_fb,
            source_session_id=source_session_id,
            source_channel=source_channel,
            source_message_id=source_message_id,
            triggered_by=triggered_by,
            valid_from=valid_from,
            valid_until=valid_until,
        )

    @staticmethod
    def _escape_fts_query(query: str) -> str:
        """Escape an FTS5 query string for safe matching.

        Wraps each token in double quotes to treat them as literals.
        Inner double-quote characters are escaped by doubling them (FTS5 syntax).
        """
        tokens = query.strip().split()
        if not tokens:
            return ""
        return " ".join(f'"{t.replace(chr(34), chr(34) + chr(34))}"' for t in tokens)

    def append_audit(
        self,
        action: str,
        key: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Append an entry to the JSONL audit log with optional extra fields (EPIC-010)."""
        record: dict[str, Any] = {
            "action": action,
            "key": key,
            "timestamp": datetime.now(tz=UTC).isoformat(),
        }
        if extra:
            record.update(extra)
        try:
            with self._audit_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._maybe_truncate_audit()
        except OSError:
            logger.debug("audit_log_write_failed", key=key, action=action)

    def _audit_log(self, action: str, key: str) -> None:
        """Append a simple entry to the JSONL audit log (delegates to append_audit)."""
        self.append_audit(action, key)

    def _maybe_truncate_audit(self) -> None:
        """Truncate audit log if it exceeds the max line count."""
        try:
            lines = self._audit_path.read_text(encoding="utf-8").splitlines()
            if len(lines) > _MAX_AUDIT_LINES:
                # Keep the most recent entries
                keep = lines[-_MAX_AUDIT_LINES:]
                self._audit_path.write_text("\n".join(keep) + "\n", encoding="utf-8")
        except OSError:
            pass
