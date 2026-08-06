-- Revert migration 030 (learning_status): drop the promotion axis from
-- private_memories.
--
-- Promotion state is lost, not archived: without the columns there is nowhere
-- to keep it.  Re-applying 030 leaves every row a `candidate` again, which is
-- the correct fail-safe — an approval that cannot be proven is not an approval.

DROP INDEX IF EXISTS idx_private_memories_learning_status;

ALTER TABLE private_memories
    DROP CONSTRAINT IF EXISTS private_memories_approved_needs_provenance;

ALTER TABLE private_memories
    DROP COLUMN IF EXISTS learning_status,
    DROP COLUMN IF EXISTS promoted_by,
    DROP COLUMN IF EXISTS promoted_at,
    DROP COLUMN IF EXISTS promotion_signal,
    DROP COLUMN IF EXISTS demotion_reason;

DELETE FROM private_schema_version WHERE version = 30;
