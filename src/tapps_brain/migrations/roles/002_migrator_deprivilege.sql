-- TAP-2686: de-privilege the migrate sidecar role.
--
-- Goal: no SUPERUSER owns the tenanted tables, and the migrate sidecar runs
-- schema DDL as a NOSUPERUSER/NOBYPASSRLS role (tapps_migrator) so the
-- privileged-role guard passes WITHOUT TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE=1
-- (TAP-2673 made FORCE-RLS owners non-privileged for the guard).
--
-- Run as a SUPERUSER, ONCE per deploy, BEFORE the migrator-role DDL steps.
-- Idempotent: every statement is a no-op on re-run, and ownership reassignment
-- is guarded with IF EXISTS so it is safe on a fresh database (the tenanted
-- tables do not exist yet — the migrator will create and therefore own them).
--
-- This file does NOT set the migrator password: that comes from
-- docker/.env (TAPPS_BRAIN_MIGRATOR_PASSWORD) and is applied by
-- migrate-entrypoint.sh via ALTER ROLE, the same way the runtime password is.

-- ---------------------------------------------------------------------------
-- 1. Ensure tapps_migrator exists and is de-privileged.
--    NOSUPERUSER + NOBYPASSRLS is what lets it own FORCE-RLS tables without
--    defeating tenant isolation (the FORCE owner is subject to the policies).
-- ---------------------------------------------------------------------------

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tapps_migrator') THEN
    CREATE ROLE tapps_migrator WITH LOGIN NOSUPERUSER NOBYPASSRLS;
  ELSE
    ALTER ROLE tapps_migrator WITH NOSUPERUSER NOBYPASSRLS;
  END IF;
END;
$$;

-- The migrator runs DDL, so it needs CREATE on the schema (also granted by
-- roles/001, repeated here because roles/002 runs first in the bootstrap).
GRANT CREATE, USAGE ON SCHEMA public TO tapps_migrator;

-- The migrator must read + write existing tables to apply and TRACK migrations
-- (e.g. SELECT/INSERT on private_schema_version). Harmless belt-and-suspenders
-- alongside the ownership reassignment below; no-op on a fresh DB.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO tapps_migrator;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO tapps_migrator;

-- ---------------------------------------------------------------------------
-- 2. Reassign ownership of EVERY application table + sequence in public to
--    tapps_migrator. The migrator must own the objects it migrates — a later
--    migration may ALTER or DROP any of them (e.g. the IVFFlat->HNSW index swap
--    on hive_memories / federated_memories in TAP-2676 does DROP INDEX, which
--    requires owning the table the index belongs to). Reassigning only the two
--    tenanted tables is enough for the privileged-role guard but NOT for the
--    migrate path, so we reassign all of them.
--
--    A table's indexes and TOAST follow the table owner automatically, so this
--    covers indexes too. Idempotent: ALTER ... OWNER TO the current owner is a
--    no-op, and on a fresh DB there are no tables yet (the migrator creates and
--    therefore owns them). Restricted to ordinary + partitioned tables and
--    sequences in public — extensions, types, and functions are left alone.
-- ---------------------------------------------------------------------------

DO $$
DECLARE
  obj text;
BEGIN
  FOR obj IN
    SELECT c.relname
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relkind IN ('r', 'p')
      AND pg_get_userbyid(c.relowner) <> 'tapps_migrator'
  LOOP
    EXECUTE format('ALTER TABLE public.%I OWNER TO tapps_migrator', obj);
  END LOOP;

  FOR obj IN
    SELECT c.relname
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relkind = 'S'
      AND pg_get_userbyid(c.relowner) <> 'tapps_migrator'
  LOOP
    EXECUTE format('ALTER SEQUENCE public.%I OWNER TO tapps_migrator', obj);
  END LOOP;
END;
$$;
