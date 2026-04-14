-- EPIC-069 STORY-069.8: Row Level Security on tenanted private-backend tables.
--
-- This migration adds RLS to the two tables that carry a tenant key
-- (project_id) in the private-backend schema:
--
--   private_memories   — per-(project_id, agent_id, key) memory rows.
--   project_profiles   — per-project MemoryProfile registry (EPIC-069 / 008).
--
-- Design summary
-- ==============
--
-- Session variables:
--   app.project_id   — the tenant identity for the current transaction.
--                      Set by PostgresConnectionManager.project_context()
--                      via SET LOCAL (transaction-scoped).
--   app.is_admin     — 'true' for registry / admin paths that must see
--                      every project.  Set by
--                      PostgresConnectionManager.admin_context().
--
-- current_setting(name, TRUE): the TRUE flag is missing_ok — returns NULL
-- when the variable has never been set in this session / transaction.
--
-- Policy shape
-- ------------
--
-- private_memories — fail-closed.
--   USING + WITH CHECK require current_setting('app.project_id', TRUE)
--   to be non-NULL AND equal to the row's project_id.  "No identity = no
--   access" is the safe default for tenant data, deliberately different
--   from the permissive admin_bypass pattern used on hive_memories.
--
-- project_profiles — admin bypass.
--   Two permissive policies:
--     project_profiles_admin_bypass
--       Passes when app.is_admin = 'true'.  Registry bookkeeping
--       (list_all, register, approve, delete) runs under admin_context
--       so it can see every row regardless of app.project_id.
--     project_profiles_tenant_isolation
--       Passes when project_id = current_setting('app.project_id', TRUE)
--       and the setting is non-NULL.  Used by callers that only need to
--       read their own tenant's profile row.
--
-- FORCE ROW LEVEL SECURITY
-- ------------------------
-- The tables are created (and owned) by tapps_migrator.  By default table
-- owners bypass RLS.  Production deployments connect as tapps_runtime
-- (non-superuser, non-owner — see migrations/roles/001_db_roles.sql) so
-- RLS is enforced on the runtime path without FORCE.  Migrations and
-- admin CLIs that connect as the owner intentionally bypass these policies
-- — that is the only path allowed to escape isolation.
--
-- We do NOT set FORCE ROW LEVEL SECURITY on either table: the admin /
-- migration role needs unfettered access to apply schema changes and to
-- seed data during onboarding.  If ops starts running the app as the
-- table owner, add FORCE ROW LEVEL SECURITY here and switch admin paths
-- to a dedicated role that also has BYPASSRLS.
--
-- Idempotency
-- -----------
-- ENABLE ROW LEVEL SECURITY is idempotent.  DROP POLICY IF EXISTS +
-- CREATE POLICY makes policy creation re-runnable.  Safe to re-apply.

-- ---------------------------------------------------------------------------
-- private_memories — fail-closed isolation
-- ---------------------------------------------------------------------------

ALTER TABLE private_memories ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS private_memories_tenant_isolation ON private_memories;

CREATE POLICY private_memories_tenant_isolation ON private_memories
    AS PERMISSIVE
    FOR ALL
    TO PUBLIC
    USING (
        current_setting('app.project_id', TRUE) IS NOT NULL
        AND current_setting('app.project_id', TRUE) <> ''
        AND project_id = current_setting('app.project_id', TRUE)
    )
    WITH CHECK (
        current_setting('app.project_id', TRUE) IS NOT NULL
        AND current_setting('app.project_id', TRUE) <> ''
        AND project_id = current_setting('app.project_id', TRUE)
    );

-- ---------------------------------------------------------------------------
-- project_profiles — tenant isolation with admin bypass
-- ---------------------------------------------------------------------------

ALTER TABLE project_profiles ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS project_profiles_admin_bypass ON project_profiles;
DROP POLICY IF EXISTS project_profiles_tenant_isolation ON project_profiles;

-- Policy 1: admin bypass — registry bookkeeping path.
CREATE POLICY project_profiles_admin_bypass ON project_profiles
    AS PERMISSIVE
    FOR ALL
    TO PUBLIC
    USING (
        current_setting('app.is_admin', TRUE) = 'true'
    )
    WITH CHECK (
        current_setting('app.is_admin', TRUE) = 'true'
    );

-- Policy 2: tenant isolation — per-project read of own profile.
CREATE POLICY project_profiles_tenant_isolation ON project_profiles
    AS PERMISSIVE
    FOR ALL
    TO PUBLIC
    USING (
        current_setting('app.project_id', TRUE) IS NOT NULL
        AND current_setting('app.project_id', TRUE) <> ''
        AND project_id = current_setting('app.project_id', TRUE)
    )
    WITH CHECK (
        current_setting('app.project_id', TRUE) IS NOT NULL
        AND current_setting('app.project_id', TRUE) <> ''
        AND project_id = current_setting('app.project_id', TRUE)
    );

-- Schema version bump.
INSERT INTO private_schema_version (version, description)
VALUES (9, 'RLS on private_memories and project_profiles (EPIC-069 STORY-069.8)');
