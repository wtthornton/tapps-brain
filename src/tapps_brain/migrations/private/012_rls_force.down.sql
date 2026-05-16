-- Undo migration 012: remove FORCE ROW LEVEL SECURITY from private_memories
-- and project_profiles, reverting to the non-forced RLS state of migration 009.

ALTER TABLE private_memories  NO FORCE ROW LEVEL SECURITY;
ALTER TABLE project_profiles  NO FORCE ROW LEVEL SECURITY;

DELETE FROM private_schema_version WHERE version = 12;
