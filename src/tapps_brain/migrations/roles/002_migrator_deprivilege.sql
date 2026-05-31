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
-- (e.g. SELECT/INSERT on private_schema_version, which it does not own on an
-- already-provisioned DB). These grants do NOT affect the privileged-role guard
-- (which only inspects OWNERSHIP of the tenanted tables, not grants), and are
-- no-ops on a fresh DB where no tables exist yet (the migrator creates and owns
-- them). Future migrations that ALTER a pre-cutover table the migrator does not
-- own still need owner-level DDL — see the cutover runbook.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO tapps_migrator;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO tapps_migrator;

-- ---------------------------------------------------------------------------
-- 2. Reassign ownership of the tenanted tables (and any sequences they own)
--    from the superuser owner to tapps_migrator. IF EXISTS guards make this a
--    no-op on a fresh DB (tables not yet created) and on re-run (already owned
--    by tapps_migrator).
-- ---------------------------------------------------------------------------

DO $$
DECLARE
  tbl  text;
  seq  text;
BEGIN
  FOREACH tbl IN ARRAY ARRAY['private_memories', 'project_profiles'] LOOP
    IF EXISTS (
      SELECT 1 FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
      WHERE n.nspname = 'public' AND c.relname = tbl AND c.relkind = 'r'
    ) THEN
      EXECUTE format('ALTER TABLE public.%I OWNER TO tapps_migrator', tbl);

      -- Reassign any sequences owned by (depending on) this table.
      FOR seq IN
        SELECT s.relname
        FROM pg_class s
        JOIN pg_depend d ON d.objid = s.oid AND d.deptype = 'a'
        JOIN pg_class t ON t.oid = d.refobjid
        WHERE s.relkind = 'S' AND t.relname = tbl
      LOOP
        EXECUTE format('ALTER SEQUENCE public.%I OWNER TO tapps_migrator', seq);
      END LOOP;
    END IF;
  END LOOP;
END;
$$;
