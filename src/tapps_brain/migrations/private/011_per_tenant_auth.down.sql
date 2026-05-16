-- Undo migration 011: remove per-tenant auth token columns from
-- project_profiles.

DROP INDEX IF EXISTS idx_project_profiles_has_token;

ALTER TABLE project_profiles
    DROP COLUMN IF EXISTS hashed_token,
    DROP COLUMN IF EXISTS token_created_at;

DELETE FROM private_schema_version WHERE version = 11;
