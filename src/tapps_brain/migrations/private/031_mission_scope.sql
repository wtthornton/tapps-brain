-- TAP-5544: mission-scoped shared state — mission_id + run_id on private_memories.
--
-- Mission scope follows the `scope=branch` precedent exactly: a MemoryScope
-- value plus a companion column that is required when that scope is set.
-- `branch` (migration 001) answers "which git branch owns this row";
-- `mission_id` answers "which mission owns this row".  Reusing `branch` for
-- both would have made a branch-scoped and a mission-scoped row
-- indistinguishable at the storage layer.
--
--   mission_id  Set when scope = 'mission'.  The isolation key.
--   run_id      Optional narrowing within a mission, so a retry can keep its
--               own findings without clobbering the mission-level record.
--
-- Existing rows get NULL for both, which is correct: nothing written before
-- this migration belonged to a mission.
--
-- Isolation
-- ---------
-- Two missions under ONE project_id must not read each other.  The RLS policy
-- from migration 009 gates on project_id only, so it cannot provide this — the
-- mission predicate does, via the index below plus an explicit mission_id
-- equality check on the read path.  The DB constraint here makes the invariant
-- unrepresentable rather than merely conventional: a 'mission'-scoped row with
-- no mission_id cannot be inserted.
--
-- Idempotency
-- -----------
-- Guarded by IF NOT EXISTS / catalog lookups so the migration is re-runnable.

ALTER TABLE private_memories
    ADD COLUMN IF NOT EXISTS mission_id TEXT,
    ADD COLUMN IF NOT EXISTS run_id TEXT;

-- A mission-scoped row must name its mission.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname = 'private_memories_mission_scope_needs_mission_id'
           AND conrelid = 'private_memories'::regclass
    ) THEN
        ALTER TABLE private_memories
            ADD CONSTRAINT private_memories_mission_scope_needs_mission_id
            CHECK (scope <> 'mission' OR mission_id IS NOT NULL);
    END IF;
END
$$;

-- Supports mission-scoped state lookup (POST /v1/mission/state:get).
CREATE INDEX IF NOT EXISTS idx_private_memories_mission
    ON private_memories (project_id, agent_id, mission_id)
    WHERE mission_id IS NOT NULL;

INSERT INTO private_schema_version (version, description)
VALUES (31, 'Add mission_id + run_id (mission scope) to private_memories (TAP-5544)');
