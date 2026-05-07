-- TAP-1489 STORY-074.2: kg_edges table — first-class knowledge graph edges.
--
-- Creates the `kg_edges` table with:
--   - subject/predicate/object FK to kg_entities
--   - full lifecycle fields mirroring MemoryEntry
--   - edge-specific fields: edge_class, layer, profile_name, created_by_agent,
--     last_reinforced_by_agent, contradiction_reason, superseded_by (self-FK)
--   - partial unique index (NOT UNIQUE constraint) on active non-invalidated edges
--   - partial indexes for active-edge neighbourhood hot paths
--   - BTree recency index + GIN on metadata
--   - RLS ENABLE + FORCE using the same tenant_id pattern as migration 012
--
-- Design notes
-- ============
-- Uniqueness is enforced via a PARTIAL INDEX (not a table constraint) so that
-- historical / superseded / invalidated edges can coexist in the same table
-- without colliding. Only (brain_id, subject, predicate, object) tuples where
-- status='active' AND invalid_at IS NULL are subject to the unique check.
--
-- superseded_by is a self-FK with ON DELETE SET NULL: deleting an old edge
-- does not cascade-delete the newer one that superseded it.
--
-- All DDL uses gen_random_uuid() and STORED generated columns.
-- No uuidv7(), no VIRTUAL, no casefold() — PG17-safe only.
--
-- Idempotency: CREATE TABLE/INDEX IF NOT EXISTS + DROP/CREATE POLICY throughout.

-- ---------------------------------------------------------------------------
-- Core table
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS kg_edges (
    -- Identity
    id                          UUID        NOT NULL DEFAULT gen_random_uuid(),

    -- Tenant / scope columns
    tenant_id                   TEXT        NOT NULL,   -- RLS key (= project_id at runtime)
    brain_id                    TEXT        NOT NULL,   -- logical brain identity
    project_id                  TEXT        NOT NULL,   -- project-level scope

    -- Edge semantics
    subject_entity_id           UUID        NOT NULL REFERENCES kg_entities(id) ON DELETE CASCADE,
    predicate                   TEXT        NOT NULL,   -- e.g. 'USES', 'DEPENDS_ON', 'IS_A'
    object_entity_id            UUID        NOT NULL REFERENCES kg_entities(id) ON DELETE CASCADE,
    edge_class                  TEXT,                   -- e.g. 'causal', 'taxonomic', 'temporal'
    layer                       TEXT,                   -- e.g. 'domain', 'procedural', 'context'
    profile_name                TEXT,                   -- memory profile this edge belongs to

    -- Supplementary data
    metadata                    JSONB       NOT NULL DEFAULT '{}'::jsonb,

    -- Lifecycle: confidence
    confidence                  REAL        NOT NULL DEFAULT 0.6,
    confidence_floor            REAL        NOT NULL DEFAULT 0.0,

    -- Lifecycle: provenance
    source                      TEXT        NOT NULL DEFAULT 'agent',
    source_agent                TEXT        NOT NULL DEFAULT 'unknown',
    created_by_agent            TEXT        NOT NULL DEFAULT 'unknown',
    last_reinforced_by_agent    TEXT,

    -- Lifecycle: status
    status                      VARCHAR(20) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'stale', 'superseded', 'archived')),
    stale_reason                TEXT,
    stale_date                  TIMESTAMPTZ,

    -- Lifecycle: timestamps
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_accessed               TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Lifecycle: access counters
    access_count                INTEGER     NOT NULL DEFAULT 0,
    useful_access_count         INTEGER     NOT NULL DEFAULT 0,
    total_access_count          INTEGER     NOT NULL DEFAULT 0,

    -- Lifecycle: FSRS-style adaptive decay
    stability                   REAL        NOT NULL DEFAULT 0.0,
    difficulty                  REAL        NOT NULL DEFAULT 0.0,

    -- Lifecycle: temporal metadata
    temporal_sensitivity        VARCHAR(10)
        CHECK (temporal_sensitivity IN ('high', 'medium', 'low')),
    valid_at                    TIMESTAMPTZ,
    invalid_at                  TIMESTAMPTZ,

    -- Edge-specific lifecycle: supersession
    superseded_by               UUID        REFERENCES kg_edges(id) ON DELETE SET NULL,

    -- Lifecycle: reinforcement
    last_reinforced             TIMESTAMPTZ,
    reinforce_count             INTEGER     NOT NULL DEFAULT 0,

    -- Lifecycle: contradiction
    contradicted                BOOLEAN     NOT NULL DEFAULT FALSE,
    contradiction_reason        TEXT,

    -- Lifecycle: flywheel feedback tallies
    positive_feedback_count     REAL        NOT NULL DEFAULT 0.0,
    negative_feedback_count     REAL        NOT NULL DEFAULT 0.0,

    -- Primary key
    PRIMARY KEY (id)
);

-- ---------------------------------------------------------------------------
-- Row Level Security
-- ---------------------------------------------------------------------------
-- Pattern mirrors 009_project_rls.sql + 012_rls_force.sql:
--   fail-closed (no admin bypass for edge data),
--   FORCE so the table owner cannot accidentally bypass isolation.

ALTER TABLE kg_edges ENABLE ROW LEVEL SECURITY;
ALTER TABLE kg_edges FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS kg_edges_tenant_isolation ON kg_edges;

CREATE POLICY kg_edges_tenant_isolation ON kg_edges
    AS PERMISSIVE
    FOR ALL
    TO PUBLIC
    USING (
        current_setting('app.project_id', TRUE) IS NOT NULL
        AND current_setting('app.project_id', TRUE) <> ''
        AND tenant_id = current_setting('app.project_id', TRUE)
    )
    WITH CHECK (
        current_setting('app.project_id', TRUE) IS NOT NULL
        AND current_setting('app.project_id', TRUE) <> ''
        AND tenant_id = current_setting('app.project_id', TRUE)
    );

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------

-- Partial unique index: only one active, non-invalidated edge per
-- (brain, subject, predicate, object) combination.  Historical edges
-- (superseded / invalidated) may coexist without violating this.
-- NOTE: This is a UNIQUE partial INDEX, not a UNIQUE table constraint,
--       so it does not appear in information_schema.table_constraints.
CREATE UNIQUE INDEX IF NOT EXISTS uix_kg_edges_active_triple
    ON kg_edges (brain_id, subject_entity_id, predicate, object_entity_id)
    WHERE status = 'active' AND invalid_at IS NULL;

-- Active-edge neighbourhood queries (subject-out, object-in, predicate scan).
CREATE INDEX IF NOT EXISTS idx_kg_edges_active_subject
    ON kg_edges (brain_id, subject_entity_id, predicate)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_kg_edges_active_object
    ON kg_edges (brain_id, object_entity_id, predicate)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_kg_edges_active_predicate
    ON kg_edges (brain_id, predicate)
    WHERE status = 'active';

-- Recency ordering.
CREATE INDEX IF NOT EXISTS idx_kg_edges_recency
    ON kg_edges (brain_id, updated_at DESC);

-- Arbitrary JSONB attribute queries.
CREATE INDEX IF NOT EXISTS idx_kg_edges_metadata_gin
    ON kg_edges USING GIN (metadata);

-- ---------------------------------------------------------------------------
-- Schema version bump
-- ---------------------------------------------------------------------------

INSERT INTO private_schema_version (version, description)
VALUES (17, 'kg_edges table with partial unique constraint + RLS (TAP-1489 STORY-074.2)');
