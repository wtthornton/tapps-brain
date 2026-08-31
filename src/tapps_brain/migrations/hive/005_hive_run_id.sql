-- TAP-6815: provenance on hive_memories — which invocation wrote the row.
--
-- `private_memories` gained `run_id` in private migration 031 (TAP-5544) and
-- `/v1/remember` fills it from `metadata.invocation_id` / the
-- `X-Origin-Invocation-Id` header (`http_adapter._extract_invocation_id`,
-- VAL-19).  The hive copy of the same write had nowhere to put it: the value
-- was dropped at `PostgresHiveBackend.save()`'s signature, not merely left
-- NULL.  A `share=true` remember therefore produced two rows, one joinable
-- back to AgentForge's `invocation_log` and one permanently anonymous.
--
-- Shape follows the `private_memories.run_id` precedent exactly — a nullable
-- TEXT column carrying an opaque caller-supplied identity, no FK (the
-- invocation table lives in a different service's database) and no CHECK
-- (the brain does not own the id's format).
--
-- NO BACKFILL, deliberately
-- ------------------------
-- 1,452 of the 4,429 existing hive rows have no same-key private counterpart,
-- so their originating invocation is already unrecoverable.  Deriving a
-- `run_id` for the remaining 2,977 by same-key join would be a guess about
-- which private write produced which hive copy, and any guess here
-- manufactures exactly the false provenance this column exists to prevent.
-- Pre-existing rows stay NULL: "unattributed" is the true answer for them.
--
-- Index
-- -----
-- The point of the column is the join back to an invocation, so the lookup is
-- `WHERE run_id = ...`.  Partial on NOT NULL because the overwhelming majority
-- of rows (every historical one, plus every write outside an invocation) carry
-- NULL and have no business in the index.
--
-- Idempotency: IF NOT EXISTS throughout, so the one-shot `tapps-brain-migrate`
-- sidecar can re-run it safely.

ALTER TABLE hive_memories
    ADD COLUMN IF NOT EXISTS run_id TEXT;

CREATE INDEX IF NOT EXISTS idx_hive_memories_run_id
    ON hive_memories (run_id)
    WHERE run_id IS NOT NULL;

INSERT INTO hive_schema_version (version, description)
VALUES (5, 'Add run_id invocation-provenance column to hive_memories (TAP-6815)');
