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

ALTER DEFAULT PRIVILEGES FOR ROLE tapps_migrator IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO tapps_runtime;

ALTER DEFAULT PRIVILEGES FOR ROLE tapps_migrator IN SCHEMA public
  GRANT SELECT ON SEQUENCES TO tapps_readonly;

-- ---------------------------------------------------------------------------
-- 4. Existing objects — every table, sequence and function in the schema
--
--    These grants MUST be schema-wide rather than an explicit table list.
--    This file runs last (step 4 in the header order), so by the time it
--    executes every table from every applied migration already exists, and
--    the default privileges in section 3 cannot reach them: ALTER DEFAULT
--    PRIVILEGES only affects objects created *after* it runs.
--
--    This section previously named the migration-001 tables explicitly —
--    13 of them, across hive, federation and private.  The private schema is
--    now at migration 029 and the full schema carries ~43 tables, so every
--    table added by migrations 002-029 (session_chunks, experience_events and
--    its partitions, kg_*, idempotency_keys, audit_log, documents,
--    profile_scoped_data, gc_archive, feedback_events, diagnostics_history,
--    flywheel_meta, private_relations, ...) received no grant at all.  A
--    tapps_runtime provisioned by the documented order got "permission denied"
--    on the first such table it touched (TAP-5460).
--
--    Keep these schema-wide so the file stays correct as migrations are added;
--    section 3 still covers tables created after this file runs.
-- ---------------------------------------------------------------------------

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES    IN SCHEMA public TO tapps_runtime;
GRANT SELECT                          ON ALL TABLES    IN SCHEMA public TO tapps_readonly;

-- Sequences backing identity/serial columns (audit_log.id today); without
-- USAGE the runtime role can read the table but every INSERT fails.
GRANT USAGE, SELECT                   ON ALL SEQUENCES IN SCHEMA public TO tapps_runtime;
GRANT SELECT                          ON ALL SEQUENCES IN SCHEMA public TO tapps_readonly;

-- Trigger + search-vector functions (hive_memories_search_vector_update,
-- hive_memories_notify, federated_memories_search_vector_update, ...) are
-- called implicitly by DML triggers; the explicit grant also lets the runtime
-- role call them directly where required.
GRANT EXECUTE                         ON ALL FUNCTIONS IN SCHEMA public TO tapps_runtime;

-- ---------------------------------------------------------------------------
-- 5. Default privileges for the *owner* of the existing objects
--    Section 3 covers objects tapps_migrator creates later.  When the schema
--    was migrated by a different role (the Docker/CI path applies migrations
--    as the database owner), that role's future objects need the same
--    treatment, or the next migration reintroduces the gap this file closes.
-- ---------------------------------------------------------------------------

DO $$
DECLARE
  owner_role text;
BEGIN
  SELECT tableowner INTO owner_role
  FROM pg_tables
  WHERE schemaname = 'public'
  ORDER BY tablename
  LIMIT 1;

  IF owner_role IS NOT NULL AND owner_role <> 'tapps_migrator' THEN
    EXECUTE format(
      'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public '
      'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO tapps_runtime',
      owner_role
    );
    EXECUTE format(
      'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public '
      'GRANT SELECT ON TABLES TO tapps_readonly',
      owner_role
    );
    EXECUTE format(
      'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public '
      'GRANT USAGE, SELECT ON SEQUENCES TO tapps_runtime',
      owner_role
    );
    EXECUTE format(
      'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public '
      'GRANT EXECUTE ON FUNCTIONS TO tapps_runtime',
      owner_role
    );
  END IF;
END;
$$;

-- ---------------------------------------------------------------------------
-- 6. Explicit REVOKE — ensure tapps_runtime has NO DDL rights
--    (Belt-and-suspenders: these privileges are never granted above.)
-- ---------------------------------------------------------------------------

REVOKE CREATE ON SCHEMA public FROM tapps_runtime;
REVOKE CREATE ON SCHEMA public FROM tapps_readonly;
