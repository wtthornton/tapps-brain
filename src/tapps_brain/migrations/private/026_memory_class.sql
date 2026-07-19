-- TAP-733: Add memory_class column to private_memories.
-- Stores a semantic type classification for pre-filter recall.
-- Four allowed values: incident, guidance, decision, convention.
-- NULL means "unclassified" (default); existing rows are unaffected.
--
-- Renumbered from 015: three migrations shared version 15, and the runner
-- dedups by bare version number — a DB that recorded version 15 with only
-- one of the three files bundled skipped the others forever, leaving
-- columns missing that SAVE_UPSERT_SQL/ENTRY_COLUMNS_SQL require.  All
-- statements are IF NOT EXISTS so re-application on healthy DBs is a no-op.

ALTER TABLE private_memories
    ADD COLUMN IF NOT EXISTS memory_class VARCHAR(20) DEFAULT NULL
        CHECK (memory_class IN ('incident', 'guidance', 'decision', 'convention'));

-- Partial index on (project_id, agent_id, memory_class) for rows where
-- memory_class is set — the filter IS NULL is cheap for the majority of rows
-- that remain unclassified.
CREATE INDEX IF NOT EXISTS idx_priv_memory_class
    ON private_memories (project_id, agent_id, memory_class)
    WHERE memory_class IS NOT NULL;

INSERT INTO private_schema_version (version, description)
VALUES (26, 'Add memory_class column + index to private_memories (TAP-733; renumbered from 015)');
