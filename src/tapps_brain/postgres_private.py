"""PostgreSQL implementation of PrivateBackend protocol.

EPIC-059 STORY-059.5 — private agent memory wired through Postgres.
All queries are scoped to the ``(project_id, agent_id)`` pair supplied at
construction, replacing per-agent SQLite files (``.tapps-brain/agents/<id>/memory.db``).

STORY-072.2 — every SQL string lives in
:mod:`tapps_brain._postgres_private_sql` so the async backend
(``AsyncPostgresPrivateBackend``) can share the exact same queries.  Only
connection / cursor mechanics live here.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

import structlog

from tapps_brain import _postgres_private_sql as _sql
from tapps_brain.models import (
    LearningStatus,
    MemoryEntry,
    MemoryScope,
    MemorySource,
    MemoryStatus,
    MemoryTier,
    PromotionSignal,
)
from tapps_brain.visual_snapshot import (
    _TOP_TAGS_LIMIT,
    AccessBucket,
    AccessStats,
    SnapshotAggregates,
)

if TYPE_CHECKING:
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
# to emit ``tapps_brain_private_missing_indexes_total``.  Shared between the
# sync and async backends — both increment into the same dict.
_MISSING_INDEX_COUNTS: dict[str, int] = {}
_MISSING_INDEX_COUNTS_LOCK = threading.Lock()


def _coerce_pgvector(raw: Any) -> list[float] | None:
    """Normalize a pgvector / list / string value into ``list[float]``."""
    if raw is None:
        return None
    seq: Any
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("[") and text.endswith("]"):
            text = text[1:-1]
        if not text:
            return []
        seq = text.split(",")
    elif isinstance(raw, (list, tuple)):
        seq = raw
    else:
        try:
            seq = list(raw)
        except TypeError:
            return None
    try:
        return [float(x) for x in seq]
    except (TypeError, ValueError):
        return None


def _parse_jsonb_list(raw: Any) -> list[str]:
    """Parse a JSONB column value into a ``list[str]``.

    Handles the three forms psycopg may return: already-a-list (JSONB
    parsed by the driver), a JSON string (TEXT column fallback), or
    ``None`` / anything else (returns empty list).
    """
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return [str(item) for item in parsed] if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            return []
    return []


def get_missing_index_counts_snapshot() -> dict[str, int]:
    """Return a frozen copy of the per-project missing-index counter.

    Called by :func:`tapps_brain.http_adapter._collect_metrics` to render
    ``tapps_brain_private_missing_indexes_total`` in Prometheus exposition
    format.  Safe to call from any thread.
    """
    with _MISSING_INDEX_COUNTS_LOCK:
        return dict(_MISSING_INDEX_COUNTS)


def _record_missing_indexes(project_id: str) -> None:
    """Increment the per-project missing-index counter.  Thread-safe."""
    with _MISSING_INDEX_COUNTS_LOCK:
        _MISSING_INDEX_COUNTS[project_id] = _MISSING_INDEX_COUNTS.get(project_id, 0) + 1


# ---------------------------------------------------------------------------
# HNSW query-time GUC helpers (TAP-2728)
# ---------------------------------------------------------------------------

# pgvector default ef_search is 40; the HNSW index was built with
# ef_construction=200 (migration 002).  For project/agent-filtered queries,
# iterative_scan requires ef_search >= the number of results you want to
# return per partition.  80 is a 2x safety margin over the default that
# keeps P99 latency acceptable on small-corpus private-memory tables.
_DEFAULT_HNSW_EF_SEARCH: int = 80


def _resolve_hnsw_ef_search() -> int:
    """Return the configured HNSW ef_search value.

    Reads ``TAPPS_BRAIN_HNSW_EF_SEARCH`` from the environment; falls back to
    :data:`_DEFAULT_HNSW_EF_SEARCH` (80).  Raises ``ValueError`` when the env
    var is set to a non-positive integer.
    """
    raw = os.environ.get("TAPPS_BRAIN_HNSW_EF_SEARCH", "")
    if not raw:
        return _DEFAULT_HNSW_EF_SEARCH
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(
            f"TAPPS_BRAIN_HNSW_EF_SEARCH must be a positive integer; got '{raw}'"
        ) from None
    if value < 1:
        raise ValueError(f"TAPPS_BRAIN_HNSW_EF_SEARCH must be >= 1; got {value}")
    return value


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
    ``db_path`` and ``audit_path`` return ``Path("/dev/null")``; ``store_dir``
    returns its parent, ``Path("/dev")``.
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
        # Memoized verify_expected_indexes result (successful probes only) —
        # health polls re-invoke the check per read, see the method docstring.
        self._index_verify_cache: list[str] | None = None

        # TAP-2728: HNSW query-time GUC — read once at construction so env-var
        # parsing is not repeated on every knn_search call.
        self._hnsw_ef_search: int = _resolve_hnsw_ef_search()
        # Set when knn_search hits a DB error so health/metrics can distinguish
        # "no neighbours" from "semantic recall degraded".
        self.knn_search_degraded: bool = False
        self.index_verify_unknown: bool = False

    # ------------------------------------------------------------------
    # Connection helper — enforces tenant RLS (EPIC-069 STORY-069.8)
    # ------------------------------------------------------------------

    def _scoped_conn(self) -> Any:
        """Return a connection-context bound to this store's project_id.

        Delegates to :meth:`PostgresConnectionManager.project_context`,
        which runs a session-level ``SET app.project_id`` so the RLS
        policies on ``private_memories`` (migration 009) restrict every
        read and write to this tenant.  The identity survives the
        transaction by design (TAP-514 moved away from ``SET LOCAL``);
        cross-borrow leakage is prevented by the pool's ``reset``
        callback, which clears the GUC before the connection is reused.

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
        params = _sql.build_save_params(
            entry=entry,
            project_id=self._project_id,
            agent_id=self._agent_id,
        )
        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.execute(_sql.SAVE_UPSERT_SQL, params)

        logger.debug(
            "postgres_private.saved",
            project_id=self._project_id,
            agent_id=self._agent_id,
            key=entry.key,
        )

    def save_many(self, entries: list[MemoryEntry]) -> None:
        """Batch-upsert multiple entries in a single pipelined round-trip (TAP-2800).

        Reuses :data:`SAVE_UPSERT_SQL` and :func:`build_save_params` verbatim so
        the column list cannot drift from :meth:`save`.  psycopg 3 routes
        ``executemany`` through pipeline mode internally (since 3.1), coalescing
        the N upserts into far fewer client/server round-trips than calling
        :meth:`save` N times.  All rows run inside one transaction, so a failure
        rolls the whole batch back — callers pre-validate per row, so only valid
        entries reach this method.
        """
        if not entries:
            return
        params_seq = [
            _sql.build_save_params(
                entry=entry,
                project_id=self._project_id,
                agent_id=self._agent_id,
            )
            for entry in entries
        ]
        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.executemany(_sql.SAVE_UPSERT_SQL, params_seq)

        logger.debug(
            "postgres_private.saved_many",
            project_id=self._project_id,
            agent_id=self._agent_id,
            count=len(entries),
        )

    def load_all(self, *, limit: int | None = None) -> list[MemoryEntry]:
        """Load entries for this ``(project_id, agent_id)`` scope.

        Used by :class:`MemoryStore` on cold-start to populate the in-memory cache.
        Consumes rows in chunks of 1 000.  Note psycopg's default client-side
        cursor buffers the whole result set at ``execute()`` time, so chunking
        only bounds Python ``MemoryEntry`` construction — not raw-row memory
        (a named/server-side cursor would be required for true streaming).
        Pass *limit* to apply an early-cutoff after the most-recently-updated
        entries have been collected (entries are ordered by ``updated_at DESC``
        so callers that honour a max-entries cap can stop early instead of
        building stale rows that would be evicted anyway).

        Args:
            limit: Maximum number of entries to return.  ``None`` means no cap.
        """
        chunk_size = 1000
        results: list[MemoryEntry] = []
        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.execute(_sql.LOAD_ALL_SQL, (self._project_id, self._agent_id))
            col_names = [desc[0] for desc in cur.description]
            while True:
                chunk = cur.fetchmany(chunk_size)
                if not chunk:
                    break
                for row in chunk:
                    results.append(self._row_to_entry(dict(zip(col_names, row, strict=False))))
                    if limit is not None and len(results) >= limit:
                        return results
        return results

    def load_one(self, key: str) -> MemoryEntry | None:
        """Load a single entry by key for this ``(project_id, agent_id)`` scope.

        Used to hydrate the write-through cache when rows were written outside
        ``MemoryStore.save`` (e.g. experience-event upserts).
        """
        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.execute(_sql.LOAD_ONE_SQL, (self._project_id, self._agent_id, key))
            row = cur.fetchone()
            if row is None:
                return None
            col_names = [desc[0] for desc in cur.description]
        return self._row_to_entry(dict(zip(col_names, row, strict=False)))

    def delete(self, key: str) -> bool:
        """Delete an entry by key.  Returns ``True`` if a row was removed."""
        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.execute(_sql.DELETE_BY_KEY_SQL, (self._project_id, self._agent_id, key))
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
        memory_class: str | None = None,
        include_expired: bool = False,
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
            memory_class: TAP-733 — when set, restrict results to entries with this
                semantic class value.  Pushed into SQL WHERE for DB-level filtering.
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
            include_expired=include_expired,
        )
        params: list[Any] = [query, self._project_id, self._agent_id, query, *extra_params]

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

    def knn_search(
        self,
        query_embedding: list[float],
        k: int,
        *,
        include_expired: bool = False,
        as_of: str | None = None,
    ) -> list[tuple[str, float]]:
        """Approximate nearest-neighbour search via pgvector cosine distance.

        Uses the ``idx_priv_embedding_hnsw`` index (migration 002).  Returns
        ``(key, distance)`` pairs, lowest distance first, or ``[]`` for an
        empty *query_embedding*.  DB errors set ``knn_search_degraded`` and
        re-raise to the caller.

        TAP-2728: sets ``hnsw.iterative_scan = 'relaxed_order'`` and a tuned
        ``hnsw.ef_search`` before the query so project/agent-filtered searches
        are not silently truncated by the pgvector default (ef=40).  Both GUCs
        use ``SET LOCAL`` so they are transaction-scoped and cannot leak to
        other queries on the same pooled connection.

        *as_of* applies the same bi-temporal window as FTS search and stands
        the live-row predicate down so point-in-time hybrid recall can rank
        versions that were valid then.
        """
        if not query_embedding:
            return []

        vec_str = "[" + ",".join(str(v) for v in query_embedding) + "]"
        knn_sql, mid_params = _sql.build_knn_search_sql(
            include_expired=include_expired, as_of=as_of
        )
        try:
            with self._scoped_conn() as conn, conn.cursor() as cur:
                # TAP-2728: HNSW GUCs for filtered recall correctness.
                # SET LOCAL is transaction-scoped — resets at commit/rollback,
                # so it cannot leak across pool borrows.
                cur.execute("SET LOCAL hnsw.iterative_scan = 'relaxed_order'")
                cur.execute(f"SET LOCAL hnsw.ef_search = {self._hnsw_ef_search:d}")
                cur.execute(
                    knn_sql,
                    (vec_str, self._project_id, self._agent_id, *mid_params, k),
                )
                rows = cur.fetchall()
            # A successful query clears the degraded latch: the flag reflects
            # the *most recent* attempt, not "any error ever" — otherwise one
            # transient blip (DB restart, pool timeout) permanently reports
            # the vector index as down for the life of the process.
            self.knn_search_degraded = False
            return [(str(r[0]), float(r[1])) for r in rows]
        except Exception:
            self.knn_search_degraded = True
            logger.warning("postgres_private.knn_search_failed", exc_info=True)
            raise

    def vector_row_count(self) -> int:
        """Number of entries with a non-NULL embedding vector."""
        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.execute(_sql.VECTOR_ROW_COUNT_SQL, (self._project_id, self._agent_id))
            row = cur.fetchone()
        return int(row[0]) if row else 0

    def keys_missing_embedding(self) -> list[str]:
        """Keys whose durable row has a NULL embedding (backfill candidates).

        ``load_all``/``load_one`` deliberately never hydrate the ``embedding``
        column, so the in-memory field cannot distinguish "row has no vector"
        from "vector exists but was not loaded" — this query can.
        """
        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.execute(_sql.KEYS_MISSING_EMBEDDING_SQL, (self._project_id, self._agent_id))
            rows = cur.fetchall()
        return [str(r[0]) for r in rows]

    def load_embeddings(self) -> dict[str, dict[str, Any]]:
        """Return ``{key: {vector, embedding_model_id}}`` for non-NULL vectors.

        Used by lossless export sidecars (TAP-5030).  Normal entry loads still
        omit the dense column for size; this is the deliberate opt-in path.
        """
        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.execute(_sql.LOAD_EMBEDDINGS_SQL, (self._project_id, self._agent_id))
            rows = cur.fetchall()
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = str(row[0])
            raw_vec = row[1]
            model_id = row[2]
            vector = _coerce_pgvector(raw_vec)
            if vector is None:
                continue
            out[key] = {
                "vector": vector,
                "embedding_model_id": str(model_id) if model_id is not None else None,
            }
        return out

    def snapshot_aggregates(self, project_id: str) -> SnapshotAggregates:
        """Return visual-snapshot rollups without hydrating full memory rows."""
        if project_id != self._project_id:
            msg = (
                f"snapshot_aggregates project_id mismatch: "
                f"expected {self._project_id!r}, got {project_id!r}"
            )
            raise ValueError(msg)

        scope = (self._project_id, self._agent_id)
        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.execute(_sql.SNAPSHOT_ACCESS_STATS_SQL, scope)
            access_row = cur.fetchone()

            cur.execute(_sql.SNAPSHOT_TIER_COUNTS_SQL, scope)
            tier_rows = cur.fetchall()

            cur.execute(_sql.SNAPSHOT_AGENT_SCOPE_COUNTS_SQL, scope)
            scope_rows = cur.fetchall()

            cur.execute(_sql.SNAPSHOT_MEMORY_GROUP_COUNTS_SQL, scope)
            group_rows = cur.fetchall()

            cur.execute(_sql.SNAPSHOT_TAG_COUNTS_SQL, (*scope, _TOP_TAGS_LIMIT))
            tag_rows = cur.fetchall()

        # An ungrouped aggregate query always returns exactly one row (all
        # COALESCE/COUNT expressions yield 0 on an empty table); execute()
        # errors raise before reaching here.
        assert access_row is not None  # noqa: S101 — SQL aggregate invariant
        sum_ac = int(access_row[0] or 0)
        sum_total = int(access_row[1] or 0)
        sum_useful = int(access_row[2] or 0)
        with_access = int(access_row[3] or 0)
        b0 = int(access_row[4] or 0)
        b1 = int(access_row[5] or 0)
        b2 = int(access_row[6] or 0)
        b3 = int(access_row[7] or 0)
        total = int(access_row[8] or 0)
        access_stats = AccessStats(
            sum_access_count=sum_ac,
            mean_access_count=round(sum_ac / total, 4) if total else 0.0,
            entries_with_access=with_access,
            sum_total_access_count=sum_total,
            sum_useful_access_count=sum_useful,
            buckets=[
                AccessBucket(label="0", count=b0),
                AccessBucket(label="1-5", count=b1),
                AccessBucket(label="6-20", count=b2),
                AccessBucket(label="21+", count=b3),
            ],
        )

        tier_distribution = {str(row[0]): int(row[1]) for row in tier_rows}
        agent_scope_counts = {
            (str(row[0]) if row[0] is not None else "private"): int(row[1]) for row in scope_rows
        }
        memory_group_counts = {str(row[0]): int(row[1]) for row in group_rows}
        tag_counts = {str(row[0]): int(row[1]) for row in tag_rows}

        return SnapshotAggregates(
            tier_distribution=dict(sorted(tier_distribution.items())),
            agent_scope_counts=dict(sorted(agent_scope_counts.items())),
            access_stats=access_stats,
            memory_group_counts=memory_group_counts,
            tag_counts=tag_counts,
        )

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

        This is a best-effort check — any DB error is caught and logged.
        On probe failure the sentinel ``__index_verify_unavailable__`` is
        returned (and ``index_verify_unknown`` set) so callers do **not**
        treat a failed probe as "all indexes present".

        Likely cause of a non-empty result: migration 002 (HNSW upgrade) was
        never applied.  The embedding recall path still works but falls back
        to a sequential scan, degrading latency.

        .. note::
            Call once at ``MemoryStore.__init__`` after :meth:`load_all`.
            Successful probe results are memoized: consumers re-invoke this
            per health poll (``MemoryStore.vector_index_enabled``), and
            without the cache every poll re-runs the ``pg_indexes`` query,
            re-warns, and inflates ``tapps_brain_private_missing_indexes_total``
            — a counter documented as counting *startup checks*.  Probe
            failures are never cached so a later call can recover.
        """
        if self._index_verify_cache is not None:
            return list(self._index_verify_cache)
        try:
            with self._scoped_conn() as conn, conn.cursor() as cur:
                cur.execute(_sql.LIST_TABLE_INDEXES_SQL)
                present = {str(row[0]) for row in cur.fetchall()}
        except Exception:
            self.index_verify_unknown = True
            logger.warning(
                "postgres_private.verify_expected_indexes.db_error",
                exc_info=True,
                hint="index status unknown — not treating as healthy",
            )
            return ["__index_verify_unavailable__"]

        missing = sorted(_sql.EXPECTED_PRIVATE_INDEXES - present)
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
            _record_missing_indexes(self._project_id)
        self._index_verify_cache = list(missing)
        return missing

    # ------------------------------------------------------------------
    # Relations
    # ------------------------------------------------------------------

    def _ensure_relations_table(self) -> None:
        """Create ``private_relations`` if it does not yet exist (idempotent).

        Probes ``pg_class`` first so a non-DDL role (``tapps_runtime``) that
        has only USAGE on ``public`` but not CREATE can still pass this
        check when the table was pre-created by the migrate sidecar —
        Postgres evaluates schema-CREATE before the ``IF NOT EXISTS``
        short-circuit, so the bare CREATE fails even when the table exists.
        """
        with self._lock:
            if self._relations_ensured:
                return
            with self._scoped_conn() as conn, conn.cursor() as cur:
                cur.execute(_sql.PROBE_RELATIONS_TABLE_SQL)
                if cur.fetchone() is None:
                    cur.execute(_sql.RELATIONS_DDL)
            self._relations_ensured = True

    def list_relations(self) -> list[dict[str, Any]]:
        """Return all relations for this ``(project_id, agent_id)`` scope."""
        self._ensure_relations_table()
        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.execute(_sql.LIST_RELATIONS_SQL, (self._project_id, self._agent_id))
            rows = cur.fetchall()
        if not rows:
            return []

        results: list[dict[str, Any]] = []
        for r in rows:
            # _parse_jsonb_list also guards against non-list JSON payloads,
            # which the previous inline parser let through untyped.
            keys = _parse_jsonb_list(r[3])
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
            cur.execute(_sql.COUNT_RELATIONS_SQL, (self._project_id, self._agent_id))
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
        params_seq = [
            (
                self._project_id,
                self._agent_id,
                rel.subject,
                rel.predicate,
                rel.object_entity,
                json.dumps(list(dict.fromkeys([*rel.source_entry_keys, key])), ensure_ascii=False),
                rel.confidence,
                now,
            )
            for rel in relations
        ]
        # executemany routes through psycopg's pipeline mode (same rationale
        # as save_many, TAP-2800) — one round-trip batch instead of N.
        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.executemany(_sql.SAVE_RELATION_UPSERT_SQL, params_seq)
        return len(params_seq)

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
                "postgres_private.delete_relations_failed",
                key=key,
                exc_info=True,
            )
            raise

    # ------------------------------------------------------------------
    # Schema / version
    # ------------------------------------------------------------------

    def get_schema_version(self) -> int:
        """Return the private-memory schema version (from ``private_schema_version``)."""
        try:
            with self._scoped_conn() as conn, conn.cursor() as cur:
                cur.execute(_sql.GET_SCHEMA_VERSION_SQL)
                row = cur.fetchone()
            return int(row[0]) if row and row[0] is not None else 0
        except Exception:
            logger.warning("postgres_private.get_schema_version_failed", exc_info=True)
            raise

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
        newest_first: bool = False,
    ) -> list[dict[str, Any]]:
        """Read entries from ``audit_log`` for this ``(project_id, agent_id)``.

        Returns dicts with ``timestamp`` (ISO-8601 string), ``event_type``,
        ``key``, and ``details``.  Ordered oldest-to-newest by default;
        ``newest_first=True`` orders newest-to-oldest so a limited query
        returns the most recent rows instead of the oldest.
        """
        stmt, extra_params = _sql.build_query_audit_sql(
            key=key,
            event_type=event_type,
            since=since,
            until=until,
            newest_first=newest_first,
        )
        params: list[Any] = [self._project_id, self._agent_id, *extra_params, limit]
        try:
            with self._scoped_conn() as conn, conn.cursor() as cur:
                cur.execute(stmt, params)
                rows = cur.fetchall()
        except Exception:
            logger.warning("postgres_private.audit_query_failed", exc_info=True)
            raise

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

    def count_audit(
        self,
        *,
        key: str | None = None,
        event_type: str | None = None,
    ) -> int:
        """Exact matching-row count over ``audit_log`` (no LIMIT cap)."""
        stmt, extra_params = _sql.build_count_audit_sql(key=key, event_type=event_type)
        params: list[Any] = [self._project_id, self._agent_id, *extra_params]
        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.execute(stmt, params)
            row = cur.fetchone()
        return int(row[0]) if row else 0

    # ------------------------------------------------------------------
    # Flywheel metadata (migration 007, STORY-066.14)
    # ------------------------------------------------------------------

    def flywheel_meta_get(self, key: str) -> str | None:
        """Return the stored flywheel metadata value for *key*, or ``None``.

        Best-effort empty result when the key is absent.  Postgres failures
        raise so callers (e.g. flywheel) do not treat a failed cursor read as
        "start from beginning" and double-apply confidence updates.
        """
        try:
            with self._scoped_conn() as conn, conn.cursor() as cur:
                cur.execute(_sql.FLYWHEEL_META_GET_SQL, (self._project_id, self._agent_id, key))
                row = cur.fetchone()
                return str(row[0]) if row else None
        except Exception:
            logger.warning("postgres_private.flywheel_meta_get_failed", key=key, exc_info=True)
            raise

    def flywheel_meta_set(self, key: str, value: str) -> None:
        """Upsert a flywheel metadata value for *key*.

        Failures are logged and re-raised so callers (e.g. the flywheel
        confidence pipeline) do not advance a cursor after a failed durable write.
        """
        try:
            with self._scoped_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    _sql.FLYWHEEL_META_SET_SQL,
                    (self._project_id, self._agent_id, key, value),
                )
        except Exception:
            logger.warning("postgres_private.flywheel_meta_set_failed", key=key, exc_info=True)
            raise

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
                    _sql.ARCHIVE_ENTRY_SQL,
                    (
                        self._project_id,
                        self._agent_id,
                        entry.key,
                        payload_json,
                        byte_count,
                    ),
                )
        except Exception:
            logger.warning(
                "postgres_private.gc_archive_entry_failed",
                key=entry.key,
                exc_info=True,
            )
            return 0
        return byte_count

    def list_archive(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Return the most recent *limit* rows from ``gc_archive``."""
        try:
            with self._scoped_conn() as conn, conn.cursor() as cur:
                cur.execute(_sql.LIST_ARCHIVE_SQL, (self._project_id, self._agent_id, limit))
                rows = cur.fetchall()
        except Exception:
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
                cur.execute(_sql.TOTAL_ARCHIVE_BYTES_SQL, (self._project_id, self._agent_id))
                row = cur.fetchone()
            return int(row[0]) if row else 0
        except Exception:
            logger.warning("postgres_private.gc_archive_total_bytes_failed", exc_info=True)
            return 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying connection pool."""
        try:
            self._cm.close()
        except Exception:
            logger.debug("postgres_private.close_failed", exc_info=True)  # nosec B110 — best-effort close; errors must not propagate

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_entry(row: dict[str, Any]) -> MemoryEntry:
        """Convert a Postgres row dict to a :class:`MemoryEntry`."""
        # Tags — stored as JSONB (may arrive as list or JSON string).
        tags = _parse_jsonb_list(row.get("tags"))

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

        # TAP-732: lifecycle status
        status_raw = str(row.get("status") or "active")
        try:
            mem_status = MemoryStatus(status_raw)
        except ValueError:
            mem_status = MemoryStatus.active

        # TAP-5542: promotion status.  An unreadable value falls back to
        # ``candidate``, never ``approved`` — a row whose trust state cannot be
        # parsed has not demonstrably passed a gate.
        try:
            learning_status = LearningStatus(str(row.get("learning_status") or "candidate"))
        except ValueError:
            learning_status = LearningStatus.candidate

        try:
            promotion_signal = (
                PromotionSignal(str(row["promotion_signal"]))
                if row.get("promotion_signal") is not None
                else None
            )
        except ValueError:
            promotion_signal = None

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
            mission_id=_str_or_none(row.get("mission_id")),
            run_id=_str_or_none(row.get("run_id")),
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
            integrity_hash_v=int(row.get("integrity_hash_v") or 1),
            embedding_model_id=_str_or_none(row.get("embedding_model_id")),
            temporal_sensitivity=cast(
                "Literal['high', 'medium', 'low'] | None",
                _str_or_none(row.get("temporal_sensitivity")),
            ),
            failed_approaches=_parse_jsonb_list(row.get("failed_approaches")),
            status=mem_status,
            stale_reason=_str_or_none(row.get("stale_reason")),
            stale_date=_iso_or_none(row.get("stale_date")),
            learning_status=learning_status,
            promoted_by=_str_or_none(row.get("promoted_by")),
            promoted_at=_iso_or_none(row.get("promoted_at")),
            promotion_signal=promotion_signal,
            demotion_reason=_str_or_none(row.get("demotion_reason")),
            memory_class=cast(
                "Literal['incident', 'guidance', 'decision', 'convention'] | None",
                _str_or_none(row.get("memory_class")),
            ),
            # embedding is not loaded from DB (large binary; on-demand via knn_search)
        )


def __getattr__(name: str) -> object:
    """Lazy re-export of the canonical async backend (TAP: single source).

    The async-native backend lives in :mod:`tapps_brain.async_postgres_private`;
    an embedded near-duplicate used to live here and drifted (missing
    ``save_many``, stale error handling).  Keep the old import path working
    without maintaining two implementations.  Lazy so importing this module
    does not create an import cycle (``async_postgres_private`` imports from
    this module).
    """
    if name == "AsyncPostgresPrivateBackend":
        from tapps_brain.async_postgres_private import AsyncPostgresPrivateBackend

        return AsyncPostgresPrivateBackend
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
