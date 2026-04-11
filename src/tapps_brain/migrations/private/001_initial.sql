-- EPIC-059 STORY-059.4: Initial PostgreSQL schema for private agent memory.
-- Keyed by (project_id, agent_id, key) — one row per memory entry per agent per project.
-- Requires: pgvector extension (for embedding column).

-- Enable required extensions (idempotent).
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
CREATE EXTENSION IF NOT EXISTS "vector";

-- ---------------------------------------------------------------------------
-- Schema version tracking
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS private_schema_version (
    version     INTEGER     NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    description TEXT        NOT NULL DEFAULT ''
);

-- ---------------------------------------------------------------------------
-- Core private_memories table
-- ---------------------------------------------------------------------------
-- All columns map 1-to-1 with MemoryEntry fields (models.py).
-- The composite primary key (project_id, agent_id, key) ensures per-agent
-- per-project isolation without file-system paths.

CREATE TABLE IF NOT EXISTS private_memories (
    -- Tenant columns (partition keys)
    project_id          TEXT        NOT NULL,   -- canonical repo hash or project slug
    agent_id            TEXT        NOT NULL,   -- agent identifier (e.g. 'claude-code')

    -- Core identity
    key                 TEXT        NOT NULL,   -- slug: [a-z0-9][a-z0-9._-]{0,127}
    value               TEXT        NOT NULL,   -- memory content (max 4096 chars)

    -- Classification
    tier                TEXT        NOT NULL DEFAULT 'pattern',
    confidence          REAL        NOT NULL DEFAULT 0.6,
    source              TEXT        NOT NULL DEFAULT 'agent',
    source_agent        TEXT        NOT NULL DEFAULT 'unknown',
    scope               TEXT        NOT NULL DEFAULT 'project',
    agent_scope         TEXT        NOT NULL DEFAULT 'private',
    memory_group        TEXT,
    tags                JSONB       NOT NULL DEFAULT '[]'::jsonb,

    -- Timestamps
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_accessed       TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Access tracking
    access_count        INTEGER     NOT NULL DEFAULT 0,
    useful_access_count INTEGER     NOT NULL DEFAULT 0,
    total_access_count  INTEGER     NOT NULL DEFAULT 0,

    -- Branch scoping
    branch              TEXT,

    -- Reinforcement / contradiction
    last_reinforced     TIMESTAMPTZ,
    reinforce_count     INTEGER     NOT NULL DEFAULT 0,
    contradicted        BOOLEAN     NOT NULL DEFAULT FALSE,
    contradiction_reason TEXT,

    -- Provenance / seeding
    seeded_from         TEXT,

    -- Bi-temporal versioning (EPIC-004)
    valid_at            TIMESTAMPTZ,
    invalid_at          TIMESTAMPTZ,
    superseded_by       TEXT,
    valid_from          TEXT        NOT NULL DEFAULT '',
    valid_until         TEXT        NOT NULL DEFAULT '',

    -- Provenance metadata (GitHub #38)
    source_session_id   TEXT        NOT NULL DEFAULT '',
    source_channel      TEXT        NOT NULL DEFAULT '',
    source_message_id   TEXT        NOT NULL DEFAULT '',
    triggered_by        TEXT        NOT NULL DEFAULT '',

    -- FSRS-style adaptive decay (GitHub #28, task 040.5)
    stability           REAL        NOT NULL DEFAULT 0.0,
    difficulty          REAL        NOT NULL DEFAULT 0.0,

    -- Flywheel feedback tallies (EPIC-031)
    positive_feedback_count REAL    NOT NULL DEFAULT 0.0,
    negative_feedback_count REAL    NOT NULL DEFAULT 0.0,

    -- Integrity
    integrity_hash      TEXT,

    -- Semantic search (optional — NULL when embeddings not enabled)
    embedding           vector(384),
    embedding_model_id  TEXT,

    -- Full-text search (maintained by trigger below)
    search_vector       tsvector,

    PRIMARY KEY (project_id, agent_id, key)
);

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------

-- GIN on tags for @> containment queries.
CREATE INDEX IF NOT EXISTS idx_priv_tags_gin
    ON private_memories USING GIN (tags);

-- GIN on search_vector for full-text recall queries.
CREATE INDEX IF NOT EXISTS idx_priv_search_vector_gin
    ON private_memories USING GIN (search_vector);

-- Composite on (project_id, agent_id, confidence) for filtered recall.
-- Hot path: recall within one agent's project scope sorted by confidence.
CREATE INDEX IF NOT EXISTS idx_priv_project_agent_confidence
    ON private_memories (project_id, agent_id, confidence DESC);

-- Composite on (project_id, agent_id, tier) for tier-filtered queries.
CREATE INDEX IF NOT EXISTS idx_priv_project_agent_tier
    ON private_memories (project_id, agent_id, tier);

-- Composite on (project_id, agent_id, updated_at) for recency ordering.
CREATE INDEX IF NOT EXISTS idx_priv_project_agent_updated
    ON private_memories (project_id, agent_id, updated_at DESC);

-- Index on memory_group for group-scoped recall.
CREATE INDEX IF NOT EXISTS idx_priv_memory_group
    ON private_memories (project_id, agent_id, memory_group);

-- Index on last_accessed for decay / GC sweeps.
CREATE INDEX IF NOT EXISTS idx_priv_last_accessed
    ON private_memories (last_accessed);

-- IVFFlat on embedding for approximate nearest-neighbour search.
-- Effective only after initial data load; 100 lists suits moderate datasets.
CREATE INDEX IF NOT EXISTS idx_priv_embedding_ivfflat
    ON private_memories USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- ---------------------------------------------------------------------------
-- Trigger: auto-update search_vector on INSERT/UPDATE
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION private_memories_search_vector_update() RETURNS trigger AS $$
BEGIN
    NEW.search_vector :=
        setweight(to_tsvector('english', COALESCE(NEW.key, '')), 'A') ||
        setweight(to_tsvector('english', COALESCE(NEW.value, '')), 'B') ||
        setweight(to_tsvector('english', COALESCE(NEW.tags::text, '')), 'C');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_private_memories_search_vector ON private_memories;
CREATE TRIGGER trg_private_memories_search_vector
    BEFORE INSERT OR UPDATE ON private_memories
    FOR EACH ROW
    EXECUTE FUNCTION private_memories_search_vector_update();

-- ---------------------------------------------------------------------------
-- Record this migration.
-- ---------------------------------------------------------------------------

INSERT INTO private_schema_version (version, description)
VALUES (1, 'Initial schema: private_memories table with full MemoryEntry column set');
