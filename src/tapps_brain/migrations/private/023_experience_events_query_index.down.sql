-- Undo migration 023: drop experience_events query index.

DROP INDEX IF EXISTS idx_experience_events_project_type_time;

DELETE FROM private_schema_version WHERE version = 23;
