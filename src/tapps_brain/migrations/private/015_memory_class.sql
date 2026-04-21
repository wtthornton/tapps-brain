-- TAP-733: Add memory_class column to private_memories.
-- Stores a semantic type classification for pre-filter recall.
-- Four allowed values: incident, guidance, decision, convention.
-- NULL means "unclassified" (default); existing rows are unaffected.

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
VALUES (15, 'Add memory_class column + index to private_memories (TAP-733)');
