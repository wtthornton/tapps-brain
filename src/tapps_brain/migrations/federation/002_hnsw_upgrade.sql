-- TAP-2676: upgrade federated_memories embedding index from IVFFlat to HNSW.
-- Mirrors private/002_hnsw_upgrade.sql and hive/003_hnsw_upgrade.sql so the
-- "pgvector HNSW everywhere" design decision in CLAUDE.md matches reality.
-- Federation was left on the original IVFFlat index from 001_initial.sql.
--
-- 2026 pgvector consensus: HNSW is the safer default for RAG/semantic-recall
-- workloads — ~1.5× faster than a tuned IVFFlat at comparable recall, better
-- tolerance of concurrent writes, no "build after bulk load" footgun.
--
-- Parameters (identical to private + hive):
--   m = 16                — graph connectivity (pgvector default, good recall)
--   ef_construction = 200 — build-time effort (doubled from default 64)

DROP INDEX IF EXISTS idx_fed_embedding_ivfflat;

CREATE INDEX IF NOT EXISTS idx_fed_embedding_hnsw
    ON federated_memories
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 200);

INSERT INTO federation_schema_version (version, description)
VALUES (2, 'Upgrade federated_memories embedding index from IVFFlat to HNSW (TAP-2676)');
