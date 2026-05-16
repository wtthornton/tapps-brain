-- Undo migration 006: drop gc_archive table and its indexes.

DROP TABLE IF EXISTS gc_archive CASCADE;

DELETE FROM private_schema_version WHERE version = 6;
