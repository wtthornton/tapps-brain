-- Undo migration 021: remove confidence_history column and its GIN index
-- from kg_edges.

DROP INDEX IF EXISTS idx_kg_edges_confidence_history_gin;

ALTER TABLE kg_edges
    DROP COLUMN IF EXISTS confidence_history;

DELETE FROM private_schema_version WHERE version = 21;
