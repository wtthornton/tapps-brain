-- Undo migration 001: drop private_memories, private_schema_version,
-- trigger, and trigger function.
-- Run this file to fully undo the initial private-schema setup.
-- Drops extensions only if nothing else depends on them (safe guard).

DROP TRIGGER IF EXISTS trg_private_memories_search_vector ON private_memories;
DROP FUNCTION IF EXISTS private_memories_search_vector_update();
DROP TABLE IF EXISTS private_memories CASCADE;
DROP TABLE IF EXISTS private_schema_version CASCADE;
