-- EPIC-055 STORY-055.1: Initial PostgreSQL schema for Hive backend.
-- Requires: pgcrypto (for gen_random_uuid), pg_trgm, pgvector extensions.

-- Enable required extensions (idempotent).
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
CREATE EXTENSION IF NOT EXISTS "vector";

-- ---------------------------------------------------------------------------
-- Schema version tracking
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS hive_schema_version (
    version     INTEGER     NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    description TEXT        NOT NULL DEFAULT ''
);

-- ---------------------------------------------------------------------------
-- Core hive_memories table
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS hive_memories (
    namespace       TEXT        NOT NULL DEFAULT 'universal',
    key             TEXT        NOT NULL,
    value           TEXT        NOT NULL,
    source_agent    TEXT        NOT NULL DEFAULT 'unknown',
    tier            TEXT        NOT NULL DEFAULT 'pattern',
    confidence      REAL        NOT NULL DEFAULT 0.6,
    source          TEXT        NOT NULL DEFAULT 'agent',
    tags            JSONB       NOT NULL DEFAULT '[]'::jsonb,
    valid_at        TIMESTAMPTZ,
    invalid_at      TIMESTAMPTZ,
    superseded_by   TEXT,
    memory_group    TEXT,
    conflict_policy TEXT        NOT NULL DEFAULT 'supersede',
    embedding       vector(384),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    search_vector   tsvector,
    PRIMARY KEY (namespace, key)
);

-- GIN index on tags for @> containment queries.
CREATE INDEX IF NOT EXISTS idx_hive_tags_gin
    ON hive_memories USING GIN (tags);

-- GIN index on search_vector for full-text search.
CREATE INDEX IF NOT EXISTS idx_hive_search_vector_gin
    ON hive_memories USING GIN (search_vector);

-- Composite index on (namespace, confidence) for filtered queries.
CREATE INDEX IF NOT EXISTS idx_hive_namespace_confidence
    ON hive_memories (namespace, confidence);

-- IVFFlat index on embedding for approximate nearest-neighbour search.
-- Note: IVFFlat requires at least some rows to build; the index is created
-- here but will only become effective after initial data load.
-- Using 100 lists as a reasonable default for moderate dataset sizes.
CREATE INDEX IF NOT EXISTS idx_hive_embedding_ivfflat
    ON hive_memories USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- Additional useful indexes.
CREATE INDEX IF NOT EXISTS idx_hive_tier ON hive_memories (tier);
CREATE INDEX IF NOT EXISTS idx_hive_source_agent ON hive_memories (source_agent);
CREATE INDEX IF NOT EXISTS idx_hive_memory_group ON hive_memories (memory_group);

-- ---------------------------------------------------------------------------
-- Trigger: auto-update search_vector on INSERT/UPDATE
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION hive_memories_search_vector_update() RETURNS trigger AS $$
BEGIN
    NEW.search_vector :=
        setweight(to_tsvector('english', COALESCE(NEW.key, '')), 'A') ||
        setweight(to_tsvector('english', COALESCE(NEW.value, '')), 'B') ||
        setweight(to_tsvector('english', COALESCE(NEW.tags::text, '')), 'C');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_hive_memories_search_vector ON hive_memories;
CREATE TRIGGER trg_hive_memories_search_vector
    BEFORE INSERT OR UPDATE ON hive_memories
    FOR EACH ROW
    EXECUTE FUNCTION hive_memories_search_vector_update();

-- ---------------------------------------------------------------------------
-- Trigger: NOTIFY on hive_memories INSERT/UPDATE
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION hive_memories_notify() RETURNS trigger AS $$
BEGIN
    PERFORM pg_notify('hive_memories_changed', json_build_object(
        'namespace', NEW.namespace,
        'key', NEW.key,
        'operation', TG_OP
    )::text);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_hive_memories_notify ON hive_memories;
CREATE TRIGGER trg_hive_memories_notify
    AFTER INSERT OR UPDATE ON hive_memories
    FOR EACH ROW
    EXECUTE FUNCTION hive_memories_notify();

-- ---------------------------------------------------------------------------
-- Group tables
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS hive_groups (
    name        TEXT        PRIMARY KEY,
    description TEXT        NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS hive_group_members (
    group_name  TEXT        NOT NULL REFERENCES hive_groups(name) ON DELETE CASCADE,
    agent_id    TEXT        NOT NULL,
    role        TEXT        NOT NULL DEFAULT 'member',
    joined_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (group_name, agent_id)
);

-- ---------------------------------------------------------------------------
-- Feedback events
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS hive_feedback_events (
    id              TEXT        NOT NULL PRIMARY KEY,
    namespace       TEXT        NOT NULL,
    entry_key       TEXT,
    event_type      TEXT        NOT NULL,
    session_id      TEXT,
    utility_score   REAL,
    details         JSONB       NOT NULL DEFAULT '{}'::jsonb,
    timestamp       TIMESTAMPTZ NOT NULL,
    source_project  TEXT
);

CREATE INDEX IF NOT EXISTS idx_hive_fb_namespace ON hive_feedback_events (namespace);
CREATE INDEX IF NOT EXISTS idx_hive_fb_entry_key ON hive_feedback_events (entry_key);
CREATE INDEX IF NOT EXISTS idx_hive_fb_event_type ON hive_feedback_events (event_type);
CREATE INDEX IF NOT EXISTS idx_hive_fb_timestamp ON hive_feedback_events (timestamp);

-- ---------------------------------------------------------------------------
-- Agent registry
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS agent_registry (
    id              TEXT        PRIMARY KEY,
    name            TEXT        NOT NULL DEFAULT '',
    profile         TEXT        NOT NULL DEFAULT 'repo-brain',
    skills          JSONB       NOT NULL DEFAULT '[]'::jsonb,
    project_root    TEXT,
    groups          JSONB       NOT NULL DEFAULT '[]'::jsonb,
    registered_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Write notification tracking (monotonic revision counter)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS hive_write_notify (
    id          INTEGER     PRIMARY KEY CHECK (id = 1),
    revision    INTEGER     NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO hive_write_notify (id, revision, updated_at)
VALUES (1, 0, now())
ON CONFLICT (id) DO NOTHING;

-- Record this migration.
INSERT INTO hive_schema_version (version, description)
VALUES (1, 'Initial schema: hive_memories, groups, feedback, agent_registry, write_notify');
