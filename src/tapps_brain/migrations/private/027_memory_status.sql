-- TAP-732: Add lifecycle status fields to private_memories.
-- Entries with status='stale' survive GC (they are explicitly flagged for review).
-- brain_recall excludes stale/superseded by default; use include_stale=True to opt in.
--
-- Renumbered from 015: three migrations shared version 15, and the runner
-- dedups by bare version number — a DB that recorded version 15 with only
-- one of the three files bundled skipped the others forever, leaving
-- columns missing that SAVE_UPSERT_SQL/ENTRY_COLUMNS_SQL require.  All
-- statements are IF NOT EXISTS so re-application on healthy DBs is a no-op.

ALTER TABLE private_memories
    ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'stale', 'superseded', 'archived')),
    ADD COLUMN IF NOT EXISTS stale_reason TEXT,
    ADD COLUMN IF NOT EXISTS stale_date TIMESTAMPTZ;

-- Index to support filtered recall (WHERE status = 'active') efficiently.
CREATE INDEX IF NOT EXISTS idx_private_memories_status
    ON private_memories (project_id, agent_id, status);

INSERT INTO private_schema_version (version, description)
VALUES (27, 'Add status/stale_reason/stale_date lifecycle columns to private_memories (TAP-732; renumbered from 015)');
