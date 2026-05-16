-- Undo migration 015 (integrity_hash_version): remove integrity_hash_v
-- column and its index from private_memories.

DROP INDEX IF EXISTS private_memories_integrity_hash_v_idx;

ALTER TABLE private_memories
    DROP COLUMN IF EXISTS integrity_hash_v;

DELETE FROM private_schema_version WHERE version = 15;
