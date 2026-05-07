"""SQL constants for the KnowledgeGraph backends (sync + async).

Both :class:`tapps_brain.postgres_kg.PostgresKnowledgeGraphStore` and
:class:`tapps_brain.async_postgres_kg.AsyncPostgresKnowledgeGraphStore` import
every SQL string from this module so a single query fix lands in both backends
at once.  Same philosophy as :mod:`tapps_brain._postgres_private_sql`.

Naming: ``<DOMAIN>_<OPERATION>_SQL``.

All queries use ``%s`` placeholders (psycopg3 format).  Dynamic WHERE clauses
(e.g. neighbour direction filtering) are composed in the builder functions at
the bottom of this module — these return ``(sql_str, extra_params)`` tuples.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Entity CRUD
# ---------------------------------------------------------------------------

#: Upsert a KG entity; on canonical-name collision reinforce existing row.
#: Params: tenant_id, brain_id, project_id, entity_type, canonical_name,
#:         aliases::jsonb, metadata::jsonb, confidence, source, source_agent.
UPSERT_ENTITY_SQL = """
INSERT INTO kg_entities (
    tenant_id, brain_id, project_id,
    entity_type, canonical_name,
    aliases, metadata,
    confidence, source, source_agent
) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s)
ON CONFLICT (brain_id, entity_type, canonical_name_norm) DO UPDATE SET
    aliases      = EXCLUDED.aliases,
    metadata     = EXCLUDED.metadata,
    confidence   = GREATEST(kg_entities.confidence, EXCLUDED.confidence),
    source_agent = EXCLUDED.source_agent,
    updated_at   = now()
RETURNING id, confidence, status
"""

#: Fetch an active entity by its UUID.
#: Params: entity_id::uuid.
GET_ENTITY_BY_ID_SQL = """
SELECT id, brain_id, project_id, entity_type,
       canonical_name, canonical_name_norm,
       aliases, metadata,
       confidence, source, source_agent, status,
       stability, difficulty, last_reinforced,
       reinforce_count, contradicted,
       created_at, updated_at
FROM kg_entities
WHERE id = %s::uuid
LIMIT 1
"""

# ---------------------------------------------------------------------------
# Entity resolution
# ---------------------------------------------------------------------------

#: Exact match on canonical_name_norm within a brain + type scope.
#: Params: brain_id, entity_type, canonical_name (Postgres lower() applied).
RESOLVE_ENTITY_EXACT_SQL = """
SELECT id, confidence, status
FROM kg_entities
WHERE brain_id = %s
  AND entity_type = %s
  AND canonical_name_norm = lower(%s)
  AND status = 'active'
ORDER BY confidence DESC
LIMIT 1
"""

#: Alias containment search — returns up to 2 rows to detect ambiguity.
#: Params: brain_id, entity_type, alias_text (Postgres lower() applied).
RESOLVE_ENTITY_BY_ALIAS_SQL = """
SELECT id, confidence, status
FROM kg_entities
WHERE brain_id = %s
  AND entity_type = %s
  AND aliases @> jsonb_build_array(lower(%s))
  AND status = 'active'
ORDER BY confidence DESC
LIMIT 2
"""

# ---------------------------------------------------------------------------
# Edge CRUD
# ---------------------------------------------------------------------------

#: Check whether an active non-invalidated edge already exists.
#: Params: brain_id, subject_entity_id::uuid, predicate, object_entity_id::uuid.
GET_ACTIVE_EDGE_SQL = """
SELECT id, confidence, stability, difficulty,
       last_reinforced, reinforce_count,
       source_agent, created_at, updated_at
FROM kg_edges
WHERE brain_id = %s
  AND subject_entity_id = %s::uuid
  AND predicate = %s
  AND object_entity_id = %s::uuid
  AND status = 'active'
  AND invalid_at IS NULL
