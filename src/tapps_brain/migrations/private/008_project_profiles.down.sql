-- Undo migration 008: drop project_profiles table, its trigger, and
-- trigger function.

DROP TRIGGER IF EXISTS trg_project_profiles_touch ON project_profiles;
DROP FUNCTION IF EXISTS project_profiles_touch_updated_at();
DROP TABLE IF EXISTS project_profiles CASCADE;

DELETE FROM private_schema_version WHERE version = 8;
