-- Undo migration 017: drop kg_edges table (CASCADE removes indexes and
-- FK constraints from kg_evidence and kg_aliases that reference it).

DROP TABLE IF EXISTS kg_edges CASCADE;

DELETE FROM private_schema_version WHERE version = 17;
