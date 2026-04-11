-- EPIC-063 STORY-063.3: RLS spike — namespace isolation on hive_memories.
--
-- This migration enables Row Level Security (RLS) on hive_memories using a
-- session-variable pattern.  Two permissive policies are combined (OR logic):
--
--   hive_admin_bypass
--     Passes when no session namespace is set (NULL or empty string).
--     Intended for migrations, admin tooling, and legacy connections that do
--     not set the namespace context.  BOTH the USING (read) and WITH CHECK
--     (write) expressions are the same, so unscoped connections can INSERT
--     and DELETE freely.
--
--   hive_namespace_isolation
--     Passes when the row's namespace column matches the session variable
--     tapps.current_namespace (exact equality).
--     Intended for application connections that set the namespace context
--     before every query.
--
-- How permissive policies compose:
--   A row is visible (or writeable) when AT LEAST ONE permissive policy
--   passes.  This means:
--     session var unset (NULL or '')  → admin_bypass passes → all rows visible
--     session var = 'project-A'       → isolation policy matches → only
--                                       namespace='project-A' rows visible
--
-- Setting the session variable:
--   Use PostgresConnectionManager.namespace_context('<namespace>') which
--   executes:
--       SET LOCAL tapps.current_namespace = '<namespace>';
--   SET LOCAL is transaction-scoped and automatically cleared on commit or
--   rollback — safe for pooled connections.
--
-- FORCE ROW LEVEL SECURITY:
--   This migration does NOT enable FORCE RLS.  Table owners and superusers
--   bypass RLS by default.  Production deployments running as tapps_runtime
--   (a non-superuser; see migrations/roles/001_db_roles.sql) are subject to
--   these policies.  The integration test explicitly sets the session variable
--   so it verifies isolation without requiring a role switch.
--
-- Spike status (EPIC-063.3):
--   This is a proof-of-concept prior to the GA ship/defer decision in
--   STORY-063.4.  See docs/planning/adr/ for the ADR tracking that decision.
--
-- Security review:
--   - current_setting(..., TRUE): the TRUE flag means "missing_ok" — returns
--     NULL if the variable has never been set in this session.
--   - No user-supplied data flows into the policy expression; the namespace
--     value is a session variable set by trusted application code.
--   - Parameterized queries in application code already filter by namespace;
--     RLS adds a defence-in-depth layer at the DB level.

-- Enable RLS on hive_memories.
-- ENABLE is idempotent: safe to run even if already enabled.
ALTER TABLE hive_memories ENABLE ROW LEVEL SECURITY;

-- Drop policies before recreating for idempotent re-application.
DROP POLICY IF EXISTS hive_admin_bypass ON hive_memories;
DROP POLICY IF EXISTS hive_namespace_isolation ON hive_memories;

-- Policy 1: Admin bypass when session namespace is absent.
CREATE POLICY hive_admin_bypass ON hive_memories
    AS PERMISSIVE
    FOR ALL
    TO PUBLIC
    USING (
        current_setting('tapps.current_namespace', TRUE) IS NULL
        OR current_setting('tapps.current_namespace', TRUE) = ''
    )
    WITH CHECK (
        current_setting('tapps.current_namespace', TRUE) IS NULL
        OR current_setting('tapps.current_namespace', TRUE) = ''
    );

-- Policy 2: Namespace isolation when session namespace is set.
CREATE POLICY hive_namespace_isolation ON hive_memories
    AS PERMISSIVE
    FOR ALL
    TO PUBLIC
    USING (namespace = current_setting('tapps.current_namespace', TRUE))
    WITH CHECK (namespace = current_setting('tapps.current_namespace', TRUE));

-- Record this migration.
INSERT INTO hive_schema_version (version, description)
VALUES (2, 'RLS spike: namespace isolation policies on hive_memories (EPIC-063.3)');