LIMIT 1
"""

#: Insert a new edge row.
#: Params: tenant_id, brain_id, project_id, subject_entity_id::uuid, predicate,
#:         object_entity_id::uuid, edge_class, layer, profile_name,
#:         confidence, source, source_agent, created_by_agent, metadata::jsonb.
INSERT_EDGE_SQL = """
INSERT INTO kg_edges (
    tenant_id, brain_id, project_id,
    subject_entity_id, predicate, object_entity_id,
    edge_class, layer, profile_name,
    confidence, source, source_agent,
    created_by_agent, metadata
) VALUES (
    %s, %s, %s,
    %s::uuid, %s, %s::uuid,
    %s, %s, %s,
    %s, %s, %s,
    %s, %s::jsonb
)
RETURNING id
"""

# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

#: Attach an evidence row to an edge XOR entity.
#: Params: tenant_id, brain_id, project_id,
#:         edge_id::uuid (or None), entity_id::uuid (or None),
#:         source_type, source_id, source_key, source_uri, source_hash,
#:         source_span, quote, metadata::jsonb, source_agent,
#:         confidence, utility_score.
ATTACH_EVIDENCE_SQL = """
INSERT INTO kg_evidence (
    tenant_id, brain_id, project_id,
    edge_id, entity_id,
    source_type, source_id, source_key,
    source_uri, source_hash, source_span, quote,
    metadata, source_agent,
    confidence, utility_score
) VALUES (
    %s, %s, %s,
    %s::uuid, %s::uuid,
    %s, %s, %s,
    %s, %s, %s, %s,
    %s::jsonb, %s,
    %s, %s
)
RETURNING id
"""

# ---------------------------------------------------------------------------
# Neighbour queries
# ---------------------------------------------------------------------------

#: Outgoing edges (subject = focal entity).
#: Params: brain_id, subject_entity_id::uuid.
GET_OUTGOING_NEIGHBORS_SQL = """
SELECT
    e.id            AS edge_id,
    e.predicate,
    e.confidence    AS edge_confidence,
    e.stability,
    e.difficulty,
    e.last_reinforced,
    e.updated_at    AS edge_updated_at,
    e.status        AS edge_status,
    ent.id          AS neighbor_id,
    ent.entity_type,
    ent.canonical_name,
    ent.confidence  AS entity_confidence
FROM kg_edges e
JOIN kg_entities ent ON ent.id = e.object_entity_id
WHERE e.brain_id = %s
  AND e.subject_entity_id = %s::uuid
  AND e.status = 'active'
ORDER BY e.confidence DESC
LIMIT 100
"""

#: Incoming edges (object = focal entity).
#: Params: brain_id, object_entity_id::uuid.
GET_INCOMING_NEIGHBORS_SQL = """
SELECT
    e.id            AS edge_id,
    e.predicate,
    e.confidence    AS edge_confidence,
    e.stability,
    e.difficulty,
    e.last_reinforced,
    e.updated_at    AS edge_updated_at,
    e.status        AS edge_status,
    ent.id          AS neighbor_id,
    ent.entity_type,
    ent.canonical_name,
    ent.confidence  AS entity_confidence
FROM kg_edges e
JOIN kg_entities ent ON ent.id = e.subject_entity_id
WHERE e.brain_id = %s
  AND e.object_entity_id = %s::uuid
  AND e.status = 'active'
ORDER BY e.confidence DESC
LIMIT 100
"""

# ---------------------------------------------------------------------------
# Edge lifecycle mutations
# ---------------------------------------------------------------------------

#: Reinforce an edge: update FSRS stability/difficulty + access counters.
#: The WHERE clause embeds the 60-second debounce so the UPDATE is a no-op
#: when the edge was reinforced recently — the caller checks rowcount.
#: Params: new_stability, new_difficulty, edge_id::uuid, brain_id.
REINFORCE_EDGE_SQL = """
UPDATE kg_edges SET
    last_reinforced     = now(),
    reinforce_count     = reinforce_count + 1,
    stability           = %s,
    difficulty          = %s,
    confidence          = LEAST(confidence * 1.05, 1.0),
    updated_at          = now()
WHERE id = %s::uuid
  AND brain_id = %s
  AND (last_reinforced IS NULL
       OR last_reinforced < now() - INTERVAL '60 seconds')
RETURNING id, last_reinforced, stability, difficulty
"""

#: Fetch edge fields needed by the FSRS stability updater (no write).
#: Params: edge_id::uuid.
GET_EDGE_FOR_REINFORCE_SQL = """
SELECT id, stability, difficulty, layer,
       last_reinforced, updated_at,
       confidence, reinforce_count
FROM kg_edges
WHERE id = %s::uuid
LIMIT 1
"""

#: Mark an edge as stale.
#: Params: stale_reason, edge_id::uuid, brain_id.
MARK_EDGE_STALE_SQL = """
UPDATE kg_edges SET
    status       = 'stale',
    stale_reason = %s,
    stale_date   = now(),
    updated_at   = now()
WHERE id = %s::uuid
  AND brain_id = %s
  AND status = 'active'
RETURNING id
"""

#: Record the supersession pointer on the old edge.
#: Params: new_edge_id::uuid, old_edge_id::uuid, brain_id.
SUPERSEDE_EDGE_SQL = """
UPDATE kg_edges SET
    status       = 'superseded',
    superseded_by = %s::uuid,
    updated_at   = now()
WHERE id = %s::uuid
  AND brain_id = %s
  AND status = 'active'
RETURNING id
"""

#: Mark an edge as contradicted; sets status=stale so it drops from recall.
#: Params: contradiction_reason, edge_id::uuid, brain_id.
CONTRADICT_EDGE_SQL = """
UPDATE kg_edges SET
    contradicted         = TRUE,
    contradiction_reason = %s,
    status               = 'stale',
    updated_at           = now()
WHERE id = %s::uuid
  AND brain_id = %s
RETURNING id
"""
