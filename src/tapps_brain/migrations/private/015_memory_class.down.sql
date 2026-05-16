-- Undo migration 015 (memory_class): remove memory_class column and its
-- index from private_memories.

DROP INDEX IF EXISTS idx_priv_memory_class;

ALTER TABLE private_memories
    DROP COLUMN IF EXISTS memory_class;

-- Version 15 DELETE is shared across three co-version migrations;
-- this file removes the version row if it still exists.
DELETE FROM private_schema_version WHERE version = 15;
