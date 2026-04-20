-- TAP-732: Add lifecycle status fields to private_memories.
-- Entries with status='stale' survive GC (they are explicitly flagged for review).
-- brain_recall excludes stale/superseded by default; use include_stale=True to opt in.

ALTER TABLE private_memories
    ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'stale', 'superseded', 'archived')),
    ADD COLUMN IF NOT EXISTS stale_reason TEXT,
    ADD COLUMN IF NOT EXISTS stale_date TIMESTAMPTZ;

-- Index to support filtered recall (WHERE status = 'active') efficiently.
CREATE INDEX IF NOT EXISTS idx_private_memories_status
    ON private_memories (project_id, agent_id, status);

INSERT INTO private_schema_version (version, description)
VALUES (15, 'Add status/stale_reason/stale_date lifecycle columns to private_memories (TAP-732)');
