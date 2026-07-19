"""SQL constants and query builders shared by the sync + async private backends.

Both :class:`tapps_brain.postgres_private.PostgresPrivateBackend` (sync) and
:class:`tapps_brain.async_postgres_private.AsyncPostgresPrivateBackend`
(STORY-072.2) import every SQL string from this module so a single query
fix lands in both backends at once.

Why strings (not ``psycopg.sql.SQL`` objects):

- Sync and async cursors both accept ``str`` execute() arguments identically.
- Static strings are cheap to test against migration files.
- The two dynamic builders (:func:`build_search_sql`, :func:`build_query_audit_sql`)
  return either a string or a ``Composable`` for the cases where conditional
  WHERE clauses or trusted column-name interpolation are required.

Naming: ``<DOMAIN>_<OPERATION>_SQL``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from psycopg.sql import Composable

    from tapps_brain.models import MemoryEntry

# ---------------------------------------------------------------------------
# Schema metadata
# ---------------------------------------------------------------------------

#: Bootstrap seed from migration ``001_initial.sql``. Never treat this as the
#: live "current" schema version — call sites that cannot read the version
#: table should report ``0`` (unknown) instead.
PRIVATE_SCHEMA_BOOTSTRAP_VERSION = 1
# Back-compat alias for older imports/tests.
PRIVATE_SCHEMA_VERSION = PRIVATE_SCHEMA_BOOTSTRAP_VERSION

#: Valid ``time_field`` values for temporal filtering on private_memories.
VALID_TIME_FIELDS: frozenset[str] = frozenset({"created_at", "updated_at", "last_accessed"})

#: Index names that must exist on ``private_memories`` after migration 002.
EXPECTED_PRIVATE_INDEXES: frozenset[str] = frozenset({"idx_priv_embedding_hnsw"})


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

#: DDL for the ``private_relations`` auxiliary table.  Created on first use.
RELATIONS_DDL = """\
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


# ---------------------------------------------------------------------------
# Core CRUD — private_memories
# ---------------------------------------------------------------------------

SAVE_UPSERT_SQL = """
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
    integrity_hash, integrity_hash_v, embedding_model_id,
    temporal_sensitivity,
    failed_approaches,
    status,
    stale_reason,
    stale_date,
    memory_class,
    embedding
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
    %s, %s, %s,
    %s,
    %s::jsonb,
    %s, %s, %s,
    %s,
    %s::vector
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
    integrity_hash_v         = EXCLUDED.integrity_hash_v,
    embedding_model_id       = CASE
        WHEN EXCLUDED.embedding IS NOT NULL THEN EXCLUDED.embedding_model_id
        ELSE private_memories.embedding_model_id
    END,
    temporal_sensitivity     = EXCLUDED.temporal_sensitivity,
    failed_approaches        = EXCLUDED.failed_approaches,
    status                   = EXCLUDED.status,
    stale_reason             = EXCLUDED.stale_reason,
    stale_date               = EXCLUDED.stale_date,
    memory_class             = EXCLUDED.memory_class,
    embedding                = COALESCE(EXCLUDED.embedding, private_memories.embedding)
"""


def embedding_to_pgvector(embedding: list[float] | None) -> str | None:
    """Format a dense vector as a pgvector text literal, or ``None`` for NULL.

    psycopg has no vector adapter registered, so we pass the textual
    ``[v1,v2,...]`` form and cast it with ``%s::vector`` in the SQL — the
    same shape ``knn_search`` already uses for the query vector.
    """
    if not embedding:
        return None
    return "[" + ",".join(str(float(v)) for v in embedding) + "]"


