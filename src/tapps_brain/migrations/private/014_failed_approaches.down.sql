-- Undo migration 014: remove failed_approaches column from
-- private_memories.

ALTER TABLE private_memories
    DROP COLUMN IF EXISTS failed_approaches;

DELETE FROM private_schema_version WHERE version = 14;
