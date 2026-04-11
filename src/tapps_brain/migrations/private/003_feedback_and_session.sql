-- EPIC-059 stage 2: relocate feedback events + session chunk index from
-- SQLite (persistence.py session_chunks, feedback.py feedback_events) into
-- the Postgres private schema. Both tables are scoped to (project_id, agent_id)
-- to match private_memories and keep tenant isolation consistent.
--
-- Greenfield: no data migration. Old rows live in SQLite memory.db files
-- that will never be read again and are deleted by the v3 startup path.

-- ---------------------------------------------------------------------------
-- feedback_events — typed quality-loop events (EPIC-029)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS feedback_events (
    project_id      TEXT        NOT NULL,
    agent_id        TEXT        NOT NULL,
    id              TEXT        NOT NULL,          -- UUID from FeedbackEvent.id
    event_type      TEXT        NOT NULL,          -- Object-Action snake_case
    entry_key       TEXT,                           -- memory entry this event relates to
    session_id      TEXT,                           -- calling session identifier
    utility_score   REAL,                           -- [-1.0, 1.0] or NULL
    details         JSONB       NOT NULL DEFAULT '{}'::jsonb,
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (project_id, agent_id, id)
);

CREATE INDEX IF NOT EXISTS idx_feedback_events_type
    ON feedback_events (project_id, agent_id, event_type);

CREATE INDEX IF NOT EXISTS idx_feedback_events_timestamp
    ON feedback_events (project_id, agent_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_feedback_events_entry_key
    ON feedback_events (project_id, agent_id, entry_key)
    WHERE entry_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_feedback_events_session_id
    ON feedback_events (project_id, agent_id, session_id)
    WHERE session_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- session_chunks — searchable past-session summaries (EPIC-002, previously
-- stored in SQLite FTS5 table via persistence.save_session_chunks)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS session_chunks (
    project_id      TEXT        NOT NULL,
    agent_id        TEXT        NOT NULL,
    session_id      TEXT        NOT NULL,
    chunk_index     INTEGER     NOT NULL,
    content         TEXT        NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- tsvector maintained by trigger below (same pattern as private_memories)
    search_vector   tsvector,

    PRIMARY KEY (project_id, agent_id, session_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_session_chunks_created_at
    ON session_chunks (project_id, agent_id, created_at);

CREATE INDEX IF NOT EXISTS idx_session_chunks_search_vector_gin
    ON session_chunks USING GIN (search_vector);

CREATE OR REPLACE FUNCTION session_chunks_search_vector_update() RETURNS trigger AS $$
BEGIN
    NEW.search_vector := to_tsvector('english', COALESCE(NEW.content, ''));
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_session_chunks_search_vector ON session_chunks;
CREATE TRIGGER trg_session_chunks_search_vector
    BEFORE INSERT OR UPDATE ON session_chunks
    FOR EACH ROW
    EXECUTE FUNCTION session_chunks_search_vector_update();

-- ---------------------------------------------------------------------------
-- Record this migration.
-- ---------------------------------------------------------------------------

INSERT INTO private_schema_version (version, description)
VALUES (3, 'Add feedback_events and session_chunks tables (Postgres-only replacement for SQLite FeedbackStore and SessionIndex)');
