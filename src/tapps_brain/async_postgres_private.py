"""Async-native PostgreSQL implementation of the PrivateBackend protocol.

STORY-072.2 — mirrors :class:`tapps_brain.postgres_private.PostgresPrivateBackend`
with ``async def`` methods and a single shared :mod:`tapps_brain._postgres_private_sql`
import so the SQL stays in lock-step.  Removes the ``asyncio.to_thread()``
hop that ``AsyncMemoryStore`` (v3.6.0) currently pays per call: under load,
agents share the event loop instead of queuing behind the default thread
executor (CPython default 64).

Design notes:

* Reuses the synchronous ``_row_to_entry`` static method and the
  ``_record_missing_indexes`` / ``_parse_jsonb_list`` helpers from
  :mod:`tapps_brain.postgres_private` — they are pure transformations
  with no IO and no async dependence.
* Tenant RLS uses :meth:`PostgresConnectionManager.async_project_context`
  (added in this story); falls back to a plain ``get_async_connection``
  on managers that don't expose it (mocked unit tests).
* ``threading.Lock`` for the relations-table init guard becomes
  ``asyncio.Lock`` — the lock is created lazily on first use because
  constructing it requires a running loop.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from tapps_brain import _postgres_private_sql as _sql
from tapps_brain.postgres_private import (
    PostgresPrivateBackend,
    _record_missing_indexes,
)

if TYPE_CHECKING:
    from tapps_brain.models import MemoryEntry
    from tapps_brain.postgres_connection import PostgresConnectionManager
    from tapps_brain.relations import RelationEntry

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


class AsyncPostgresPrivateBackend:
    """Async-native PostgreSQL backend for private memory.

    Same surface as :class:`PostgresPrivateBackend` but every IO method is
    ``async def``.  Constructor takes the same
    ``(connection_manager, project_id, agent_id)`` shape so callers can
    swap one backend for the other based on whether they're in a sync or
    async context.

    The connection_manager's :meth:`get_async_connection` /
    :meth:`async_project_context` are used for IO; the sync pool on the
    same manager is left untouched.  Both backends can run side-by-side
    against the same physical database.
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

        # Created lazily on first ensure_relations_table because asyncio.Lock
        # construction requires a running event loop and the backend may be
        # instantiated from sync code.
        self._relations_lock: asyncio.Lock | None = None
        self._relations_ensured = False

    # ------------------------------------------------------------------
    # Connection helper — enforces tenant RLS via async_project_context
    # ------------------------------------------------------------------

    def _scoped_conn(self) -> Any:  # noqa: ANN401  -- psycopg async cm
        """Return an async-context-manager bound to this store's project_id.

        Delegates to :meth:`PostgresConnectionManager.async_project_context`
        when available; falls back to ``get_async_connection`` for managers
        without the per-tenant context (mocked unit tests).
        """
        apc = getattr(self._cm, "async_project_context", None)
        if apc is not None:
            return apc(self._project_id)
        return self._cm.get_async_connection()

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
    # Core CRUD
    # ------------------------------------------------------------------

    async def save(self, entry: MemoryEntry) -> None:
        """Upsert a :class:`MemoryEntry` into ``private_memories``."""
        params = _sql.build_save_params(
            entry=entry,
            project_id=self._project_id,
            agent_id=self._agent_id,
        )
        async with self._scoped_conn() as conn, conn.cursor() as cur:
            await cur.execute(_sql.SAVE_UPSERT_SQL, params)

        logger.debug(
            "async_postgres_private.saved",
            project_id=self._project_id,
            agent_id=self._agent_id,
            key=entry.key,
        )

    async def load_all(self, *, limit: int | None = None) -> list[MemoryEntry]:
        """Load entries for this ``(project_id, agent_id)`` scope.

        Streams rows in chunks of 1 000 to avoid materialising the full
        result set at once.  Pass *limit* to apply early-cutoff.
        """
        chunk_size = 1000
        results: list[MemoryEntry] = []
        async with self._scoped_conn() as conn, conn.cursor() as cur:
            await cur.execute(_sql.LOAD_ALL_SQL, (self._project_id, self._agent_id))
            col_names = [desc[0] for desc in cur.description]
            while True:
                chunk = await cur.fetchmany(chunk_size)
                if not chunk:
                    break
                for row in chunk:
                    results.append(
                        PostgresPrivateBackend._row_to_entry(
                            dict(zip(col_names, row, strict=False))
                        )
                    )
                    if limit is not None and len(results) >= limit:
                        return results
        return results

    async def delete(self, key: str) -> bool:
        """Delete an entry by key.  Returns ``True`` if a row was removed."""
        async with self._scoped_conn() as conn, conn.cursor() as cur:
            await cur.execute(_sql.DELETE_BY_KEY_SQL, (self._project_id, self._agent_id, key))
            deleted = (cur.rowcount or 0) > 0
        if deleted:
            logger.debug(
                "async_postgres_private.deleted",
                project_id=self._project_id,
                agent_id=self._agent_id,
                key=key,
            )
        return deleted

    async def search(
        self,
        query: str,
        *,
        memory_group: str | None = None,
        since: str | None = None,
        until: str | None = None,
        time_field: str = "created_at",
        as_of: str | None = None,
        memory_class: str | None = None,
    ) -> list[MemoryEntry]:
        """Full-text search via ``search_vector @@ plainto_tsquery``.

        Behavioral parity with :meth:`PostgresPrivateBackend.search`.  All
        filter composition flows through :func:`_sql.build_search_sql`.
        """
        if not query.strip():
            return []

        sql, extra_params = _sql.build_search_sql(
            memory_group=memory_group,
            since=since,
            until=until,
            time_field=time_field,
            memory_class=memory_class,
            as_of=as_of,
        )
        params: list[Any] = [query, self._project_id, self._agent_id, query, *extra_params]

        async with self._scoped_conn() as conn, conn.cursor() as cur:
            await cur.execute(sql, params)
            rows = await cur.fetchall()
            if not rows:
                return []
            col_names = [desc[0] for desc in cur.description]

        results = []
        for row in rows:
            row_dict = dict(zip(col_names, row, strict=False))
            row_dict.pop("_rank", None)
            results.append(PostgresPrivateBackend._row_to_entry(row_dict))
        return results

    # ------------------------------------------------------------------
    # Vector similarity
    # ------------------------------------------------------------------

    async def knn_search(self, query_embedding: list[float], k: int) -> list[tuple[str, float]]:
        """Approximate nearest-neighbour search via pgvector cosine distance."""
        if not query_embedding:
            return []
        vec_str = "[" + ",".join(str(v) for v in query_embedding) + "]"
        try:
            async with self._scoped_conn() as conn, conn.cursor() as cur:
                await cur.execute(
                    _sql.KNN_SEARCH_SQL,
                    (vec_str, self._project_id, self._agent_id, k),
                )
                rows = await cur.fetchall()
            return [(str(r[0]), float(r[1])) for r in rows]
        except Exception:
            logger.warning("async_postgres_private.knn_search_failed", exc_info=True)
            return []

    async def vector_row_count(self) -> int:
        """Number of entries with a non-NULL embedding vector."""
        async with self._scoped_conn() as conn, conn.cursor() as cur:
            await cur.execute(_sql.VECTOR_ROW_COUNT_SQL, (self._project_id, self._agent_id))
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    # ------------------------------------------------------------------
    # Index sanity check
    # ------------------------------------------------------------------

    async def verify_expected_indexes(self) -> list[str]:
        """Async parity for :meth:`PostgresPrivateBackend.verify_expected_indexes`."""
        try:
            async with self._scoped_conn() as conn, conn.cursor() as cur:
                await cur.execute(_sql.LIST_TABLE_INDEXES_SQL)
                present = {str(row[0]) for row in await cur.fetchall()}
        except Exception:
            logger.warning(
                "async_postgres_private.verify_expected_indexes.db_error",
                exc_info=True,
            )
            return []

        missing = sorted(_sql.EXPECTED_PRIVATE_INDEXES - present)
        if missing:
            logger.warning(
                "private.indexes.missing",
                missing=missing,
                project_id=self._project_id,
                pool="async",
                hint=(
                    "Apply migration 002 (002_hnsw_upgrade.sql) to create the HNSW index. "
                    "Until then, vector recall falls back to a sequential scan."
                ),
            )
            _record_missing_indexes(self._project_id)
        return missing

    # ------------------------------------------------------------------
    # Relations
    # ------------------------------------------------------------------

    async def _ensure_relations_table(self) -> None:
        """Async equivalent of the sync init guard.

        Lock is created lazily because it requires a running event loop and
        the backend may be constructed from sync setup code.
        """
        if self._relations_lock is None:
            self._relations_lock = asyncio.Lock()
        async with self._relations_lock:
            if self._relations_ensured:
                return
            async with self._scoped_conn() as conn, conn.cursor() as cur:
                await cur.execute(_sql.PROBE_RELATIONS_TABLE_SQL)
                if await cur.fetchone() is None:
                    await cur.execute(_sql.RELATIONS_DDL)
            self._relations_ensured = True

    async def list_relations(self) -> list[dict[str, Any]]:
        """Return all relations for this ``(project_id, agent_id)`` scope."""
        await self._ensure_relations_table()
        async with self._scoped_conn() as conn, conn.cursor() as cur:
            await cur.execute(_sql.LIST_RELATIONS_SQL, (self._project_id, self._agent_id))
            rows = await cur.fetchall()
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

    async def count_relations(self) -> int:
        """Total relation count for this ``(project_id, agent_id)`` scope."""
        await self._ensure_relations_table()
        async with self._scoped_conn() as conn, conn.cursor() as cur:
            await cur.execute(_sql.COUNT_RELATIONS_SQL, (self._project_id, self._agent_id))
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def save_relations(self, key: str, relations: list[RelationEntry]) -> int:
        """Batch-upsert relations linked to a memory entry key."""
        if not relations:
            return 0
        await self._ensure_relations_table()
        now = datetime.now(tz=UTC).isoformat()
        count = 0
        async with self._scoped_conn() as conn, conn.cursor() as cur:
            for rel in relations:
                source_keys: list[str] = list(dict.fromkeys([*rel.source_entry_keys, key]))
                await cur.execute(
                    _sql.SAVE_RELATION_UPSERT_SQL,
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

    async def load_relations(self, key: str) -> list[dict[str, Any]]:
        """Return relations whose ``source_entry_keys`` contains *key*."""
        return [r for r in await self.list_relations() if key in r["source_entry_keys"]]

    async def delete_relations(self, key: str) -> int:
        """Delete all relations whose ``source_entry_keys`` contains *key*."""
        await self._ensure_relations_table()
        try:
            async with self._scoped_conn() as conn, conn.cursor() as cur:
                await cur.execute(
                    _sql.DELETE_RELATIONS_BY_KEY_SQL,
                    (
                        self._project_id,
                        self._agent_id,
                        json.dumps([key], ensure_ascii=False),
                    ),
                )
                return cur.rowcount or 0
        except Exception:
            logger.warning(
                "async_postgres_private.delete_relations_failed",
                key=key,
                exc_info=True,
            )
            return 0

    # ------------------------------------------------------------------
    # Schema / version
    # ------------------------------------------------------------------

    async def get_schema_version(self) -> int:
        """Return the private-memory schema version."""
        try:
            async with self._scoped_conn() as conn, conn.cursor() as cur:
                await cur.execute(_sql.GET_SCHEMA_VERSION_SQL)
                row = await cur.fetchone()
            return int(row[0]) if row and row[0] is not None else _sql.PRIVATE_SCHEMA_VERSION
        except Exception:
            logger.warning("async_postgres_private.get_schema_version_failed", exc_info=True)
            return _sql.PRIVATE_SCHEMA_VERSION

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------

    async def append_audit(
        self,
        action: str,
        key: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Best-effort audit_log INSERT — failures log and never raise."""
        try:
            async with self._scoped_conn() as conn, conn.cursor() as cur:
                await cur.execute(
                    _sql.APPEND_AUDIT_SQL,
                    (
                        self._project_id,
                        self._agent_id,
                        action,
                        key or "",
                        json.dumps(extra or {}, default=str),
                    ),
                )
        except Exception:
            logger.warning(
                "async_postgres_private.audit_append_failed",
                action=action,
                key=key,
                exc_info=True,
            )

    async def query_audit(
        self,
        *,
        key: str | None = None,
        event_type: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Read entries from ``audit_log`` for this ``(project_id, agent_id)``."""
        stmt, extra_params = _sql.build_query_audit_sql(
            key=key,
            event_type=event_type,
            since=since,
            until=until,
        )
        params: list[Any] = [self._project_id, self._agent_id, *extra_params, limit]
        try:
            async with self._scoped_conn() as conn, conn.cursor() as cur:
                await cur.execute(stmt, params)
                rows = await cur.fetchall()
        except Exception:
            logger.warning("async_postgres_private.audit_query_failed", exc_info=True)
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
    # Flywheel meta
    # ------------------------------------------------------------------

    async def flywheel_meta_get(self, key: str) -> str | None:
        """Best-effort flywheel meta lookup; failures log and return None."""
        try:
            async with self._scoped_conn() as conn, conn.cursor() as cur:
                await cur.execute(
                    _sql.FLYWHEEL_META_GET_SQL,
                    (self._project_id, self._agent_id, key),
                )
                row = await cur.fetchone()
                return str(row[0]) if row else None
        except Exception:
            logger.warning(
                "async_postgres_private.flywheel_meta_get_failed",
                key=key,
                exc_info=True,
            )
            return None

    async def flywheel_meta_set(self, key: str, value: str) -> None:
        """Best-effort flywheel meta upsert; failures log and swallow."""
        try:
            async with self._scoped_conn() as conn, conn.cursor() as cur:
                await cur.execute(
                    _sql.FLYWHEEL_META_SET_SQL,
                    (self._project_id, self._agent_id, key, value),
                )
        except Exception:
            logger.warning(
                "async_postgres_private.flywheel_meta_set_failed",
                key=key,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # GC archive
    # ------------------------------------------------------------------

    async def archive_entry(self, entry: MemoryEntry) -> int:
        """INSERT a GC-evicted entry into ``gc_archive`` and return byte_count.

        Best-effort: logs and returns 0 on failure — GC must not be blocked
        by an archive write error.
        """
        try:
            payload_dict = entry.model_dump()
            payload_json = json.dumps(payload_dict, default=str)
            byte_count = len(payload_json.encode("utf-8"))
            async with self._scoped_conn() as conn, conn.cursor() as cur:
                await cur.execute(
                    _sql.ARCHIVE_ENTRY_SQL,
                    (
                        self._project_id,
                        self._agent_id,
                        entry.key,
                        payload_json,
                        byte_count,
                    ),
                )
            return byte_count
        except Exception:
            logger.warning(
                "async_postgres_private.gc_archive_entry_failed",
                key=entry.key,
                exc_info=True,
            )
            return 0

    async def list_archive(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Return the most recent *limit* rows from ``gc_archive``."""
        try:
            async with self._scoped_conn() as conn, conn.cursor() as cur:
                await cur.execute(
                    _sql.LIST_ARCHIVE_SQL,
                    (self._project_id, self._agent_id, limit),
                )
                rows = await cur.fetchall()
        except Exception:
            logger.warning("async_postgres_private.gc_archive_list_failed", exc_info=True)
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

    async def total_archive_bytes(self) -> int:
        """Return ``SUM(byte_count)`` from ``gc_archive`` for this agent scope."""
        try:
            async with self._scoped_conn() as conn, conn.cursor() as cur:
                await cur.execute(
                    _sql.TOTAL_ARCHIVE_BYTES_SQL,
                    (self._project_id, self._agent_id),
                )
                row = await cur.fetchone()
            return int(row[0]) if row else 0
        except Exception:
            logger.warning(
                "async_postgres_private.gc_archive_total_bytes_failed",
                exc_info=True,
            )
            return 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the underlying async connection pool."""
        try:
            await self._cm.close_async()
        except Exception:
            logger.debug("async_postgres_private.close_failed", exc_info=True)
