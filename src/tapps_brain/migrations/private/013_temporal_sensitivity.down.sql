-- Undo migration 013: remove temporal_sensitivity column from
-- private_memories.

ALTER TABLE private_memories
    DROP COLUMN IF EXISTS temporal_sensitivity;

DELETE FROM private_schema_version WHERE version = 13;
