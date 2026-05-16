-- Undo migration 007: drop flywheel_meta table.

DROP TABLE IF EXISTS flywheel_meta CASCADE;

DELETE FROM private_schema_version WHERE version = 7;
