-- EPIC-070 STORY-070.8: Per-tenant auth tokens for project_profiles.
--
-- Each project can optionally hold a single active bearer token whose
-- argon2id hash is stored here.  The plaintext token is NEVER stored;
-- only the hash is persisted.
--
-- When TAPPS_BRAIN_PER_TENANT_AUTH=1 the HTTP adapter verifies incoming
-- bearer tokens against this column, scoped to the project_id extracted
-- from the X-Project-Id header.  If a project has no hashed_token the
-- adapter falls back to the global TAPPS_BRAIN_AUTH_TOKEN check so
-- deployments that haven't set up per-tenant tokens continue to work.
--
-- Token lifecycle (CLI):
--   tapps-brain project rotate-token <slug>   — issue/replace; prints plaintext once
--   tapps-brain project revoke-token  <slug>  — clears the hash (token_created_at → NULL)
--
-- ADD COLUMN IF NOT EXISTS makes this migration safe to re-run.

ALTER TABLE project_profiles
    ADD COLUMN IF NOT EXISTS hashed_token      TEXT,
    ADD COLUMN IF NOT EXISTS token_created_at  TIMESTAMPTZ;

-- Index for admin listings that want to know which projects have active tokens.
CREATE INDEX IF NOT EXISTS idx_project_profiles_has_token
    ON project_profiles (project_id)
    WHERE hashed_token IS NOT NULL;

-- Schema version bump.
INSERT INTO private_schema_version (version, description)
VALUES (11, 'per-tenant auth tokens for project_profiles (STORY-070.8)');
