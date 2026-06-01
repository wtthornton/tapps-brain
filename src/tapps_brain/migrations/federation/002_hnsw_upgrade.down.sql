-- Undo TAP-2676: revert federated_memories embedding index from HNSW to IVFFlat.

DROP INDEX IF EXISTS idx_fed_embedding_hnsw;

CREATE INDEX IF NOT EXISTS idx_fed_embedding_ivfflat
    ON federated_memories USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

DELETE FROM federation_schema_version WHERE version = 2;
