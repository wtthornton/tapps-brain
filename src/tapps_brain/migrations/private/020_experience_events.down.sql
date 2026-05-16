-- Undo migration 020: drop the experience_events partitioned table and
-- all of its partitions (CASCADE).

DROP TABLE IF EXISTS experience_events CASCADE;

DELETE FROM private_schema_version WHERE version = 20;
