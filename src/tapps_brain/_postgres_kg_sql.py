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

#: Batch exact canonical-name resolution against an array of normalised candidates.
#: Returns one row per matched candidate (highest-confidence entity wins ties).
#: Params: brain_id, candidates::text[] (caller must lower-case each element).
BATCH_RESOLVE_EXACT_SQL = """
SELECT lower(canonical_name) AS matched_norm,
       id::text              AS entity_id,
       confidence
FROM kg_entities
WHERE brain_id = %s
  AND status   = 'active'
  AND lower(canonical_name) = ANY(%s::text[])
ORDER BY confidence DESC
"""

#: Batch alias resolution against an array of normalised candidates.
#: Expands JSONB alias arrays and checks each element against the candidate set.
#: Returns one row per (alias_norm, entity) pair; the caller groups and
#: picks the best match per candidate.
#: Params: brain_id, candidates::text[] (caller must lower-case each element).
BATCH_RESOLVE_ALIAS_SQL = """
SELECT lower(ae.alias_elem) AS matched_norm,
       e.id::text            AS entity_id,
       e.confidence
FROM   kg_entities e
CROSS  JOIN LATERAL jsonb_array_elements_text(e.aliases) AS ae(alias_elem)
WHERE  e.brain_id = %s
  AND  e.status   = 'active'
  AND  lower(ae.alias_elem) = ANY(%s::text[])
ORDER  BY e.confidence DESC
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
# Neighbour queries (single entity — used by the single-entity get_neighbors)
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
# Multi-entity neighbourhood queries (STORY-076.2)
# ---------------------------------------------------------------------------

#: 1-hop outgoing neighbourhood for a set of focal entity UUIDs.
#: Includes evidence_count via a LEFT JOIN aggregate so callers can use it
#: in the composite edge-score formula without a second round-trip.
#: Params: brain_id, focal_ids::uuid[], include_historical (bool x2),
#:         predicate_filter (str|None x2), limit (int).
GET_MULTI_NEIGHBORS_1HOP_SQL = """
SELECT
    e.id                            AS edge_id,
    e.predicate,
    e.confidence                    AS edge_confidence,
    e.stability,
    e.difficulty,
    e.last_reinforced,
    e.updated_at                    AS edge_updated_at,
    e.status                        AS edge_status,
    e.contradicted,
    e.reinforce_count,
    e.useful_access_count,
    e.access_count,
    e.source,
    COALESCE(ev.evidence_count, 0)  AS evidence_count,
    ent.id::text                    AS neighbor_id,
    ent.entity_type,
    ent.canonical_name,
    ent.confidence                  AS entity_confidence,
    1                               AS hop
FROM  kg_edges e
JOIN  kg_entities ent
      ON ent.id = e.object_entity_id
LEFT  JOIN (
    SELECT edge_id, COUNT(*) AS evidence_count
    FROM   kg_evidence
    GROUP  BY edge_id
) ev ON ev.edge_id = e.id
WHERE e.brain_id = %s
  AND e.subject_entity_id = ANY(%s::uuid[])
  AND (e.status = 'active' OR %s)
  AND (NOT e.contradicted OR %s)
  AND (%s IS NULL OR e.predicate = %s)
