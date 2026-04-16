-- TAP-512: FORCE ROW LEVEL SECURITY on tenanted private-backend tables.
--
-- 009_project_rls.sql enabled RLS but deliberately did NOT set FORCE,
-- relying on the deployment connecting as a non-owner role
-- (tapps_runtime — see roles/001_db_roles.sql).  That assumption is
-- silent: if ops accidentally deploys as the table owner, RLS is
-- bypassed and tenant isolation breaks with no error.
--
-- This migration closes that gap by enabling FORCE on the two tenanted
-- tables.  After this migration:
--
--   * private_memories: every connection (owner included) must have
--     app.project_id set; the fail-closed policy hides rows otherwise.
--     There is intentionally NO admin-bypass policy — admin/maintenance
--     against this table must connect as a role with BYPASSRLS, or
--     temporarily DISABLE the table's RLS.
--   * project_profiles: admin paths still work via the existing
--     app.is_admin='true' bypass policy (set by admin_context() in
--     postgres_connection.py); per-tenant reads use the
--     tenant_isolation policy as before.  No code change needed.
--
-- Schema-only DDL run by the migrator role is unaffected — RLS only
-- gates DML.  Migrations that need to seed data into these tables
-- (none today) must wrap the inserts in either app.is_admin='true' (for
-- project_profiles) or a SET LOCAL app.project_id (for private_memories).
--
-- ALTER TABLE … FORCE ROW LEVEL SECURITY is idempotent.

ALTER TABLE private_memories  FORCE ROW LEVEL SECURITY;
ALTER TABLE project_profiles  FORCE ROW LEVEL SECURITY;

INSERT INTO private_schema_version (version, description)
VALUES (12, 'FORCE ROW LEVEL SECURITY on private_memories and project_profiles (TAP-512)');
