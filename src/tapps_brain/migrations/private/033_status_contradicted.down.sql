-- Revert migration 033 (TAP-6697): narrow status back to the 027 vocabulary.
--
-- NARROWING A CHECK LOSES DATA SEMANTICS.  Any row close_validity() closed with
-- reason='contradiction' carries status='contradicted', which the 027 constraint
-- rejects.  Those rows are rewritten to 'superseded' first -- the closest 027
-- member that still means "not live" -- rather than dropped or reset to 'active'
-- (resetting would resurrect contradicted rows into recall, which is the exact
-- failure TAP-6697 closed).  The contradicted BOOLEAN column is untouched, so the
-- distinction survives the downgrade there.

UPDATE private_memories SET status = 'superseded' WHERE status = 'contradicted';

ALTER TABLE private_memories
    DROP CONSTRAINT IF EXISTS private_memories_status_check;

ALTER TABLE private_memories
    ADD CONSTRAINT private_memories_status_check
    CHECK (status IN ('active', 'stale', 'superseded', 'archived'));

COMMENT ON COLUMN private_memories.status IS NULL;
COMMENT ON COLUMN private_memories.valid_from IS NULL;
COMMENT ON COLUMN private_memories.valid_until IS NULL;

DELETE FROM private_schema_version WHERE version = 33;
