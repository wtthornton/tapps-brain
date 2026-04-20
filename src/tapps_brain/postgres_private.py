"""PostgreSQL implementation of PrivateBackend protocol.

EPIC-059 STORY-059.5 — private agent memory wired through Postgres.
All queries are scoped to the ``(project_id, agent_id)`` pair supplied at
construction, replacing per-agent SQLite files (``.tapps-brain/agents/<id>/memory.db``).
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

import structlog

if TYPE_CHECKING:
    from tapps_brain.models import MemoryEntry
    from tapps_brain.postgres_connection import PostgresConnectionManager
    from tapps_brain.relations import RelationEntry

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level missing-index counter (TAP-655)
# ---------------------------------------------------------------------------

# Thread-safe counter: project_id → number of startup checks that found
# idx_priv_embedding_hnsw absent.  Incremented once per
# PostgresPrivateBackend.verify_expected_indexes() call that detects a gap;
# reset only on process restart.  Consumed by http_adapter._collect_metrics()
# to emit ``tapps_brain_private_missing_indexes_total``.
_MISSING_INDEX_COUNTS: dict[str, int] = {}
_MISSING_INDEX_COUNTS_LOCK = threading.Lock()

#: Index names that must exist on ``private_memories`` after migration 002.
_EXPECTED_PRIVATE_INDEXES: frozenset[str] = frozenset({"idx_priv_embedding_hnsw"})


def get_missing_index_counts_snapshot() -> dict[str, int]:
    """Return a frozen copy of the per-project missing-index counter.

    Called by :func:`tapps_brain.http_adapter._collect_metrics` to render
    ``tapps_brain_private_missing_indexes_total`` in Prometheus exposition
    format.  Safe to call from any thread.
    """
    with _MISSING_INDEX_COUNTS_LOCK:
        return dict(_MISSING_INDEX_COUNTS)


# DDL for the private_relations auxiliary table.  Created on first use.
_RELATIONS_DDL = """\
CREATE TABLE IF NOT EXISTS private_relations (
    project_id          TEXT        NOT NULL,
    agent_id            TEXT        NOT NULL,
    subject             TEXT        NOT NULL,
    predicate           TEXT        NOT NULL,
    object_entity       TEXT        NOT NULL,
    source_entry_keys   JSONB       NOT NULL DEFAULT '[]'::jsonb,
    confidence          REAL        NOT NULL DEFAULT 0.8,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, agent_id, subject, predicate, object_entity)
);
CREATE INDEX IF NOT EXISTS idx_priv_rel_project_agent
    ON private_relations (project_id, agent_id);
