-- STORY-066.3: GC archive Postgres table.
-- Replaces the legacy archive.jsonl JSONL file written by MemoryGarbageCollector.
-- Archived rows are now INSERTed here so they are queryable through SQL and survive
-- the deletion of the on-disk store directory.
--
-- byte_count is denormalised at INSERT time to avoid hot-path SUM(octet_length(...))
-- queries when reporting archive size in health() / CLI maintenance gc.

CREATE TABLE IF NOT EXISTS gc_archive (
    project_id   TEXT        NOT NULL,
    agent_id     TEXT        NOT NULL,
    archived_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    key          TEXT        NOT NULL,
    payload      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    byte_count   INTEGER     NOT NULL DEFAULT 0,

    PRIMARY KEY (project_id, agent_id, archived_at, key)
);

-- Recency queries: "show me the most recent N archived rows for this agent".
CREATE INDEX IF NOT EXISTS idx_gc_archive_recency
    ON gc_archive (project_id, agent_id, archived_at DESC);

-- Per-key lookup: "was key X ever archived?".
CREATE INDEX IF NOT EXISTS idx_gc_archive_key
    ON gc_archive (project_id, agent_id, key);

INSERT INTO private_schema_version (version, description)
VALUES (6, 'Add gc_archive table (Postgres-only GC archive replaces archive.jsonl)');
