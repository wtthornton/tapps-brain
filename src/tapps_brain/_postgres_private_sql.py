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

#: Schema version reported by ``get_schema_version()`` (mirrors 001_initial.sql).
PRIVATE_SCHEMA_VERSION = 1

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
    memory_class
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
    integrity_hash_v         = EXCLUDED.integrity_hash_v,
    embedding_model_id       = EXCLUDED.embedding_model_id,
    temporal_sensitivity     = EXCLUDED.temporal_sensitivity,
    failed_approaches        = EXCLUDED.failed_approaches,
    status                   = EXCLUDED.status,
    stale_reason             = EXCLUDED.stale_reason,
    stale_date               = EXCLUDED.stale_date,
    memory_class             = EXCLUDED.memory_class
"""


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
    )


LOAD_ALL_SQL = (
    "SELECT * FROM private_memories"
    " WHERE project_id = %s AND agent_id = %s"
    " ORDER BY updated_at DESC"
)

DELETE_BY_KEY_SQL = (
    "DELETE FROM private_memories WHERE project_id = %s AND agent_id = %s AND key = %s"
)


# ---------------------------------------------------------------------------
# Search (FTS) — base + composable filter snippets
# ---------------------------------------------------------------------------

_SEARCH_BASE_SQL = (
    "SELECT *, ts_rank(search_vector, plainto_tsquery('english', %s)) AS _rank "
    "FROM private_memories "
    "WHERE project_id = %s AND agent_id = %s "
    "  AND search_vector @@ plainto_tsquery('english', %s)"
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

    sql += _SEARCH_ORDER_LIMIT_SQL
    return sql, params


# ---------------------------------------------------------------------------
# Vector similarity
# ---------------------------------------------------------------------------

KNN_SEARCH_SQL = (
    "SELECT key, embedding <=> %s::vector AS distance "
    "FROM private_memories "
    "WHERE project_id = %s AND agent_id = %s AND embedding IS NOT NULL "
    "ORDER BY distance "
    "LIMIT %s"
)

VECTOR_ROW_COUNT_SQL = (
    "SELECT COUNT(*) FROM private_memories "
    "WHERE project_id = %s AND agent_id = %s AND embedding IS NOT NULL"
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
