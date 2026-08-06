-- Revert migration 031 (mission scope): drop the mission companion columns.
--
-- Any row still carrying scope = 'mission' would be left with no owning
-- mission, so the scope value is folded back to 'project' before the columns
-- go.  Losing the mission association is unavoidable — there is nowhere to
-- keep it — and leaving orphaned 'mission' rows behind would strand state that
-- no mission API could ever reach again.

UPDATE private_memories SET scope = 'project' WHERE scope = 'mission';

DROP INDEX IF EXISTS idx_private_memories_mission;

ALTER TABLE private_memories
    DROP CONSTRAINT IF EXISTS private_memories_mission_scope_needs_mission_id;

ALTER TABLE private_memories
    DROP COLUMN IF EXISTS mission_id,
    DROP COLUMN IF EXISTS run_id;

DELETE FROM private_schema_version WHERE version = 31;
