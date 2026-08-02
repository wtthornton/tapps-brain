-- Scope idempotency keys to the operation that produced them.
--
-- Migration 010 keyed ``idempotency_keys`` on ``(key, project_id)`` alone.  A
-- client that reuses one key across two different write operations therefore
-- gets the FIRST operation's response body replayed for the SECOND call, and
-- the second write is skipped entirely — a silent lost write that reports
-- success.  Adding ``operation`` to the key restores per-operation semantics:
-- a key only replays a response for the same operation it was stored under.
--
-- ``operation`` is the HTTP route path (``/v1/remember``) or the MCP tool name
-- (``memory_save``).  Pre-existing rows are backfilled with ``''`` and expire
-- naturally within the 24 h TTL.
--
-- Idempotency
-- -----------
-- Guarded by IF NOT EXISTS / catalog lookups so the migration is re-runnable.

-- ---------------------------------------------------------------------------
-- idempotency_keys.operation
-- ---------------------------------------------------------------------------

ALTER TABLE idempotency_keys
    ADD COLUMN IF NOT EXISTS operation TEXT NOT NULL DEFAULT '';

-- Widen the primary key to include the operation.  The old two-column key is
-- dropped only when it is still the active constraint, so re-running is safe.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname = 'idempotency_keys_pkey'
           AND conrelid = 'idempotency_keys'::regclass
           AND array_length(conkey, 1) = 2
    ) THEN
        ALTER TABLE idempotency_keys DROP CONSTRAINT idempotency_keys_pkey;
        ALTER TABLE idempotency_keys
            ADD CONSTRAINT idempotency_keys_pkey
            PRIMARY KEY (key, project_id, operation);
    END IF;
END
$$;

-- Schema version bump.
INSERT INTO private_schema_version (version, description)
VALUES (29, 'Scope idempotency keys by operation (TAP-5442 sibling)');
