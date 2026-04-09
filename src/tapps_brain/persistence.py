"""SQLite-backed persistence layer for the shared memory subsystem.

Uses WAL journal mode for concurrent reads during writes, FTS5 for
full-text search, and schema versioning.
A JSONL audit log is maintained for debugging/compliance (append-only).

When ``TAPPS_SQLITE_MEMORY_READONLY_SEARCH`` is enabled, FTS ``search()`` and
sqlite-vec ``sqlite_vec_knn_search()`` use a second read-only connection under
``_read_lock`` so they do not serialize behind the primary writer lock
(EPIC-050 STORY-050.3).
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

from tapps_brain.lexical import LexicalRetrievalConfig, build_fts_match_query
from tapps_brain.models import MemoryEntry
from tapps_brain.sqlcipher_util import (
    connect_sqlite,
    connect_sqlite_readonly,
    resolve_memory_encryption_key,
    resolve_memory_readonly_search_enabled,
)

logger = structlog.get_logger(__name__)

# Production schema version — first release starts at 1.
_SCHEMA_VERSION = 1

# Maximum JSONL audit log lines before truncation.
_MAX_AUDIT_LINES = 10_000


class MemoryPersistence:
    """SQLite-backed persistence for memory entries.

    Storage directory: ``{project_root}/{store_dir}/memory/``

    Default store_dir is ``.tapps-brain`` for standalone use.
    TappsMCP passes ``.tapps-mcp`` for backward compatibility.

    Files:
    - ``memory.db`` -- SQLite database (WAL mode, FTS5); optional SQLCipher when
      ``encryption_key`` or ``TAPPS_BRAIN_ENCRYPTION_KEY`` is set
      (see ``docs/guides/sqlcipher.md``).
    - ``memory_log.jsonl`` -- append-only audit log
    """

    def __init__(
        self,
        project_root: Path,
        *,
        store_dir: str = ".tapps-brain",
        agent_id: str | None = None,
        encryption_key: str | None = None,
        lexical_config: LexicalRetrievalConfig | None = None,
    ) -> None:
        self._agent_id = agent_id
        if agent_id is not None:
            self._store_dir = project_root / store_dir / "agents" / agent_id
        else:
            self._store_dir = project_root / store_dir / "memory"
        self._store_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self._store_dir / "memory.db"
        self._audit_path = self._store_dir / "memory_log.jsonl"
        self._encryption_key = resolve_memory_encryption_key(encryption_key)
        self._lexical = lexical_config or LexicalRetrievalConfig()
        self._lock = threading.Lock()
        self._read_lock = threading.Lock()
        self._read_conn: sqlite3.Connection | None = None
        self._readonly_search_enabled = resolve_memory_readonly_search_enabled()
        self._readonly_conn_failed = False
        # Cached after _ensure_schema() — schema never changes after startup.
        self._schema_version: int = 0
        # Optional sqlite-vec ANN index (GitHub #30); see _setup_sqlite_vec().
        self._sqlite_vec_enabled = False
        self._sqlite_vec_dim = 384

        try:
            self._conn = self._connect()
            self._ensure_schema()
            self._setup_sqlite_vec()
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
    def agent_id(self) -> str | None:
        """Return the agent identity used for storage isolation, or ``None``."""
        return self._agent_id

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

    @property
    def encryption_key(self) -> str | None:
        """Passphrase used for SQLCipher, or ``None`` when the DB is plain SQLite."""
        return self._encryption_key

    @property
    def sqlcipher_enabled(self) -> bool:
        """True when opening ``memory.db`` with SQLCipher (key from arg or env)."""
        return self._encryption_key is not None

    # ------------------------------------------------------------------
    # Connection and schema
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        """Open a SQLite or SQLCipher connection with recommended pragmas."""
        return connect_sqlite(
            self._db_path,
            encryption_key=self._encryption_key,
            check_same_thread=False,
        )

    def _get_read_connection(self) -> sqlite3.Connection | None:
        """Lazily open a read-only handle for search/KNN when env enables it (EPIC-050.3)."""
        if not self._readonly_search_enabled or self._readonly_conn_failed:
            return None
        with self._read_lock:
            if self._read_conn is None:
                try:
                    self._read_conn = connect_sqlite_readonly(
                        self._db_path,
                        encryption_key=self._encryption_key,
                        check_same_thread=False,
                    )
                    if self._sqlite_vec_enabled:
                        from tapps_brain.sqlite_vec_index import load_extension

                        load_extension(self._read_conn)
                except (OSError, sqlite3.Error):
                    logger.debug("memory_readonly_conn_failed", exc_info=True)
                    self._readonly_conn_failed = True
                    self._read_conn = None
                    return None
            return self._read_conn

    def _ensure_schema(self) -> None:
        """Create tables if absent (single production schema, no migrations)."""
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
                self._create_schema(cur)
                logger.debug("schema_created", version=_SCHEMA_VERSION)

            self._conn.commit()

            # Cache the final schema version so save() avoids repeated DB lookups.
            row2 = cur.execute("SELECT MAX(version) FROM schema_version").fetchone()
            self._schema_version = int(row2[0]) if row2[0] is not None else 0

    def _setup_sqlite_vec(self) -> None:
        """Load sqlite-vec extension, create ``memory_vec``, backfill if empty."""
        from tapps_brain.sqlite_vec_index import (
            DEFAULT_VEC_DIM,
            ensure_memory_vec_table,
            load_extension,
            maybe_backfill_if_empty,
        )

        self._sqlite_vec_dim = DEFAULT_VEC_DIM
        load_extension(self._conn)
        with self._lock:
            ensure_memory_vec_table(self._conn, dim=self._sqlite_vec_dim)
            rows = self._conn.execute(
                "SELECT * FROM memories WHERE embedding IS NOT NULL AND TRIM(embedding) != ''"
            ).fetchall()
            entries = [self._row_to_entry(r) for r in rows]
            maybe_backfill_if_empty(self._conn, entries)
            self._conn.commit()
        self._sqlite_vec_enabled = True

    def _sqlite_vec_sync_unlocked(self, entry: MemoryEntry) -> None:
        """Keep ``memory_vec`` in sync with ``memories.embedding`` (caller holds lock).

        One upsert or delete per save; each upsert is delete+insert on the vec table
        (incremental cost notes: ``docs/guides/sqlite-vec-operators.md``).
        """
        if not self._sqlite_vec_enabled:
            return
        from tapps_brain.sqlite_vec_index import delete_vec_key, upsert_vec_row

        emb = entry.embedding
        if emb is not None and len(emb) == self._sqlite_vec_dim:
            upsert_vec_row(self._conn, entry.key, emb)
        else:
            delete_vec_key(self._conn, entry.key)

    def sqlite_vec_knn_search(
        self, query_embedding: list[float], k: int
    ) -> list[tuple[str, float]]:
        """Return ``(key, distance)`` from sqlite-vec KNN, or empty if disabled.

        ``distance`` is vec0 **L2** distance (see ``sqlite_vec_index.knn_search``).
        """
        if not self._sqlite_vec_enabled:
            return []
        from tapps_brain.sqlite_vec_index import knn_search

        read_conn = self._get_read_connection()
        if read_conn is not None:
            with self._read_lock:
                return knn_search(
                    read_conn,
                    query_embedding,
                    k,
                    dim=self._sqlite_vec_dim,
                )
        with self._lock:
            return knn_search(
                self._conn,
                query_embedding,
                k,
                dim=self._sqlite_vec_dim,
            )

    def sqlite_vec_row_count(self) -> int:
        """Rows in ``memory_vec`` (0 if disabled)."""
        if not self._sqlite_vec_enabled:
            return 0
        from tapps_brain.sqlite_vec_index import vec_row_count

        with self._lock:
            return vec_row_count(self._conn)

    def _create_schema(self, cur: sqlite3.Cursor) -> None:
        """Create the production v1 schema (all tables, indexes, triggers)."""
        # Main memories table — all columns from the final schema
        cur.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                key TEXT NOT NULL PRIMARY KEY,
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
                embedding TEXT,
                valid_at TEXT,
                invalid_at TEXT,
                superseded_by TEXT,
                agent_scope TEXT DEFAULT 'private',
                integrity_hash TEXT,
                positive_feedback_count REAL NOT NULL DEFAULT 0,
                negative_feedback_count REAL NOT NULL DEFAULT 0,
                source_session_id TEXT NOT NULL DEFAULT '',
                source_channel TEXT NOT NULL DEFAULT '',
                source_message_id TEXT NOT NULL DEFAULT '',
                triggered_by TEXT NOT NULL DEFAULT '',
                valid_from TEXT NOT NULL DEFAULT '',
                valid_until TEXT NOT NULL DEFAULT '',
                stability REAL NOT NULL DEFAULT 0.0,
                difficulty REAL NOT NULL DEFAULT 0.0,
                useful_access_count INTEGER NOT NULL DEFAULT 0,
                total_access_count INTEGER NOT NULL DEFAULT 0,
                memory_group TEXT,
                embedding_model_id TEXT
            )
        """)

        # Indexes
        cur.execute("CREATE INDEX IF NOT EXISTS idx_memories_tier ON memories(tier)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(scope)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_memories_confidence ON memories(confidence)")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_temporal ON memories(valid_at, invalid_at)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_valid_window "
            "ON memories(valid_from, valid_until)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_memory_group ON memories(memory_group)"
        )

        # FTS5 full-text search index
        cur.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
            USING fts5(key, value, tags, content=memories,
            content_rowid=rowid, tokenize='porter unicode61')
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

        # Archived memories (GC)
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
                archived_at TEXT NOT NULL,
                memory_group TEXT,
                embedding_model_id TEXT
            )
        """)

        # Session index (searchable session chunks)
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
            USING fts5(session_id, content, content=session_index,
            content_rowid=rowid, tokenize='porter unicode61')
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

        # Relations (entity/relationship extraction)
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

        # Feedback events
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

        # Diagnostics history (quality scorecard)
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

        # Flywheel metadata KV store
        cur.execute("""
            CREATE TABLE IF NOT EXISTS flywheel_meta (
                key   TEXT NOT NULL PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        # Record schema version
        cur.execute(
            "INSERT INTO schema_version (version, migrated_at) VALUES (?, ?)",
            (_SCHEMA_VERSION, datetime.now(tz=UTC).isoformat()),
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

        # Embedding
        columns.append("embedding")
        values = (*values, embedding_json)

        # Temporal fields
        columns.extend(["valid_at", "invalid_at", "superseded_by"])
        values = (*values, entry.valid_at, entry.invalid_at, entry.superseded_by)

        # Agent scope
        columns.append("agent_scope")
        values = (*values, entry.agent_scope)

        # Integrity hash
        from tapps_brain.integrity import compute_integrity_hash

        tier_str = entry.tier.value if hasattr(entry.tier, "value") else str(entry.tier)
        source_str = entry.source.value if hasattr(entry.source, "value") else str(entry.source)
        integrity_hash = compute_integrity_hash(entry.key, entry.value, tier_str, source_str)
        columns.append("integrity_hash")
        values = (*values, integrity_hash)

        # Flywheel feedback tallies
        columns.extend(["positive_feedback_count", "negative_feedback_count"])
        values = (*values, entry.positive_feedback_count, entry.negative_feedback_count)

        # Provenance metadata (GitHub #38)
        columns.extend(["source_session_id", "source_channel", "source_message_id", "triggered_by"])
        values = (
            *values,
            entry.source_session_id,
            entry.source_channel,
            entry.source_message_id,
            entry.triggered_by,
        )

        # Temporal validity window (GitHub #29)
        columns.extend(["valid_from", "valid_until"])
        values = (*values, entry.valid_from, entry.valid_until)

        # Adaptive stability and difficulty (GitHub #28)
        columns.extend(["stability", "difficulty"])
        values = (*values, entry.stability, entry.difficulty)

        # Bayesian confidence update counters (GitHub #35)
        columns.extend(["useful_access_count", "total_access_count"])
        values = (*values, entry.useful_access_count, entry.total_access_count)

        # Project-local partition (GitHub #49)
        columns.append("memory_group")
        values = (*values, entry.memory_group)

        # Dense embedding model id (STORY-042.2)
        columns.append("embedding_model_id")
        values = (*values, entry.embedding_model_id)

        placeholders = ", ".join("?" * len(columns))

        cols = ", ".join(columns)
        with self._lock:
            self._conn.execute(
                f"INSERT OR REPLACE INTO memories ({cols}) VALUES ({placeholders})",
                values,
            )
            self._sqlite_vec_sync_unlocked(entry)
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
            if self._sqlite_vec_enabled:
                from tapps_brain.sqlite_vec_index import delete_vec_key

                delete_vec_key(self._conn, key)
            self._conn.commit()
        deleted = cur.rowcount > 0
        if deleted:
            self._audit_log("delete", key)
        return deleted

    # Allowed time_field values for temporal filtering (Issue #70).
    _TEMPORAL_FIELDS: frozenset[str] = frozenset({"created_at", "updated_at", "last_accessed"})

    def search(
        self,
        query: str,
        *,
        memory_group: str | None = None,
        since: str | None = None,
        until: str | None = None,
        time_field: str = "created_at",
    ) -> list[MemoryEntry]:
        """Full-text search via FTS5 across key, value, and tags.

        Args:
            query: FTS query text.
            memory_group: When set, restrict to entries in this project-local group.
            since: ISO-8601 UTC lower bound (inclusive) on *time_field*.
            until: ISO-8601 UTC upper bound (exclusive) on *time_field*.
            time_field: Column to filter on (``created_at``, ``updated_at``,
                or ``last_accessed``). Defaults to ``created_at``.
        """
        if not query.strip():
            return []

        if time_field not in self._TEMPORAL_FIELDS:
            msg = f"time_field must be one of {sorted(self._TEMPORAL_FIELDS)}, got {time_field!r}"
            raise ValueError(msg)

        # Escape FTS5 special characters for safety
        safe_query = self._escape_fts_query_text(query)
        if not safe_query:
            return []

        sql = """
                    SELECT m.* FROM memories m
                    JOIN memories_fts fts ON m.rowid = fts.rowid
                    WHERE memories_fts MATCH ?
        """
        params: list[Any] = [safe_query]
        if memory_group is not None:
            sql += " AND m.memory_group = ?"
            params.append(memory_group)
        if since is not None:
            sql += f" AND m.{time_field} >= ?"
            params.append(since)
        if until is not None:
            sql += f" AND m.{time_field} < ?"
            params.append(until)

        read_conn = self._get_read_connection()
        if read_conn is not None:
            with self._read_lock:
                try:
                    rows = read_conn.execute(sql, params).fetchall()
                except sqlite3.OperationalError:
                    logger.debug("fts_search_failed", query=query)
                    return []
        else:
            with self._lock:
                try:
                    rows = self._conn.execute(sql, params).fetchall()
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
        """Read a flywheel_meta value (EPIC-031); None if missing."""
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM flywheel_meta WHERE key = ?",
                (key,),
            ).fetchone()
        return str(row[0]) if row and row[0] is not None else None

    def flywheel_meta_set(self, key: str, value: str) -> None:
        """Upsert a flywheel_meta key (EPIC-031)."""
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
        with self._read_lock:
            if self._read_conn is not None:
                with contextlib.suppress(sqlite3.Error):
                    self._read_conn.close()
                self._read_conn = None
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
        safe = self._escape_fts_query_text(query)
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
    def _row_to_entry(row: sqlite3.Row) -> MemoryEntry:  # noqa: PLR0915
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

        # Read adaptive stability / difficulty (v14+, GitHub #28)
        stability: float = 0.0
        difficulty: float = 0.0
        with contextlib.suppress(KeyError, IndexError):
            stability = float(row["stability"] or 0.0)
            difficulty = float(row["difficulty"] or 0.0)

        # Read Bayesian confidence update counters (v15+, GitHub #35)
        useful_access_count: int = 0
        total_access_count: int = 0
        with contextlib.suppress(KeyError, IndexError):
            useful_access_count = int(row["useful_access_count"] or 0)
            total_access_count = int(row["total_access_count"] or 0)

        # Project-local group (v16+, GitHub #49)
        memory_group: str | None = None
        with contextlib.suppress(KeyError, IndexError):
            mg = row["memory_group"]
            memory_group = str(mg) if mg is not None and str(mg) != "" else None

        embedding_model_id: str | None = None
        with contextlib.suppress(KeyError, IndexError):
            em = row["embedding_model_id"]
            embedding_model_id = str(em) if em is not None and str(em) != "" else None

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
            embedding_model_id=embedding_model_id,
            valid_at=valid_at,
            invalid_at=invalid_at,
            superseded_by=superseded_by,
            agent_scope=agent_scope,
            memory_group=memory_group,
            integrity_hash=integrity_hash,
            positive_feedback_count=pos_fb,
            negative_feedback_count=neg_fb,
            source_session_id=source_session_id,
            source_channel=source_channel,
            source_message_id=source_message_id,
            triggered_by=triggered_by,
            valid_from=valid_from,
            valid_until=valid_until,
            stability=stability,
            difficulty=difficulty,
            useful_access_count=useful_access_count,
            total_access_count=total_access_count,
        )

    def _escape_fts_query_text(self, query: str) -> str:
        """Escape an FTS5 query string for safe matching.

        Wraps each token in double quotes to treat them as literals (AND).
        Inner double-quote characters are escaped by doubling them (FTS5 syntax).
        Term splitting follows :class:`~tapps_brain.lexical.LexicalRetrievalConfig`.
        """
        return build_fts_match_query(
            query,
            fts_path_splits=self._lexical.fts_path_splits,
        )

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
