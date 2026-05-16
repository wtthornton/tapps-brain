-- Undo migration hive/001: drop all Hive-schema tables, triggers, and
-- trigger functions created by the initial Hive migration.

DROP TRIGGER IF EXISTS trg_hive_memories_notify ON hive_memories;
DROP FUNCTION IF EXISTS hive_memories_notify();
DROP TRIGGER IF EXISTS trg_hive_memories_search_vector ON hive_memories;
DROP FUNCTION IF EXISTS hive_memories_search_vector_update();

DROP TABLE IF EXISTS hive_write_notify CASCADE;
DROP TABLE IF EXISTS hive_feedback_events CASCADE;
DROP TABLE IF EXISTS hive_group_members CASCADE;
DROP TABLE IF EXISTS hive_groups CASCADE;
DROP TABLE IF EXISTS agent_registry CASCADE;
DROP TABLE IF EXISTS hive_memories CASCADE;
DROP TABLE IF EXISTS hive_schema_version CASCADE;
