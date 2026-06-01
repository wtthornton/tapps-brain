-- Undo TAP-2676: revert hive_memories embedding index from HNSW to IVFFlat.

DROP INDEX IF EXISTS idx_hive_embedding_hnsw;

CREATE INDEX IF NOT EXISTS idx_hive_embedding_ivfflat
    ON hive_memories USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

DELETE FROM hive_schema_version WHERE version = 3;
