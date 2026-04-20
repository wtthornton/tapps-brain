-- Migration 015: Add integrity_hash_v column (TAP-710)
--
-- Tracks the canonical encoding version used to produce integrity_hash:
--   1 = legacy pipe-joined form (key|value|tier|source) — vulnerable to
--       field-boundary collision when value contains a pipe (TAP-710).
--   2 = JSON array [key, value, tier, source] — collision-free.
--
-- Existing rows default to 1 (legacy).  The application migration shim
-- (MemoryStore.rehash_integrity_v1) upgrades rows to version 2 on demand.
-- New writes always produce version 2 hashes.

ALTER TABLE private_memories
    ADD COLUMN IF NOT EXISTS integrity_hash_v INTEGER NOT NULL DEFAULT 1;

-- Index to make the migration shim's "find all v1 rows" query efficient.
CREATE INDEX IF NOT EXISTS private_memories_integrity_hash_v_idx
    ON private_memories (project_id, agent_id, integrity_hash_v)
    WHERE integrity_hash_v < 2;
