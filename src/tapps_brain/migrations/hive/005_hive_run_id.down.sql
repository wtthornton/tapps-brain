-- Revert TAP-6815: drop the hive_memories provenance column.
--
-- Dropping the column discards every recorded invocation id.  There is nowhere
-- else in this schema to park them, and the pre-005 code path cannot read it
-- anyway, so the loss is inherent to the rollback rather than something this
-- file could mitigate.  For local rollback testing on a throwaway container,
-- never prod.

DROP INDEX IF EXISTS idx_hive_memories_run_id;

ALTER TABLE hive_memories
    DROP COLUMN IF EXISTS run_id;

DELETE FROM hive_schema_version WHERE version = 5;