"""

# Schema version reported by get_schema_version() (mirrors 001_initial.sql).
_PRIVATE_SCHEMA_VERSION = 1

# Valid time_field values for temporal filtering.
_VALID_TIME_FIELDS: frozenset[str] = frozenset({"created_at", "updated_at", "last_accessed"})


class PostgresPrivateBackend:
    """PostgreSQL-backed private memory backend.

    Satisfies the ``PrivateBackend`` protocol (``_protocols.py``).  All
    operations are scoped to the ``(project_id, agent_id)`` pair set at
    construction.  No SQLite files are created.

    The ``private_memories`` table and its indexes must already exist (applied by
    ``tapps_brain.postgres_migrations.discover_private_migrations()`` → migration
    ``001_initial.sql``).  The lighter-weight ``private_relations`` table is
    created inline on first use via :meth:`_ensure_relations_table`.

    Path sentinels
    --------------
    ``db_path``, ``store_dir``, and ``audit_path`` return ``Path("/dev/null")``.
    v3 is Postgres-only (ADR-007) — these paths exist for legacy protocol
    compatibility and are not written to.  JSONL audit is a no-op; feedback
    events live in the ``feedback_events`` table (migration 003) via
    :class:`~tapps_brain.feedback.FeedbackStore`.
    """

    def __init__(
        self,
        connection_manager: PostgresConnectionManager,
        *,
        project_id: str,
        agent_id: str,
    ) -> None:
        self._cm = connection_manager
        self._project_id = project_id
        self._agent_id = agent_id

        # Sentinel paths — required by PrivateBackend protocol; no real files.
        self._db_path: Path = Path("/dev/null")
        self._store_dir: Path = Path("/dev/null").parent
        self._audit_path: Path = Path("/dev/null")

        self._lock = threading.Lock()
        self._relations_ensured = False

    # ------------------------------------------------------------------
    # Connection helper — enforces tenant RLS (EPIC-069 STORY-069.8)
    # ------------------------------------------------------------------

    def _scoped_conn(self) -> Any:
        """Return a connection-context bound to this store's project_id.

        Delegates to :meth:`PostgresConnectionManager.project_context`,
        which runs ``SET LOCAL app.project_id`` inside the transaction so
        the RLS policies on ``private_memories`` (migration 009) restrict
        every read and write to this tenant.  ``SET LOCAL`` is
        transaction-scoped so the identity cannot leak across pool
        borrows.

        Falls back to :meth:`PostgresConnectionManager.get_connection`
        when the underlying manager does not expose ``project_context``
        (keeps mocked unit-test managers and non-Postgres dev fakes
        working; RLS is a no-op against an in-memory backend).
        """
        pc = getattr(self._cm, "project_context", None)
        if pc is not None:
            return pc(self._project_id)
        return self._cm.get_connection()

    # ------------------------------------------------------------------
    # Protocol-required properties
    # ------------------------------------------------------------------

    @property
    def store_dir(self) -> Path:
        """Sentinel path — Postgres backend has no on-disk store directory."""
        return self._store_dir

    @property
    def db_path(self) -> Path:
        """Sentinel path — Postgres backend has no SQLite file."""
        return self._db_path

    @property
    def audit_path(self) -> Path:
        """Sentinel path — JSONL audit log is not used by this backend."""
        return self._audit_path

    @property
    def encryption_key(self) -> str | None:
        """Always ``None`` — Postgres uses pg_tde at the storage layer (ADR-007)."""
        return None

    # ------------------------------------------------------------------
    # Core CRUD — private_memories table
    # ------------------------------------------------------------------

    def save(self, entry: MemoryEntry) -> None:
        """Upsert a :class:`MemoryEntry` into ``private_memories``."""
        tier = entry.tier.value if hasattr(entry.tier, "value") else str(entry.tier)
        source = entry.source.value if hasattr(entry.source, "value") else str(entry.source)
        scope = entry.scope.value if hasattr(entry.scope, "value") else str(entry.scope)
        tags_json = json.dumps(entry.tags, ensure_ascii=False)

        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO private_memories (
                    project_id, agent_id, key, value,
                    tier, confidence, source, source_agent,
                    scope, agent_scope, memory_group, tags,
                    created_at, updated_at, last_accessed,
                    access_count, useful_access_count, total_access_count,
                    branch, last_reinforced, reinforce_count,
                    contradicted, contradiction_reason, seeded_from,
                    valid_at, invalid_at, superseded_by,
                    valid_from, valid_until,
                    source_session_id, source_channel, source_message_id, triggered_by,
                    stability, difficulty,
                    positive_feedback_count, negative_feedback_count,
                    integrity_hash, embedding_model_id,
                    temporal_sensitivity
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s::jsonb,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s,
                    %s, %s, %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s,
                    %s
                )
                ON CONFLICT (project_id, agent_id, key) DO UPDATE SET
                    value                    = EXCLUDED.value,
                    tier                     = EXCLUDED.tier,
                    confidence               = EXCLUDED.confidence,
                    source                   = EXCLUDED.source,
                    source_agent             = EXCLUDED.source_agent,
                    scope                    = EXCLUDED.scope,
                    agent_scope              = EXCLUDED.agent_scope,
                    memory_group             = EXCLUDED.memory_group,
                    tags                     = EXCLUDED.tags,
                    updated_at               = EXCLUDED.updated_at,
                    last_accessed            = EXCLUDED.last_accessed,
                    access_count             = EXCLUDED.access_count,
                    useful_access_count      = EXCLUDED.useful_access_count,
                    total_access_count       = EXCLUDED.total_access_count,
                    branch                   = EXCLUDED.branch,
                    last_reinforced          = EXCLUDED.last_reinforced,
                    reinforce_count          = EXCLUDED.reinforce_count,
                    contradicted             = EXCLUDED.contradicted,
                    contradiction_reason     = EXCLUDED.contradiction_reason,
                    seeded_from              = EXCLUDED.seeded_from,
                    valid_at                 = EXCLUDED.valid_at,
                    invalid_at               = EXCLUDED.invalid_at,
                    superseded_by            = EXCLUDED.superseded_by,
                    valid_from               = EXCLUDED.valid_from,
                    valid_until              = EXCLUDED.valid_until,
                    source_session_id        = EXCLUDED.source_session_id,
                    source_channel           = EXCLUDED.source_channel,
                    source_message_id        = EXCLUDED.source_message_id,
                    triggered_by             = EXCLUDED.triggered_by,
                    stability                = EXCLUDED.stability,
                    difficulty               = EXCLUDED.difficulty,
                    positive_feedback_count  = EXCLUDED.positive_feedback_count,
                    negative_feedback_count  = EXCLUDED.negative_feedback_count,
                    integrity_hash           = EXCLUDED.integrity_hash,
                    embedding_model_id       = EXCLUDED.embedding_model_id,
                    temporal_sensitivity     = EXCLUDED.temporal_sensitivity
                """,
                (
                    self._project_id,
                    self._agent_id,
                    entry.key,
                    entry.value,
                    tier,
                    entry.confidence,
                    source,
                    entry.source_agent,
                    scope,
                    entry.agent_scope,
                    entry.memory_group,
                    tags_json,
                    entry.created_at,
                    entry.updated_at,
                    entry.last_accessed,
                    entry.access_count,
                    entry.useful_access_count,
                    entry.total_access_count,
                    entry.branch,
                    entry.last_reinforced,
                    entry.reinforce_count,
                    entry.contradicted,
                    entry.contradiction_reason,
                    entry.seeded_from,
                    entry.valid_at,
                    entry.invalid_at,
                    entry.superseded_by,
                    entry.valid_from,
                    entry.valid_until,
                    entry.source_session_id,
                    entry.source_channel,
                    entry.source_message_id,
                    entry.triggered_by,
                    entry.stability,
                    entry.difficulty,
                    entry.positive_feedback_count,
                    entry.negative_feedback_count,
                    entry.integrity_hash,
                    entry.embedding_model_id,
                    entry.temporal_sensitivity,
                ),
            )

        logger.debug(
            "postgres_private.saved",
            project_id=self._project_id,
            agent_id=self._agent_id,
            key=entry.key,
        )

    def load_all(self, *, limit: int | None = None) -> list[MemoryEntry]:
        """Load entries for this ``(project_id, agent_id)`` scope.

        Used by :class:`MemoryStore` on cold-start to populate the in-memory cache.
        Streams rows in chunks of 1 000 to avoid materialising the full result set
        at once.  Pass *limit* to apply an early-cutoff after the most-recently-
        updated entries have been collected (entries are ordered by ``updated_at
        DESC`` so callers that honour a max-entries cap can stop early instead of
        loading stale rows that would be evicted anyway).

        Args:
            limit: Maximum number of entries to return.  ``None`` means no cap.
        """
        _CHUNK = 1000
        results: list[MemoryEntry] = []
        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM private_memories"
                " WHERE project_id = %s AND agent_id = %s"
                " ORDER BY updated_at DESC",
                (self._project_id, self._agent_id),
            )
            col_names = [desc[0] for desc in cur.description]
            while True:
                chunk = cur.fetchmany(_CHUNK)
                if not chunk:
                    break
                for row in chunk:
                    results.append(self._row_to_entry(dict(zip(col_names, row, strict=False))))
                    if limit is not None and len(results) >= limit:
                        return results
        return results

    def delete(self, key: str) -> bool:
        """Delete an entry by key.  Returns ``True`` if a row was removed."""
        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM private_memories WHERE project_id = %s AND agent_id = %s AND key = %s",
                (self._project_id, self._agent_id, key),
            )
            deleted = (cur.rowcount or 0) > 0
        if deleted:
            logger.debug(
                "postgres_private.deleted",
                project_id=self._project_id,
                agent_id=self._agent_id,
                key=key,
            )
        return deleted

    def search(
        self,
        query: str,
        *,
        memory_group: str | None = None,
        since: str | None = None,
        until: str | None = None,
        time_field: str = "created_at",
        as_of: str | None = None,
    ) -> list[MemoryEntry]:
        """Full-text search via ``search_vector @@ plainto_tsquery``.

        The ``search_vector`` column is maintained by a Postgres trigger defined
        in ``migrations/private/001_initial.sql``.  Results are ranked by
        ``ts_rank``.

        Args:
            query: Plain-text search query (passed to ``plainto_tsquery``).
            memory_group: Restrict results to a project-local group.
            since: ISO-8601 lower bound (inclusive) on *time_field*.
            until: ISO-8601 upper bound (exclusive) on *time_field*.
            time_field: Column to filter on.
            as_of: ISO-8601 timestamp for bi-temporal point-in-time filtering.
                When set, adds ``(valid_at IS NULL OR valid_at <= as_of)`` and
                ``(invalid_at IS NULL OR invalid_at > as_of)`` predicates so only
                the version of an entry that was valid at *as_of* is returned.
                The value is passed as a parameterised ``%s::timestamptz``
                placeholder — never string-concatenated (SQL injection safe).
                Corresponds to the ``valid_at``/``invalid_at`` columns from
                migration 001 (``migrations/private/001_initial.sql``).
        """
        if time_field not in _VALID_TIME_FIELDS:
            msg = f"time_field must be one of {sorted(_VALID_TIME_FIELDS)}, got {time_field!r}"
            raise ValueError(msg)
        if not query.strip():
            return []

        # Base query using the tsvector index (idx_priv_search_vector_gin).
        sql = (
            "SELECT *, ts_rank(search_vector, plainto_tsquery('english', %s)) AS _rank "
            "FROM private_memories "
            "WHERE project_id = %s AND agent_id = %s "
            "  AND search_vector @@ plainto_tsquery('english', %s)"
        )
        params: list[Any] = [query, self._project_id, self._agent_id, query]

        if memory_group is not None:
            sql += " AND memory_group = %s"
            params.append(memory_group)
        if since is not None:
            sql += f" AND {time_field} >= %s"
            params.append(since)
        if until is not None:
            sql += f" AND {time_field} < %s"
            params.append(until)

        # Bi-temporal as_of filter (STORY-066.2).
        # NULL valid_at / invalid_at means "unbounded" — always visible.
        if as_of is not None:
            sql += (
                " AND (valid_at IS NULL OR valid_at <= %s::timestamptz)"
                " AND (invalid_at IS NULL OR invalid_at > %s::timestamptz)"
            )
            params.append(as_of)
            params.append(as_of)

        sql += " ORDER BY _rank DESC LIMIT 100"

        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            if not rows:
                return []
            col_names = [desc[0] for desc in cur.description]

        results = []
        for row in rows:
            row_dict = dict(zip(col_names, row, strict=False))
            row_dict.pop("_rank", None)  # computed column, not in MemoryEntry
            results.append(self._row_to_entry(row_dict))
        return results

    # ------------------------------------------------------------------
    # Vector similarity search
    # ------------------------------------------------------------------

    def knn_search(self, query_embedding: list[float], k: int) -> list[tuple[str, float]]:
        """Approximate nearest-neighbour search via pgvector cosine distance.

        Uses the ``idx_priv_embedding_hnsw`` index (migration 002).  Returns
        ``(key, distance)`` pairs, lowest distance first.  Returns ``[]`` if
        the ``embedding`` column is unpopulated or on any DB error.
        """
        if not query_embedding:
            return []

        vec_str = "[" + ",".join(str(v) for v in query_embedding) + "]"
        sql = (
            "SELECT key, embedding <=> %s::vector AS distance "
            "FROM private_memories "
            "WHERE project_id = %s AND agent_id = %s AND embedding IS NOT NULL "
            "ORDER BY distance "
            "LIMIT %s"
        )
        try:
            with self._scoped_conn() as conn, conn.cursor() as cur:
                cur.execute(sql, (vec_str, self._project_id, self._agent_id, k))
                rows = cur.fetchall()
            return [(str(r[0]), float(r[1])) for r in rows]
        except Exception:  # noqa: BLE001 — psycopg errors are heterogeneous; fallback to default
            logger.warning("postgres_private.knn_search_failed", exc_info=True)
            return []

    def vector_row_count(self) -> int:
        """Number of entries with a non-NULL embedding vector."""
        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM private_memories "
                "WHERE project_id = %s AND agent_id = %s AND embedding IS NOT NULL",
                (self._project_id, self._agent_id),
            )
            row = cur.fetchone()
        return int(row[0]) if row else 0

    # ------------------------------------------------------------------
    # Startup index sanity check (TAP-655)
    # ------------------------------------------------------------------

    def verify_expected_indexes(self) -> list[str]:
        """Check that all expected indexes on ``private_memories`` are present.

        Queries ``pg_indexes`` for the table and compares against
        :data:`_EXPECTED_PRIVATE_INDEXES`.  When any index is absent:

        * A ``WARNING`` structured log is emitted (key
          ``"private.indexes.missing"``).
        * The per-project counter in :data:`_MISSING_INDEX_COUNTS` is
          incremented so the HTTP adapter can expose
          ``tapps_brain_private_missing_indexes_total`` to Prometheus
          scrapers.

        Returns the list of missing index names (empty when all present).

        This is a best-effort check — any DB error is caught, logged at
        DEBUG level, and treated as "no missing indexes" so a transient
        connection hiccup at startup does not abort the store.

        Likely cause of a non-empty result: migration 002 (HNSW upgrade) was
        never applied.  The embedding recall path still works but falls back
        to a sequential scan, degrading latency.

        .. note::
            Call once at ``MemoryStore.__init__`` after :meth:`load_all`.
            Calling repeatedly is harmless but redundant.
        """
        try:
            with self._scoped_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE tablename = 'private_memories' AND schemaname = 'public'"
                )
                present = {str(row[0]) for row in cur.fetchall()}
        except Exception:  # noqa: BLE001 — psycopg errors are heterogeneous; fallback to default
            logger.warning(
                "postgres_private.verify_expected_indexes.db_error",
                exc_info=True,
            )
            return []

        missing = sorted(_EXPECTED_PRIVATE_INDEXES - present)
        if missing:
            logger.warning(
                "private.indexes.missing",
                missing=missing,
                project_id=self._project_id,
                hint=(
                    "Apply migration 002 (002_hnsw_upgrade.sql) to create the HNSW index. "
                    "Until then, vector recall falls back to a sequential scan."
                ),
            )
            with _MISSING_INDEX_COUNTS_LOCK:
                _MISSING_INDEX_COUNTS[self._project_id] = (
                    _MISSING_INDEX_COUNTS.get(self._project_id, 0) + 1
                )
        return missing

    # ------------------------------------------------------------------
    # Relations
    # ------------------------------------------------------------------

    def _ensure_relations_table(self) -> None:
        """Create ``private_relations`` if it does not yet exist (idempotent)."""
        with self._lock:
            if self._relations_ensured:
                return
            with self._scoped_conn() as conn, conn.cursor() as cur:
                cur.execute(_RELATIONS_DDL)
            self._relations_ensured = True

    def list_relations(self) -> list[dict[str, Any]]:
        """Return all relations for this ``(project_id, agent_id)`` scope."""
        self._ensure_relations_table()
        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT subject, predicate, object_entity, "
                "       source_entry_keys, confidence, created_at "
                "FROM private_relations "
                "WHERE project_id = %s AND agent_id = %s",
                (self._project_id, self._agent_id),
            )
            rows = cur.fetchall()
        if not rows:
            return []

        results: list[dict[str, Any]] = []
        for r in rows:
            raw_keys = r[3]
            if isinstance(raw_keys, list):
                keys: list[str] = [str(k) for k in raw_keys]
            elif isinstance(raw_keys, str):
                try:
                    keys = json.loads(raw_keys)
                except (json.JSONDecodeError, TypeError):
                    keys = []
            else:
                keys = []
            created_raw = r[5]
            created_str = (
                created_raw.isoformat() if hasattr(created_raw, "isoformat") else str(created_raw)
            )
            results.append(
                {
                    "subject": str(r[0]),
                    "predicate": str(r[1]),
                    "object_entity": str(r[2]),
                    "source_entry_keys": keys,
                    "confidence": float(r[4]),
                    "created_at": created_str,
                }
            )
        return results

    def count_relations(self) -> int:
        """Total relation count for this ``(project_id, agent_id)`` scope."""
        self._ensure_relations_table()
        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM private_relations WHERE project_id = %s AND agent_id = %s",
                (self._project_id, self._agent_id),
            )
            row = cur.fetchone()
        return int(row[0]) if row else 0

    def save_relations(self, key: str, relations: list[RelationEntry]) -> int:
        """Batch-upsert relations linked to a memory entry key.

        Each relation's ``source_entry_keys`` is ensured to contain *key*.
        Returns the number of relations saved.
        """
        if not relations:
            return 0
        self._ensure_relations_table()
        now = datetime.now(tz=UTC).isoformat()
        count = 0
        with self._scoped_conn() as conn, conn.cursor() as cur:
            for rel in relations:
                source_keys: list[str] = list(dict.fromkeys([*rel.source_entry_keys, key]))
                cur.execute(
                    """
                    INSERT INTO private_relations
                        (project_id, agent_id, subject, predicate, object_entity,
                         source_entry_keys, confidence, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                    ON CONFLICT (project_id, agent_id, subject, predicate, object_entity)
                    DO UPDATE SET
                        source_entry_keys = EXCLUDED.source_entry_keys,
                        confidence        = EXCLUDED.confidence
                    """,
                    (
                        self._project_id,
                        self._agent_id,
                        rel.subject,
                        rel.predicate,
                        rel.object_entity,
                        json.dumps(source_keys, ensure_ascii=False),
                        rel.confidence,
                        now,
                    ),
                )
                count += 1
        return count

    def load_relations(self, key: str) -> list[dict[str, Any]]:
        """Return relations whose ``source_entry_keys`` contains *key*."""
        return [r for r in self.list_relations() if key in r["source_entry_keys"]]

    def delete_relations(self, key: str) -> int:
        """Delete all relations whose ``source_entry_keys`` contains *key*.

        Called during consolidation undo to remove relation rows for the
        deleted consolidated entry.  Returns the count of rows removed.
        """
        self._ensure_relations_table()
        try:
            with self._scoped_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM private_relations
                    WHERE project_id = %s
                      AND agent_id   = %s
                      AND source_entry_keys::jsonb @> %s::jsonb
                    """,
                    (
                        self._project_id,
                        self._agent_id,
                        json.dumps([key], ensure_ascii=False),
                    ),
                )
                return cur.rowcount or 0
        except Exception:  # noqa: BLE001 — psycopg errors are heterogeneous; fallback to default
            logger.warning(
                "postgres_private.delete_relations_failed",
                key=key,
                exc_info=True,
            )
            return 0

    # ------------------------------------------------------------------
    # Schema / version
    # ------------------------------------------------------------------

    def get_schema_version(self) -> int:
        """Return the private-memory schema version (from ``private_schema_version``)."""
        try:
            with self._scoped_conn() as conn, conn.cursor() as cur:
                cur.execute("SELECT MAX(version) FROM private_schema_version")
                row = cur.fetchone()
            return int(row[0]) if row and row[0] is not None else _PRIVATE_SCHEMA_VERSION
        except Exception:  # noqa: BLE001 — psycopg errors are heterogeneous; fallback to default
            logger.warning("postgres_private.get_schema_version_failed", exc_info=True)
            return _PRIVATE_SCHEMA_VERSION

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------

    def append_audit(
        self,
        action: str,
        key: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Append a row to the Postgres ``audit_log`` table (migration 005).

        Best-effort: failures are logged but never raised — audit MUST NOT
        block the hot save/delete path.
        """
        try:
            with self._scoped_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO audit_log
                        (project_id, agent_id, event_type, key, details)
                    VALUES (%s, %s, %s, %s, %s::jsonb)
                    """,
                    (
                        self._project_id,
                        self._agent_id,
                        action,
                        key or "",
                        json.dumps(extra or {}, default=str),
                    ),
                )
        except Exception:  # noqa: BLE001 — psycopg errors are heterogeneous; fallback to default
            logger.warning(
                "postgres_private.audit_append_failed",
                action=action,
                key=key,
                exc_info=True,
            )

    def query_audit(
        self,
        *,
        key: str | None = None,
        event_type: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Read entries from ``audit_log`` for this ``(project_id, agent_id)``.

        Returns dicts with ``timestamp`` (ISO-8601 string), ``event_type``,
        ``key``, and ``details``.  Ordered oldest-to-newest.
        """
        conditions: list[str] = ["project_id = %s", "agent_id = %s"]
        params: list[Any] = [self._project_id, self._agent_id]
        if key is not None:
            conditions.append("key = %s")
            params.append(key)
        if event_type is not None:
            conditions.append("event_type = %s")
            params.append(event_type)
        if since is not None:
            conditions.append("timestamp >= %s")
            params.append(since)
        if until is not None:
            conditions.append("timestamp <= %s")
            params.append(until)
        from psycopg import sql as pgsql

        where = pgsql.SQL(" AND ").join(pgsql.SQL(c) for c in conditions)
        stmt = pgsql.SQL(
            "SELECT timestamp, event_type, key, details "
            "FROM audit_log WHERE {} "
            "ORDER BY timestamp ASC, id ASC LIMIT {}"
        ).format(where, pgsql.Placeholder())
        params.append(limit)
        try:
            with self._scoped_conn() as conn, conn.cursor() as cur:
                cur.execute(stmt, params)
                rows = cur.fetchall()
        except Exception:  # noqa: BLE001 — psycopg errors are heterogeneous; fallback to default
            logger.warning("postgres_private.audit_query_failed", exc_info=True)
            return []

        results: list[dict[str, Any]] = []
        for r in rows:
            ts = r[0]
            ts_str = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
            details_raw = r[3]
            if isinstance(details_raw, dict):
                details = details_raw
            elif isinstance(details_raw, str):
                try:
                    details = json.loads(details_raw)
                except (json.JSONDecodeError, TypeError):
                    details = {}
            else:
                details = {}
            results.append(
                {
                    "timestamp": ts_str,
                    "event_type": str(r[1]),
                    "key": str(r[2] or ""),
                    "details": details,
                }
            )
        return results

    # ------------------------------------------------------------------
    # Flywheel metadata (migration 007, STORY-066.14)
    # ------------------------------------------------------------------

    def flywheel_meta_get(self, key: str) -> str | None:
        """Return the stored flywheel metadata value for *key*, or ``None``.

        Best-effort: failures log and return ``None`` so the flywheel pipeline
        can still run (it will just reprocess from the beginning).
        """
        try:
            with self._scoped_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT value FROM flywheel_meta "
                    "WHERE project_id = %s AND agent_id = %s AND key = %s",
                    (self._project_id, self._agent_id, key),
                )
                row = cur.fetchone()
                return str(row[0]) if row else None
        except Exception:  # noqa: BLE001 — psycopg errors are heterogeneous; fallback to default
            logger.warning("postgres_private.flywheel_meta_get_failed", key=key, exc_info=True)
            return None

    def flywheel_meta_set(self, key: str, value: str) -> None:
        """Upsert a flywheel metadata value for *key*.

        Best-effort: failures log and are swallowed so a transient DB issue
        can't break the feedback pipeline's in-memory state.
        """
        try:
            with self._scoped_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO flywheel_meta (project_id, agent_id, key, value, updated_at)
                    VALUES (%s, %s, %s, %s, now())
                    ON CONFLICT (project_id, agent_id, key)
                    DO UPDATE SET value = EXCLUDED.value, updated_at = now()
                    """,
                    (self._project_id, self._agent_id, key, value),
                )
        except Exception:  # noqa: BLE001 — psycopg errors are heterogeneous; fallback to default
            logger.warning("postgres_private.flywheel_meta_set_failed", key=key, exc_info=True)

    # ------------------------------------------------------------------
    # GC archive (migration 006, STORY-066.3)
    # ------------------------------------------------------------------

    def archive_entry(self, entry: MemoryEntry) -> int:
        """INSERT a GC-evicted entry into ``gc_archive`` and return byte_count.

        The payload is the full ``MemoryEntry.model_dump()`` serialised to JSON.
        ``byte_count`` is denormalised at insert time to keep ``total_archive_bytes``
        cheap (``SUM(byte_count)`` instead of ``SUM(octet_length(payload::text))``).

        Best-effort: logs and returns 0 on failure — GC must not be blocked by
        an archive write error.
        """
        try:
            payload_dict = entry.model_dump()
            payload_json = json.dumps(payload_dict, default=str)
            byte_count = len(payload_json.encode("utf-8"))
            with self._scoped_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO gc_archive
                        (project_id, agent_id, archived_at, key, payload, byte_count)
                    VALUES (%s, %s, now(), %s, %s::jsonb, %s)
                    ON CONFLICT (project_id, agent_id, archived_at, key) DO NOTHING
                    """,
                    (
                        self._project_id,
                        self._agent_id,
                        entry.key,
                        payload_json,
                        byte_count,
                    ),
                )
            return byte_count
        except Exception:  # noqa: BLE001 — psycopg errors are heterogeneous; fallback to default
            logger.warning(
                "postgres_private.gc_archive_entry_failed",
                key=entry.key,
                exc_info=True,
            )
            return 0

    def list_archive(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Return the most recent *limit* rows from ``gc_archive``."""
        try:
            with self._scoped_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT key, archived_at, byte_count, payload
                    FROM gc_archive
                    WHERE project_id = %s AND agent_id = %s
                    ORDER BY archived_at DESC
                    LIMIT %s
                    """,
                    (self._project_id, self._agent_id, limit),
                )
                rows = cur.fetchall()
        except Exception:  # noqa: BLE001 — psycopg errors are heterogeneous; fallback to default
            logger.warning("postgres_private.gc_archive_list_failed", exc_info=True)
            return []

        results: list[dict[str, Any]] = []
        for row in rows:
            key, archived_at, byte_count, payload = row
            ts_str = (
                archived_at.isoformat() if hasattr(archived_at, "isoformat") else str(archived_at)
            )
            results.append(
                {
                    "key": str(key),
                    "archived_at": ts_str,
                    "byte_count": int(byte_count),
                    "payload": payload if isinstance(payload, dict) else {},
                }
            )
        return results

    def total_archive_bytes(self) -> int:
        """Return ``SUM(byte_count)`` from ``gc_archive`` for this agent scope."""
        try:
            with self._scoped_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COALESCE(SUM(byte_count), 0)
                    FROM gc_archive
                    WHERE project_id = %s AND agent_id = %s
                    """,
                    (self._project_id, self._agent_id),
                )
                row = cur.fetchone()
            return int(row[0]) if row else 0
        except Exception:  # noqa: BLE001 — psycopg errors are heterogeneous; fallback to default
            logger.warning("postgres_private.gc_archive_total_bytes_failed", exc_info=True)
            return 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying connection pool."""
        try:
            self._cm.close()
        except Exception:  # noqa: BLE001 — best-effort close; errors must not propagate
            logger.debug("postgres_private.close_failed", exc_info=True)  # nosec B110 — best-effort close; errors must not propagate

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_entry(row: dict[str, Any]) -> MemoryEntry:
        """Convert a Postgres row dict to a :class:`MemoryEntry`."""
        from tapps_brain.models import MemoryEntry, MemoryScope, MemorySource, MemoryTier

        # Tags — stored as JSONB (may arrive as list or JSON string).
        tags_raw = row.get("tags")
        if isinstance(tags_raw, list):
            tags: list[str] = [str(t) for t in tags_raw]
        elif isinstance(tags_raw, str):
            try:
                parsed = json.loads(tags_raw)
                tags = [str(t) for t in parsed] if isinstance(parsed, list) else []
            except (json.JSONDecodeError, TypeError):
                tags = []
        else:
            tags = []

        def _str_or_none(v: Any) -> str | None:
            return str(v) if v is not None else None

        def _to_iso(v: Any) -> str:
            if v is None:
                return datetime.now(tz=UTC).isoformat()
            if hasattr(v, "isoformat"):
                return v.isoformat()  # type: ignore[no-any-return]
            return str(v)

        def _iso_or_none(v: Any) -> str | None:
            """ISO-8601 with ``T`` separator, or ``None``.

            Used for nullable temporal columns so downstream string comparisons
            (``is_temporally_valid``) stay consistent with ``created_at``/
            ``updated_at`` which already use ``isoformat()``.
            """
            if v is None:
                return None
            if hasattr(v, "isoformat"):
                return v.isoformat()  # type: ignore[no-any-return]
            return str(v)

        # Tier — accept enum values or raw strings (profile layers).
        tier_raw = row.get("tier", "pattern")
        try:
            tier: MemoryTier | str = MemoryTier(tier_raw)
        except (ValueError, KeyError):
            tier = str(tier_raw)

        source_raw = row.get("source", "agent")
        try:
            source = MemorySource(source_raw)
        except (ValueError, KeyError):
            source = MemorySource.agent

        scope_raw = row.get("scope", "project")
        try:
            scope = MemoryScope(scope_raw)
        except (ValueError, KeyError):
            scope = MemoryScope.project

        return MemoryEntry(
            key=str(row["key"]),
            value=str(row["value"]),
            tier=tier,
            confidence=float(row.get("confidence", 0.6)),
            source=source,
            source_agent=str(row.get("source_agent", "unknown")),
            scope=scope,
            tags=tags,
            created_at=_to_iso(row.get("created_at")),
            updated_at=_to_iso(row.get("updated_at")),
            last_accessed=_to_iso(row.get("last_accessed")),
            access_count=int(row.get("access_count", 0)),
            useful_access_count=int(row.get("useful_access_count", 0)),
            total_access_count=int(row.get("total_access_count", 0)),
            branch=_str_or_none(row.get("branch")),
            last_reinforced=_iso_or_none(row.get("last_reinforced")),
            reinforce_count=int(row.get("reinforce_count", 0)),
            contradicted=bool(row.get("contradicted", False)),
            contradiction_reason=_str_or_none(row.get("contradiction_reason")),
            seeded_from=_str_or_none(row.get("seeded_from")),
            agent_scope=str(row.get("agent_scope", "private")),
            memory_group=_str_or_none(row.get("memory_group")),
            valid_at=_iso_or_none(row.get("valid_at")),
            invalid_at=_iso_or_none(row.get("invalid_at")),
            superseded_by=_str_or_none(row.get("superseded_by")),
            valid_from=str(row.get("valid_from") or ""),
            valid_until=str(row.get("valid_until") or ""),
            source_session_id=str(row.get("source_session_id") or ""),
            source_channel=str(row.get("source_channel") or ""),
            source_message_id=str(row.get("source_message_id") or ""),
            triggered_by=str(row.get("triggered_by") or ""),
            stability=float(row.get("stability", 0.0)),
            difficulty=float(row.get("difficulty", 0.0)),
            positive_feedback_count=float(row.get("positive_feedback_count", 0.0)),
            negative_feedback_count=float(row.get("negative_feedback_count", 0.0)),
            integrity_hash=_str_or_none(row.get("integrity_hash")),
            embedding_model_id=_str_or_none(row.get("embedding_model_id")),
            temporal_sensitivity=cast(
                "Literal['high', 'medium', 'low'] | None",
                _str_or_none(row.get("temporal_sensitivity")),
            ),
            # embedding is not loaded from DB (large binary; on-demand via knn_search)
        )
