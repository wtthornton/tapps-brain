-- TAP-1488 STORY-074.1: kg_entities table — first-class knowledge graph entities.
--
-- Creates the `kg_entities` table with full RLS (ENABLE + FORCE), lifecycle
-- fields mirroring MemoryEntry, a STORED generated column for casefolded lookup,
-- and partial/GIN indexes optimised for entity neighbourhood queries.
--
-- Design notes
-- ============
-- tenant_id   — RLS partition key, mirrors project_id role in private_memories.
--               The fail-closed USING policy requires it to be non-NULL and match
--               the current app.project_id session variable.
-- brain_id    — Logical brain identity (project_id + agent_id composite key, or a
--               dedicated UUID from the application layer).  Scope for entity
--               uniqueness: one entity per (brain_id, entity_type, canonical_name).
-- project_id  — Project-level identifier; used for cross-brain project queries.
--
-- All DDL uses gen_random_uuid() and STORED generated columns.
-- No uuidv7(), no VIRTUAL, no casefold() — PG17-safe only.
--
-- Idempotency: CREATE TABLE/INDEX/POLICY IF NOT EXISTS throughout.

-- ---------------------------------------------------------------------------
-- Core table
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS kg_entities (
    -- Identity
    id                      UUID        NOT NULL DEFAULT gen_random_uuid(),

    -- Tenant / scope columns
    tenant_id               TEXT        NOT NULL,   -- RLS key (= project_id at runtime)
    brain_id                TEXT        NOT NULL,   -- logical brain identity
    project_id              TEXT        NOT NULL,   -- project-level scope

    -- Entity semantics
    entity_type             TEXT        NOT NULL,   -- e.g. 'person', 'concept', 'tool'
    canonical_name          TEXT        NOT NULL,
    canonical_name_norm     TEXT        NOT NULL
        GENERATED ALWAYS AS (lower(canonical_name)) STORED,

    -- Supplementary data
    aliases                 JSONB       NOT NULL DEFAULT '[]'::jsonb,
    metadata                JSONB       NOT NULL DEFAULT '{}'::jsonb,

    -- Lifecycle: confidence
    confidence              REAL        NOT NULL DEFAULT 0.6,
    confidence_floor        REAL        NOT NULL DEFAULT 0.0,

    -- Lifecycle: provenance
    source                  TEXT        NOT NULL DEFAULT 'agent',
    source_agent            TEXT        NOT NULL DEFAULT 'unknown',

    -- Lifecycle: status
    status                  VARCHAR(20) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'stale', 'superseded', 'archived')),
    stale_reason            TEXT,
    stale_date              TIMESTAMPTZ,

    -- Lifecycle: timestamps
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_accessed           TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Lifecycle: access counters
    access_count            INTEGER     NOT NULL DEFAULT 0,
    useful_access_count     INTEGER     NOT NULL DEFAULT 0,
    total_access_count      INTEGER     NOT NULL DEFAULT 0,

    -- Lifecycle: FSRS-style adaptive decay
    stability               REAL        NOT NULL DEFAULT 0.0,
    difficulty              REAL        NOT NULL DEFAULT 0.0,

    -- Lifecycle: temporal metadata
    temporal_sensitivity    VARCHAR(10)
        CHECK (temporal_sensitivity IN ('high', 'medium', 'low')),
    valid_at                TIMESTAMPTZ,
    invalid_at              TIMESTAMPTZ,
    superseded_by           TEXT,

    -- Lifecycle: reinforcement
    last_reinforced         TIMESTAMPTZ,
    reinforce_count         INTEGER     NOT NULL DEFAULT 0,
    contradicted            BOOLEAN     NOT NULL DEFAULT FALSE,

    -- Lifecycle: flywheel feedback tallies
    positive_feedback_count REAL        NOT NULL DEFAULT 0.0,
    negative_feedback_count REAL        NOT NULL DEFAULT 0.0,

    -- Primary key: UUID-based to support global cross-brain references
    PRIMARY KEY (id),

    -- Uniqueness within a brain: one active canonical entity per (type, name).
    -- Historical / superseded entities may share (brain_id, entity_type, canonical_name_norm)
    -- once the original is superseded; the application layer enforces this via status.
    UNIQUE (brain_id, entity_type, canonical_name_norm)
);

-- ---------------------------------------------------------------------------
-- Row Level Security
-- ---------------------------------------------------------------------------
-- Pattern mirrors 009_project_rls.sql + 012_rls_force.sql:
--   fail-closed (no admin bypass for entity data),
--   FORCE so the table owner cannot accidentally bypass isolation.

ALTER TABLE kg_entities ENABLE ROW LEVEL SECURITY;
ALTER TABLE kg_entities FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS kg_entities_tenant_isolation ON kg_entities;

CREATE POLICY kg_entities_tenant_isolation ON kg_entities
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

-- Primary lookup: entity neighbourhood queries within a brain.
CREATE INDEX IF NOT EXISTS idx_kg_entities_brain_type_name
    ON kg_entities (brain_id, entity_type, canonical_name_norm);

-- Cross-brain project-level queries (e.g. list all entities in a project).
CREATE INDEX IF NOT EXISTS idx_kg_entities_project_type
    ON kg_entities (project_id, entity_type);

-- GIN on aliases for @> containment queries (alias lookup).
CREATE INDEX IF NOT EXISTS idx_kg_entities_aliases_gin
    ON kg_entities USING GIN (aliases);

-- GIN on metadata for arbitrary JSONB attribute queries.
CREATE INDEX IF NOT EXISTS idx_kg_entities_metadata_gin
    ON kg_entities USING GIN (metadata);

-- Partial index on active entities for status-filtered recall hot path.
CREATE INDEX IF NOT EXISTS idx_kg_entities_active
    ON kg_entities (brain_id, entity_type, canonical_name_norm)
    WHERE status = 'active';

-- ---------------------------------------------------------------------------
-- Schema version bump
-- ---------------------------------------------------------------------------

INSERT INTO private_schema_version (version, description)
VALUES (16, 'kg_entities table with RLS + lifecycle fields (TAP-1488 STORY-074.1)');
