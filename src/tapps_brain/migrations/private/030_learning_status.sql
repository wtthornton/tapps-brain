-- TAP-5542: gated learning — promotion status + provenance on private_memories.
--
-- Promotion is a TRUST axis, independent of the LIFECYCLE axis that migration
-- 027 added.  `status` answers "is this row live" (active/stale/superseded/
-- archived); `learning_status` answers "has this learning been validated".
-- An `active` row can be a `candidate`, and an `approved` learning can later go
-- `stale`, so folding promotion into 027's CHECK would make `approved` and
-- `stale` mutually exclusive — which is wrong.
--
--   candidate  Default for any agent-emitted learning.  Not eligible for injection.
--   approved   Passed an explicit gate.  Eligible for injection.
--   demoted    Was approved, then contradicted or decayed.  Not eligible.
--
-- Existing rows land as `candidate`: nothing written before this migration
-- passed a gate, so claiming otherwise would be a lie the recall path acts on.
--
-- The load-bearing rule (see docs/engineering/gated-learning-contract.md):
-- frequency alone cannot approve.  `reinforce()` raises confidence and access
-- count and must never move candidate -> approved; promotion requires an
-- explicit `eval` or `human` signal.  The provenance CHECK below enforces that
-- at the storage layer, so an approval with no recorded signal cannot exist
-- even if a future code path forgets.
--
-- Idempotency
-- -----------
-- Guarded by IF NOT EXISTS / catalog lookups so the migration is re-runnable.

ALTER TABLE private_memories
    ADD COLUMN IF NOT EXISTS learning_status VARCHAR(20) NOT NULL DEFAULT 'candidate'
        CHECK (learning_status IN ('candidate', 'approved', 'demoted')),
    ADD COLUMN IF NOT EXISTS promoted_by TEXT,
    ADD COLUMN IF NOT EXISTS promoted_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS promotion_signal VARCHAR(10)
        CHECK (promotion_signal IN ('eval', 'human')),
    ADD COLUMN IF NOT EXISTS demotion_reason TEXT;

-- An approved row must carry who approved it, when, and on what signal.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname = 'private_memories_approved_needs_provenance'
           AND conrelid = 'private_memories'::regclass
    ) THEN
        ALTER TABLE private_memories
            ADD CONSTRAINT private_memories_approved_needs_provenance
            CHECK (
                learning_status <> 'approved'
                OR (
                    promoted_by IS NOT NULL
                    AND promoted_at IS NOT NULL
                    AND promotion_signal IS NOT NULL
                )
            );
    END IF;
END
$$;

-- Supports approved-only recall (POST /v1/recall:tool_paths defaults to
-- learning_status = 'approved').
CREATE INDEX IF NOT EXISTS idx_private_memories_learning_status
    ON private_memories (project_id, agent_id, learning_status);

INSERT INTO private_schema_version (version, description)
VALUES (30, 'Add learning_status + promotion provenance to private_memories (TAP-5542)');
