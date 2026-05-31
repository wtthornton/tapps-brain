-- Undo TAP-2686: reassign the tenanted tables back to the superuser owner
-- (tapps). For manual rollback only; the roles/ migrations are applied by
-- migrate-entrypoint.sh via psql -f, not the version-table loader.

DO $$
DECLARE
  tbl text;
  seq text;
BEGIN
  FOREACH tbl IN ARRAY ARRAY['private_memories', 'project_profiles'] LOOP
    IF EXISTS (
      SELECT 1 FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
      WHERE n.nspname = 'public' AND c.relname = tbl AND c.relkind = 'r'
    ) THEN
      EXECUTE format('ALTER TABLE public.%I OWNER TO tapps', tbl);
      FOR seq IN
        SELECT s.relname
        FROM pg_class s
        JOIN pg_depend d ON d.objid = s.oid AND d.deptype = 'a'
        JOIN pg_class t ON t.oid = d.refobjid
        WHERE s.relkind = 'S' AND t.relname = tbl
      LOOP
        EXECUTE format('ALTER SEQUENCE public.%I OWNER TO tapps', seq);
      END LOOP;
    END IF;
  END LOOP;
END;
$$;
