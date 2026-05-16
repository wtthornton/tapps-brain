-- Undo migration 004: drop diagnostics_history table and its indexes.

DROP TABLE IF EXISTS diagnostics_history CASCADE;

DELETE FROM private_schema_version WHERE version = 4;