def build_save_params(
    *,
    entry: MemoryEntry,
    project_id: str,
    agent_id: str,
) -> tuple[Any, ...]:
    """Return the parameter tuple for :data:`SAVE_UPSERT_SQL`.

    Co-located with the SQL constant so adding a column needs both edits
    in this module — keeps the column list and the attribute list from
    drifting between the sync and async backends.
    """
    tier = entry.tier.value if hasattr(entry.tier, "value") else str(entry.tier)
    source = entry.source.value if hasattr(entry.source, "value") else str(entry.source)
    scope = entry.scope.value if hasattr(entry.scope, "value") else str(entry.scope)
    status = entry.status.value if hasattr(entry.status, "value") else str(entry.status)
    return (
        project_id,
        agent_id,
        entry.key,
        entry.value,
        tier,
        entry.confidence,
        source,
        entry.source_agent,
        scope,
        entry.agent_scope,
        entry.memory_group,
        json.dumps(entry.tags, ensure_ascii=False),
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
        entry.integrity_hash_v,
        entry.embedding_model_id,
        entry.temporal_sensitivity,
        json.dumps(entry.failed_approaches, ensure_ascii=False),
        status,
        entry.stale_reason,
        entry.stale_date,
        getattr(entry, "memory_class", None),
        embedding_to_pgvector(entry.embedding),
    )


#: Explicit column list for row-to-entry hydration.  Deliberately excludes
#: ``embedding`` (dense vector, hundreds of bytes to KBs per row — never read
#: by ``_row_to_entry``; semantic recall fetches it on demand via
#: ``knn_search``) and the trigger-maintained ``search_vector``.  ``SELECT *``
#: previously shipped both over the wire on every load/search only for Python
#: to discard them.
ENTRY_COLUMNS_SQL = (
    "key, value, tier, confidence, source, source_agent, "
    "scope, agent_scope, memory_group, tags, "
    "created_at, updated_at, last_accessed, "
    "access_count, useful_access_count, total_access_count, "
    "branch, last_reinforced, reinforce_count, "
    "contradicted, contradiction_reason, seeded_from, "
    "valid_at, invalid_at, superseded_by, "
    "valid_from, valid_until, "
    "source_session_id, source_channel, source_message_id, triggered_by, "
    "stability, difficulty, "
    "positive_feedback_count, negative_feedback_count, "
    "integrity_hash, integrity_hash_v, embedding_model_id, "
    "temporal_sensitivity, failed_approaches, "
    "status, stale_reason, stale_date, memory_class"
)

LOAD_ALL_SQL = (
    f"SELECT {ENTRY_COLUMNS_SQL} FROM private_memories"
    " WHERE project_id = %s AND agent_id = %s"
    " ORDER BY updated_at DESC"
)

LOAD_ONE_SQL = (
    f"SELECT {ENTRY_COLUMNS_SQL} FROM private_memories"
    " WHERE project_id = %s AND agent_id = %s AND key = %s LIMIT 1"
)

DELETE_BY_KEY_SQL = (
    "DELETE FROM private_memories WHERE project_id = %s AND agent_id = %s AND key = %s"
)


# ---------------------------------------------------------------------------
# Search (FTS) — base + composable filter snippets
# ---------------------------------------------------------------------------

_SEARCH_BASE_SQL = (
    f"SELECT {ENTRY_COLUMNS_SQL}, "
    "ts_rank(search_vector, plainto_tsquery('english', %s)) AS _rank "
    "FROM private_memories "
    "WHERE project_id = %s AND agent_id = %s "
    "  AND search_vector @@ plainto_tsquery('english', %s)"
)

#: TAP-4586 — live-row predicate pushed into recall SQL so expired
#: (``valid_until`` in the past) and superseded (``superseded_by`` set) rows
#: never occupy a top-K slot when live rows exist.  Mirrors the canonical
#: Python gate in :meth:`tapps_brain.models.MemoryEntry.is_temporally_valid`
#: (kept as defense-in-depth in ``retrieval.py``).
#:
#: ``valid_until`` is ``TEXT NOT NULL DEFAULT ''`` (migration 001), so a live
#: row is the empty string (or NULL, defensively); only a non-empty timestamp
#: in the past marks the row expired.  Uses ``now()`` and ``IS NULL`` — no
#: bound parameters — so it composes into existing param tuples unchanged.
#: Deliberately touches NO tenant/RLS predicate (``project_id``/``agent_id``).
#:
#: The ``::timestamptz`` cast is guarded by a CASE + shape regex: ``valid_until``
#: is free text (unvalidated ``str`` on ``MemoryEntry``), and a bare cast would
#: abort the *entire* recall query for the tenant the moment one row holds a
#: non-timestamp value (e.g. ``"never"``).  CASE guarantees the cast is only
#: evaluated for timestamp-shaped values; non-conforming values are treated as
#: expired, mirroring ``MemoryEntry.is_temporally_valid`` which returns False
#: for unparseable ``valid_until`` instead of raising.
_LIVE_ROW_PREDICATE_SQL = (
    " AND superseded_by IS NULL"
    " AND ("
    "valid_until IS NULL OR valid_until = ''"
    " OR (CASE WHEN valid_until ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'"
    " THEN valid_until::timestamptz > now() ELSE false END)"
    ")"
)
_SEARCH_FILTER_MEMORY_GROUP_SQL = " AND memory_group = %s"
_SEARCH_FILTER_MEMORY_CLASS_SQL = " AND memory_class = %s"
_SEARCH_FILTER_AS_OF_SQL = (
    " AND (valid_at IS NULL OR valid_at <= %s::timestamptz)"
    " AND (invalid_at IS NULL OR invalid_at > %s::timestamptz)"
)
_SEARCH_ORDER_LIMIT_SQL = " ORDER BY _rank DESC LIMIT 100"


