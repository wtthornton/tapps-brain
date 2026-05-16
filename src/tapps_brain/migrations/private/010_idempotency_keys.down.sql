-- Undo migration 010: drop idempotency_keys table and its indexes.

DROP TABLE IF EXISTS idempotency_keys CASCADE;

DELETE FROM private_schema_version WHERE version = 10;
