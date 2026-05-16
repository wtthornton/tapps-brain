-- Undo migration 018: drop kg_evidence table (CASCADE removes indexes
-- and the FK from kg_aliases.evidence_id).

DROP TABLE IF EXISTS kg_evidence CASCADE;

DELETE FROM private_schema_version WHERE version = 18;