def build_search_sql(
    *,
    memory_group: str | None,
    since: str | None,
    until: str | None,
    time_field: str,
    memory_class: str | None,
    as_of: str | None,
    include_expired: bool = False,
) -> tuple[str, list[Any]]:
    """Compose the FTS search SQL + the variable-portion params.

    Returns ``(sql, extra_params)``.  Caller must prepend the fixed
    ``[query, project_id, agent_id, query]`` head to ``extra_params``
    before executing — those four parameters are the same in every
    invocation and stay caller-side.

    *time_field* is interpolated with an f-string (not ``%s``) because
    Postgres prepared statements cannot parameterise column names.  The
    value is validated against :data:`VALID_TIME_FIELDS` first so the
    interpolation is safe.

    *include_expired* (TAP-4586): when ``False`` (default), the live-row
    predicate (:data:`_LIVE_ROW_PREDICATE_SQL`) is pushed into the WHERE so
    expired/superseded rows never consume a top-K slot.  Pass ``True`` from
    the ``include_historical`` / ``include_superseded`` recall paths so those
    callers still see stale rows (the Python filter then decides).  The
    predicate adds no bound params, so it never perturbs *extra_params*.

    The live-row predicate is also suppressed when *as_of* is set: a
    point-in-time query resolves temporal validity through the bi-temporal
    ``valid_at``/``invalid_at`` window, and a superseded row is *expected* for
    the timestamp at which it was valid.

    Raises:
        ValueError: if *time_field* is not in :data:`VALID_TIME_FIELDS`.
    """
    if time_field not in VALID_TIME_FIELDS:
        msg = f"time_field must be one of {sorted(VALID_TIME_FIELDS)}, got {time_field!r}"
        raise ValueError(msg)

    sql = _SEARCH_BASE_SQL
    params: list[Any] = []

    if memory_group is not None:
        sql += _SEARCH_FILTER_MEMORY_GROUP_SQL
        params.append(memory_group)
    if since is not None:
        sql += f" AND {time_field} >= %s"
        params.append(since)
    if until is not None:
        sql += f" AND {time_field} < %s"
        params.append(until)
    if memory_class is not None:
        sql += _SEARCH_FILTER_MEMORY_CLASS_SQL
        params.append(memory_class)
    if as_of is not None:
        sql += _SEARCH_FILTER_AS_OF_SQL
        params.extend([as_of, as_of])
    # TAP-4586: exclude expired/superseded rows from top-K — but NOT when a
    # point-in-time (`as_of`) query is running.  For `as_of`, the bi-temporal
    # `valid_at`/`invalid_at` window (added just above) is the authoritative
    # gate; a superseded row is *expected* to be returned for the timestamp at
    # which it was still valid, so the live-row predicate must stand down.
    if not include_expired and as_of is None:
        sql += _LIVE_ROW_PREDICATE_SQL

    sql += _SEARCH_ORDER_LIMIT_SQL
    return sql, params


# ---------------------------------------------------------------------------
# Vector similarity
# ---------------------------------------------------------------------------

_KNN_SEARCH_HEAD_SQL = (
    "SELECT key, embedding <=> %s::vector AS distance "
    "FROM private_memories "
    "WHERE project_id = %s AND agent_id = %s AND embedding IS NOT NULL"
)
_KNN_SEARCH_TAIL_SQL = " ORDER BY distance LIMIT %s"

