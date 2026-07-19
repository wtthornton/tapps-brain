-- Undo migration 027 (memory_status): remove status lifecycle columns and
-- their index from private_memories.

DROP INDEX IF EXISTS idx_private_memories_status;

ALTER TABLE private_memories
    DROP COLUMN IF EXISTS status,
    DROP COLUMN IF EXISTS stale_reason,
    DROP COLUMN IF EXISTS stale_date;

DELETE FROM private_schema_version WHERE version = 27;
