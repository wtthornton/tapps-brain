-- Undo migration hive/002: remove RLS policies from hive_memories and
-- disable row level security.

DROP POLICY IF EXISTS hive_admin_bypass ON hive_memories;
DROP POLICY IF EXISTS hive_namespace_isolation ON hive_memories;
ALTER TABLE hive_memories DISABLE ROW LEVEL SECURITY;

DELETE FROM hive_schema_version WHERE version = 2;