#: TAP-4586: KNN recall SQL with the live-row predicate baked in (the default
#: recall path).  Kept as a module constant for callers/tests that want the
#: budget-honest query without invoking :func:`build_knn_search_sql`.
KNN_SEARCH_SQL = _KNN_SEARCH_HEAD_SQL + _LIVE_ROW_PREDICATE_SQL + _KNN_SEARCH_TAIL_SQL


def build_knn_search_sql(*, include_expired: bool = False) -> str:
    """Compose the KNN recall SQL (TAP-4586).

    When *include_expired* is ``False`` (default), the live-row predicate is
    pushed into the WHERE so expired/superseded rows do not occupy a top-K
    slot when live rows exist.  Pass ``True`` from the ``include_historical``
    recall path so stale rows still reach the Python filter.

    The predicate adds no bound parameters, so the caller's positional
    ``(vec, project_id, agent_id, k)`` tuple is unchanged in either case.
    Tenant/RLS predicates (``project_id`` / ``agent_id``) are untouched.
    """
    if include_expired:
        return _KNN_SEARCH_HEAD_SQL + _KNN_SEARCH_TAIL_SQL
    return KNN_SEARCH_SQL


VECTOR_ROW_COUNT_SQL = (
    "SELECT COUNT(*) FROM private_memories "
    "WHERE project_id = %s AND agent_id = %s AND embedding IS NOT NULL"
)

KEYS_MISSING_EMBEDDING_SQL = (
    "SELECT key FROM private_memories "
    "WHERE project_id = %s AND agent_id = %s AND embedding IS NULL"
)


# ---------------------------------------------------------------------------
# Index sanity check
# ---------------------------------------------------------------------------

LIST_TABLE_INDEXES_SQL = (
    "SELECT indexname FROM pg_indexes "
    "WHERE tablename = 'private_memories' AND schemaname = 'public'"
)


# ---------------------------------------------------------------------------
# Relations
# ---------------------------------------------------------------------------

PROBE_RELATIONS_TABLE_SQL = (
    "SELECT 1 FROM pg_class c "
    "JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = 'public' AND c.relname = 'private_relations' "
    "LIMIT 1"
)

LIST_RELATIONS_SQL = (
    "SELECT subject, predicate, object_entity, "
    "       source_entry_keys, confidence, created_at "
    "FROM private_relations "
    "WHERE project_id = %s AND agent_id = %s"
)

COUNT_RELATIONS_SQL = (
    "SELECT COUNT(*) FROM private_relations WHERE project_id = %s AND agent_id = %s"
)

SAVE_RELATION_UPSERT_SQL = """
INSERT INTO private_relations
    (project_id, agent_id, subject, predicate, object_entity,
     source_entry_keys, confidence, created_at)
VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s)
ON CONFLICT (project_id, agent_id, subject, predicate, object_entity)
DO UPDATE SET
    source_entry_keys = EXCLUDED.source_entry_keys,
    confidence        = EXCLUDED.confidence
"""

DELETE_RELATIONS_BY_KEY_SQL = """
DELETE FROM private_relations
WHERE project_id = %s
  AND agent_id   = %s
  AND source_entry_keys::jsonb @> %s::jsonb
"""


# ---------------------------------------------------------------------------
# Schema version
# ---------------------------------------------------------------------------

GET_SCHEMA_VERSION_SQL = "SELECT MAX(version) FROM private_schema_version"


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

APPEND_AUDIT_SQL = """
INSERT INTO audit_log
    (project_id, agent_id, event_type, key, details)
VALUES (%s, %s, %s, %s, %s::jsonb)
"""


