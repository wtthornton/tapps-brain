-- EPIC-063 STORY-063.1: Least-privilege PostgreSQL roles for tapps-brain.
--
-- Run as superuser AFTER all schema migrations have been applied:
--   1. hive/001_initial.sql
--   2. federation/001_initial.sql
--   3. private/001_initial.sql
--   4. THIS FILE: roles/001_db_roles.sql
--
-- Idempotent: safe to re-apply on an already-configured database.
-- GRANT statements are no-ops if the privilege already exists (PG behaviour).
-- Role creation uses DO blocks so the script does not error on re-run.
--
-- Roles created:
--   tapps_migrator  — DDL role for schema migrations; owns schema objects.
--   tapps_runtime   — DML-only role used by the running application.
--   tapps_readonly  — SELECT-only role for reporting, debugging, read replicas.

-- ---------------------------------------------------------------------------
-- 1. Role creation (idempotent via existence check)
-- ---------------------------------------------------------------------------

DO $$
BEGIN
  -- tapps_migrator: used by deploy/CI jobs to apply schema migrations (DDL).
  -- Must NOT be used by the running application.
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tapps_migrator') THEN
    CREATE ROLE tapps_migrator WITH LOGIN;
  END IF;
END;
$$;

DO $$
BEGIN
  -- tapps_runtime: used exclusively by the running application (DML only).
  -- Cannot perform DDL (CREATE/ALTER/DROP), only data reads and writes.
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tapps_runtime') THEN
    CREATE ROLE tapps_runtime WITH LOGIN;
  END IF;
END;
$$;

DO $$
BEGIN
  -- tapps_readonly: SELECT-only; used for reporting, debugging, read replicas.
  -- Optional: create only when needed.
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tapps_readonly') THEN
    CREATE ROLE tapps_readonly WITH LOGIN;
  END IF;
END;
$$;

-- ---------------------------------------------------------------------------
-- 2. Schema-level privileges
-- ---------------------------------------------------------------------------

-- tapps_migrator owns schema objects and can create new ones.
GRANT CREATE  ON SCHEMA public TO tapps_migrator;
GRANT USAGE   ON SCHEMA public TO tapps_migrator;

-- tapps_runtime and tapps_readonly can resolve names in public schema.
GRANT USAGE ON SCHEMA public TO tapps_runtime;
GRANT USAGE ON SCHEMA public TO tapps_readonly;

-- ---------------------------------------------------------------------------
-- 3. Default privileges
--    Future tables/functions created by tapps_migrator are auto-granted.
--    This covers schema migrations added after this file is applied.
-- ---------------------------------------------------------------------------

ALTER DEFAULT PRIVILEGES FOR ROLE tapps_migrator IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO tapps_runtime;

ALTER DEFAULT PRIVILEGES FOR ROLE tapps_migrator IN SCHEMA public
  GRANT SELECT ON TABLES TO tapps_readonly;

ALTER DEFAULT PRIVILEGES FOR ROLE tapps_migrator IN SCHEMA public
  GRANT EXECUTE ON FUNCTIONS TO tapps_runtime;

-- ---------------------------------------------------------------------------
-- 4. Existing tables — Hive backend
--    Grant on current tables (already created by hive/001_initial.sql).
-- ---------------------------------------------------------------------------

GRANT SELECT, INSERT, UPDATE, DELETE ON
  hive_memories,
  hive_groups,
  hive_group_members,
  hive_feedback_events,
  agent_registry,
  hive_write_notify,
  hive_schema_version
TO tapps_runtime;

GRANT SELECT ON
  hive_memories,
  hive_groups,
  hive_group_members,
  hive_feedback_events,
  agent_registry,
  hive_write_notify,
  hive_schema_version
TO tapps_readonly;

-- Hive trigger functions (called implicitly by DML triggers; explicit grant
-- ensures runtime role can also call them directly if required).
GRANT EXECUTE ON FUNCTION
  hive_memories_search_vector_update(),
  hive_memories_notify()
TO tapps_runtime;

-- ---------------------------------------------------------------------------
-- 5. Existing tables — Federation backend
--    Grant on current tables (already created by federation/001_initial.sql).
-- ---------------------------------------------------------------------------

GRANT SELECT, INSERT, UPDATE, DELETE ON
  federated_memories,
  federation_subscriptions,
  federation_meta,
  federation_schema_version
TO tapps_runtime;

GRANT SELECT ON
  federated_memories,
  federation_subscriptions,
  federation_meta,
  federation_schema_version
TO tapps_readonly;

GRANT EXECUTE ON FUNCTION
  federated_memories_search_vector_update()
TO tapps_runtime;

-- ---------------------------------------------------------------------------
-- 6. Existing tables — Private memory backend
--    Grant on current tables (already created by private/001_initial.sql).
-- ---------------------------------------------------------------------------

GRANT SELECT, INSERT, UPDATE, DELETE ON
  private_memories,
  private_schema_version
TO tapps_runtime;

GRANT SELECT ON
  private_memories,
  private_schema_version
TO tapps_readonly;

-- ---------------------------------------------------------------------------
-- 7. Explicit REVOKE — ensure tapps_runtime has NO DDL rights
--    (Belt-and-suspenders: these privileges are never granted above.)
-- ---------------------------------------------------------------------------

REVOKE CREATE ON SCHEMA public FROM tapps_runtime;
REVOKE CREATE ON SCHEMA public FROM tapps_readonly;
