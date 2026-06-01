-- Undo TAP-2686: reassign every application table + sequence in public back to
-- the superuser owner (tapps). For manual rollback only; the roles/ migrations
-- are applied by migrate-entrypoint.sh via psql -f, not the version-table
-- loader. Mirrors the forward migration's all-tables reassignment.

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
      AND pg_get_userbyid(c.relowner) = 'tapps_migrator'
  LOOP
    EXECUTE format('ALTER TABLE public.%I OWNER TO tapps', obj);
  END LOOP;

  FOR obj IN
    SELECT c.relname
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relkind = 'S'
      AND pg_get_userbyid(c.relowner) = 'tapps_migrator'
  LOOP
    EXECUTE format('ALTER SEQUENCE public.%I OWNER TO tapps', obj);
  END LOOP;
END;
$$;