def build_query_audit_sql(
    *,
    key: str | None,
    event_type: str | None,
    since: str | None,
    until: str | None,
) -> tuple[Composable, list[Any]]:
    """Compose the audit-log SELECT + per-filter params.

    Returns ``(stmt, extra_params)``.  Caller must prepend
    ``[project_id, agent_id]`` to ``extra_params`` and append the trailing
    ``LIMIT`` value.  The returned ``stmt`` is a ``psycopg.sql.Composable``
    (parameterised LIMIT placeholder); it executes the same against both
    sync and async cursors.

    Uses ``psycopg.sql`` composition (not f-string concatenation) because
    the WHERE clauses are joined with ``AND`` separators that depend on
    which filters are present — psycopg.sql.SQL guarantees correct joining
    without manual delimiter bookkeeping.
    """
    from psycopg import sql as pgsql

    conditions: list[str] = ["project_id = %s", "agent_id = %s"]
    params: list[Any] = []
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

    where = pgsql.SQL(" AND ").join(pgsql.SQL(c) for c in conditions)
    stmt = pgsql.SQL(
        "SELECT timestamp, event_type, key, details "
        "FROM audit_log WHERE {} "
        "ORDER BY timestamp ASC, id ASC LIMIT {}"
    ).format(where, pgsql.Placeholder())
    return stmt, params


# ---------------------------------------------------------------------------
# Flywheel meta
# ---------------------------------------------------------------------------

FLYWHEEL_META_GET_SQL = (
    "SELECT value FROM flywheel_meta WHERE project_id = %s AND agent_id = %s AND key = %s"
)

FLYWHEEL_META_SET_SQL = """
INSERT INTO flywheel_meta (project_id, agent_id, key, value, updated_at)
VALUES (%s, %s, %s, %s, now())
ON CONFLICT (project_id, agent_id, key)
DO UPDATE SET value = EXCLUDED.value, updated_at = now()
"""


# ---------------------------------------------------------------------------
# GC archive
# ---------------------------------------------------------------------------

ARCHIVE_ENTRY_SQL = """
INSERT INTO gc_archive
    (project_id, agent_id, archived_at, key, payload, byte_count)
VALUES (%s, %s, now(), %s, %s::jsonb, %s)
ON CONFLICT (project_id, agent_id, archived_at, key) DO NOTHING
"""

LIST_ARCHIVE_SQL = """
SELECT key, archived_at, byte_count, payload
FROM gc_archive
WHERE project_id = %s AND agent_id = %s
ORDER BY archived_at DESC
LIMIT %s
"""

TOTAL_ARCHIVE_BYTES_SQL = """
SELECT COALESCE(SUM(byte_count), 0)
FROM gc_archive
WHERE project_id = %s AND agent_id = %s
"""

# ---------------------------------------------------------------------------
# Visual snapshot aggregates (STORY-078.2)
# ---------------------------------------------------------------------------

SNAPSHOT_ACCESS_STATS_SQL = """
SELECT
    COALESCE(SUM(access_count), 0),
    COALESCE(SUM(total_access_count), 0),
    COALESCE(SUM(useful_access_count), 0),
    COUNT(*) FILTER (WHERE access_count > 0),
    COUNT(*) FILTER (WHERE access_count = 0),
    COUNT(*) FILTER (WHERE access_count BETWEEN 1 AND 5),
    COUNT(*) FILTER (WHERE access_count BETWEEN 6 AND 20),
    COUNT(*) FILTER (WHERE access_count > 20),
    COUNT(*)
FROM private_memories
WHERE project_id = %s AND agent_id = %s
"""

SNAPSHOT_TIER_COUNTS_SQL = """
SELECT tier, COUNT(*)::bigint
FROM private_memories
WHERE project_id = %s AND agent_id = %s
GROUP BY tier
"""

SNAPSHOT_AGENT_SCOPE_COUNTS_SQL = """
SELECT agent_scope, COUNT(*)::bigint
FROM private_memories
WHERE project_id = %s AND agent_id = %s
GROUP BY agent_scope
"""

SNAPSHOT_MEMORY_GROUP_COUNTS_SQL = """
SELECT memory_group, COUNT(*)::bigint
FROM private_memories
WHERE project_id = %s AND agent_id = %s
  AND memory_group IS NOT NULL
  AND memory_group <> ''
GROUP BY memory_group
"""

SNAPSHOT_TAG_COUNTS_SQL = """
SELECT tag, COUNT(*)::bigint AS cnt
FROM private_memories,
     jsonb_array_elements_text(tags) AS tag
WHERE project_id = %s AND agent_id = %s
  AND tag IS NOT NULL
  AND btrim(tag) <> ''
GROUP BY tag
ORDER BY cnt DESC, tag ASC
LIMIT %s
"""
