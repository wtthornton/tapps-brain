-- Undo migration 019: drop kg_aliases table and its indexes.

DROP TABLE IF EXISTS kg_aliases CASCADE;

DELETE FROM private_schema_version WHERE version = 19;
