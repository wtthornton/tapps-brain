-- Undo migration 016: drop kg_entities table (CASCADE removes all
-- dependent indexes, policies, and FK constraints from child tables).
--
-- WARNING: kg_edges, kg_evidence, and kg_aliases all reference kg_entities
-- via FK ON DELETE CASCADE; those tables must be rolled back first if you
-- want to preserve their data.

DROP TABLE IF EXISTS kg_entities CASCADE;

DELETE FROM private_schema_version WHERE version = 16;
