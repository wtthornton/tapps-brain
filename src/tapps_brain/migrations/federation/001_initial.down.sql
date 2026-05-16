-- Undo migration federation/001: drop all Federation-schema tables,
-- triggers, and trigger functions created by the initial Federation migration.

DROP TRIGGER IF EXISTS trg_federated_memories_search_vector ON federated_memories;
DROP FUNCTION IF EXISTS federated_memories_search_vector_update();

DROP TABLE IF EXISTS federation_meta CASCADE;
DROP TABLE IF EXISTS federation_subscriptions CASCADE;
DROP TABLE IF EXISTS federated_memories CASCADE;
DROP TABLE IF EXISTS federation_schema_version CASCADE;
