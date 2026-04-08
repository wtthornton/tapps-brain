-- EPIC-055 STORY-055.1: Initial PostgreSQL schema for Federation backend.
-- Requires: pgvector extension.

CREATE EXTENSION IF NOT EXISTS "vector";

-- ---------------------------------------------------------------------------
-- Schema version tracking
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS federation_schema_version (
    version     INTEGER     NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    description TEXT        NOT NULL DEFAULT ''
);

-- ---------------------------------------------------------------------------
-- Core federated_memories table
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS federated_memories (
    project_id          TEXT        NOT NULL,
    key                 TEXT        NOT NULL,
    value               TEXT        NOT NULL,
    tier                TEXT        NOT NULL DEFAULT 'pattern',
    confidence          REAL        NOT NULL DEFAULT 0.6,
    source              TEXT        NOT NULL DEFAULT 'agent',
    source_agent        TEXT        NOT NULL DEFAULT 'unknown',
    tags                JSONB       NOT NULL DEFAULT '[]'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    origin_project_root TEXT        NOT NULL DEFAULT '',
    memory_group        TEXT,
    embedding           vector(384),
    search_vector       tsvector,
    PRIMARY KEY (project_id, key)
);

-- GIN index on tags.
CREATE INDEX IF NOT EXISTS idx_fed_tags_gin
    ON federated_memories USING GIN (tags);

-- GIN index on search_vector.
CREATE INDEX IF NOT EXISTS idx_fed_search_vector_gin
    ON federated_memories USING GIN (search_vector);

-- Composite indexes.
CREATE INDEX IF NOT EXISTS idx_fed_project ON federated_memories (project_id);
CREATE INDEX IF NOT EXISTS idx_fed_confidence ON federated_memories (confidence);
CREATE INDEX IF NOT EXISTS idx_fed_tier ON federated_memories (tier);
CREATE INDEX IF NOT EXISTS idx_fed_memory_group ON federated_memories (memory_group);

-- IVFFlat on embedding.
CREATE INDEX IF NOT EXISTS idx_fed_embedding_ivfflat
    ON federated_memories USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- ---------------------------------------------------------------------------
-- Trigger: auto-update search_vector
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION federated_memories_search_vector_update() RETURNS trigger AS $$
BEGIN
    NEW.search_vector :=
        setweight(to_tsvector('english', COALESCE(NEW.key, '')), 'A') ||
        setweight(to_tsvector('english', COALESCE(NEW.value, '')), 'B') ||
        setweight(to_tsvector('english', COALESCE(NEW.tags::text, '')), 'C');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_federated_memories_search_vector ON federated_memories;
CREATE TRIGGER trg_federated_memories_search_vector
    BEFORE INSERT OR UPDATE ON federated_memories
    FOR EACH ROW
    EXECUTE FUNCTION federated_memories_search_vector_update();

-- ---------------------------------------------------------------------------
-- Federation subscriptions
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS federation_subscriptions (
    subscriber      TEXT        NOT NULL,
    sources         JSONB       NOT NULL DEFAULT '[]'::jsonb,
    tag_filter      JSONB       NOT NULL DEFAULT '[]'::jsonb,
    min_confidence  REAL        NOT NULL DEFAULT 0.5,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (subscriber)
);

-- ---------------------------------------------------------------------------
-- Federation metadata (per-project sync tracking)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS federation_meta (
    project_id  TEXT        PRIMARY KEY,
    last_sync   TIMESTAMPTZ NOT NULL DEFAULT now(),
    entry_count INTEGER     NOT NULL DEFAULT 0
);

-- Record this migration.
INSERT INTO federation_schema_version (version, description)
VALUES (1, 'Initial schema: federated_memories, subscriptions, meta');