ORDER BY e.confidence DESC
LIMIT %s
"""

#: 2-hop recursive neighbourhood for a set of focal entity UUIDs.
#: Uses a recursive CTE (UNION ALL for performance; DISTINCT ON deduplicates)
#: to follow outgoing edges up to ``max_hops`` levels deep.
#: Params: brain_id, focal_ids::uuid[], include_historical (bool x2),
#:         predicate_filter (str|None x2),
#:         brain_id (again in recursive term), include_historical (bool x2),
#:         predicate_filter (str|None x2), max_hops (int), limit (int).
GET_MULTI_NEIGHBORS_2HOP_SQL = """
WITH RECURSIVE neighbourhood(
    edge_id, predicate, edge_confidence,
    stability, difficulty, last_reinforced, edge_updated_at, edge_status,
    contradicted, reinforce_count, useful_access_count, access_count, source,
    neighbor_id, entity_type, canonical_name, entity_confidence, hop
) AS (
    -- Base case: direct neighbours of focal entities.
    SELECT
        e.id, e.predicate, e.confidence,
        e.stability, e.difficulty, e.last_reinforced, e.updated_at,
        e.status, e.contradicted, e.reinforce_count,
        e.useful_access_count, e.access_count, e.source,
        e.object_entity_id,
        ent.entity_type, ent.canonical_name, ent.confidence,
        1 AS hop
    FROM kg_edges e
    JOIN kg_entities ent ON ent.id = e.object_entity_id
    WHERE e.brain_id = %s
      AND e.subject_entity_id = ANY(%s::uuid[])
      AND (e.status = 'active' OR %s)
      AND (NOT e.contradicted OR %s)
      AND (%s IS NULL OR e.predicate = %s)

    UNION ALL

    -- Recursive step: one hop further from the previous frontier.
    SELECT
        e2.id, e2.predicate, e2.confidence,
        e2.stability, e2.difficulty, e2.last_reinforced, e2.updated_at,
        e2.status, e2.contradicted, e2.reinforce_count,
        e2.useful_access_count, e2.access_count, e2.source,
        e2.object_entity_id,
        ent2.entity_type, ent2.canonical_name, ent2.confidence,
        n.hop + 1
    FROM kg_edges e2
    JOIN neighbourhood n      ON n.neighbor_id = e2.subject_entity_id
    JOIN kg_entities   ent2   ON ent2.id        = e2.object_entity_id
    WHERE e2.brain_id = %s
      AND (e2.status = 'active' OR %s)
      AND (NOT e2.contradicted OR %s)
      AND (%s IS NULL OR e2.predicate = %s)
      AND n.hop < %s
)
SELECT DISTINCT ON (edge_id)
    edge_id::text,
    predicate, edge_confidence, stability, difficulty,
    last_reinforced, edge_updated_at, edge_status, contradicted,
    reinforce_count, useful_access_count, access_count, source,
    COALESCE(ev.evidence_count, 0) AS evidence_count,
    neighbor_id::text,
    entity_type, canonical_name, entity_confidence, hop
FROM neighbourhood n2
LEFT JOIN (
    SELECT edge_id, COUNT(*) AS evidence_count
    FROM   kg_evidence
    GROUP  BY edge_id
) ev ON ev.edge_id = n2.edge_id
ORDER BY edge_id, hop, edge_confidence DESC
LIMIT %s
"""

# ---------------------------------------------------------------------------
# Edge lifecycle mutations
# ---------------------------------------------------------------------------

#: Reinforce an edge: update FSRS stability/difficulty + access counters.
#: The WHERE clause embeds the 60-second debounce so the UPDATE is a no-op
#: when the edge was reinforced recently — the caller checks rowcount.
#: Params: new_stability, new_difficulty, edge_id::uuid, brain_id.
# ---------------------------------------------------------------------------
# Edge feedback counters (EPIC-076 STORY-076.6)
# ---------------------------------------------------------------------------

#: Apply ``edge_helpful`` counters: increment useful_access_count +
#: positive_feedback_count + access_count.
#: Params: edge_id::uuid, brain_id.
APPLY_EDGE_HELPFUL_SQL = """
UPDATE kg_edges SET
    useful_access_count     = useful_access_count + 1,
    positive_feedback_count = positive_feedback_count + 1,
    access_count            = access_count + 1,
    updated_at              = now()
WHERE id = %s::uuid
  AND brain_id = %s
RETURNING id, positive_feedback_count, negative_feedback_count
"""

#: Apply ``edge_misleading`` counters: increment negative_feedback_count,
#: reduce confidence by *delta* (clamped at confidence_floor), and set
#: ``metadata.review_flagged = true`` when the 3:1 negative-to-positive
#: ratio threshold is exceeded.
#: Params: confidence_delta (REAL), edge_id::uuid, brain_id.
APPLY_EDGE_MISLEADING_SQL = """
UPDATE kg_edges SET
    negative_feedback_count = negative_feedback_count + 1,
    access_count            = access_count + 1,
    confidence              = GREATEST(confidence - %s, confidence_floor),
    metadata                = CASE
        WHEN (negative_feedback_count + 1) > (3 * positive_feedback_count)
        THEN jsonb_set(metadata, '{review_flagged}', 'true'::jsonb, true)
        ELSE metadata
    END,
    updated_at              = now()
WHERE id = %s::uuid
  AND brain_id = %s
RETURNING id, positive_feedback_count, negative_feedback_count,
          confidence, (metadata->>'review_flagged') AS review_flagged
"""

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
