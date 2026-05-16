-- Undo migration 005: drop audit_log table and its indexes.

DROP TABLE IF EXISTS audit_log CASCADE;

DELETE FROM private_schema_version WHERE version = 5;
