-- EPIC-059 STORY-059.5 follow-up: upgrade private_memories embedding index
-- from IVFFlat to HNSW. 2026 pgvector consensus: HNSW is the safer default for
-- RAG/semantic-recall workloads — ~1.5× faster than a tuned IVFFlat at
-- comparable recall, better tolerance of concurrent writes, no "build after
-- bulk load" footgun. IVFFlat only wins on build time / memory at >50M rows,
-- which is not the private-memory scale profile.
--
-- Parameters:
--   m = 16                — graph connectivity (pgvector default, good recall)
--   ef_construction = 200 — build-time effort (doubled from default 64 for
--                           stronger recall without a large build-time hit on
--                           the small private-memory tables)
--
-- Callers should tune ef_search at query time via
--   SET LOCAL hnsw.ef_search = 80;
-- inside a transaction before running recall; 40 is the pgvector default.

DROP INDEX IF EXISTS idx_priv_embedding_ivfflat;

CREATE INDEX IF NOT EXISTS idx_priv_embedding_hnsw
    ON private_memories
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 200);

INSERT INTO private_schema_version (version, description)
VALUES (2, 'Upgrade private_memories embedding index from IVFFlat to HNSW (2026 default)');
