-- Undo migration 009: remove RLS policies from private_memories and
-- project_profiles, then disable RLS on both tables.

DROP POLICY IF EXISTS private_memories_tenant_isolation ON private_memories;
ALTER TABLE private_memories DISABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS project_profiles_admin_bypass ON project_profiles;
DROP POLICY IF EXISTS project_profiles_tenant_isolation ON project_profiles;
ALTER TABLE project_profiles DISABLE ROW LEVEL SECURITY;

DELETE FROM private_schema_version WHERE version = 9;
