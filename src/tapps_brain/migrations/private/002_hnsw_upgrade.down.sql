-- Undo migration 002: revert embedding index from HNSW back to IVFFlat.

DROP INDEX IF EXISTS idx_priv_embedding_hnsw;

CREATE INDEX IF NOT EXISTS idx_priv_embedding_ivfflat
    ON private_memories USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

DELETE FROM private_schema_version WHERE version = 2;
