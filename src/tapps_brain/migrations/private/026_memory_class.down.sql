-- Undo migration 026 (memory_class): remove memory_class column and its
-- index from private_memories.

DROP INDEX IF EXISTS idx_priv_memory_class;

ALTER TABLE private_memories
    DROP COLUMN IF EXISTS memory_class;

DELETE FROM private_schema_version WHERE version = 26;
