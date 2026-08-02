-- Revert migration 029: drop the operation column from idempotency_keys.
--
-- Rows whose ``(key, project_id)`` pair is duplicated across operations must be
-- collapsed before the narrow primary key can be restored; the newest row per
-- pair wins.  Idempotency keys are TTL-bounded (24 h), so this loses only
-- replay ability, never a committed write.

DELETE FROM idempotency_keys a
      USING idempotency_keys b
      WHERE a.key = b.key
        AND a.project_id = b.project_id
        AND a.created_at < b.created_at;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname = 'idempotency_keys_pkey'
           AND conrelid = 'idempotency_keys'::regclass
           AND array_length(conkey, 1) = 3
    ) THEN
        ALTER TABLE idempotency_keys DROP CONSTRAINT idempotency_keys_pkey;
        ALTER TABLE idempotency_keys
            ADD CONSTRAINT idempotency_keys_pkey
            PRIMARY KEY (key, project_id);
    END IF;
END
$$;

ALTER TABLE idempotency_keys DROP COLUMN IF EXISTS operation;

DELETE FROM private_schema_version WHERE version = 29;
