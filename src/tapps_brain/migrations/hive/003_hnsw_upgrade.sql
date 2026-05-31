-- TAP-2676: upgrade hive_memories embedding index from IVFFlat to HNSW.
-- Mirrors private/002_hnsw_upgrade.sql so the "pgvector HNSW everywhere" design
-- decision in CLAUDE.md matches what the database actually has. Hive was left on
-- the original IVFFlat index from 001_initial.sql; private was already migrated.
--
-- 2026 pgvector consensus: HNSW is the safer default for RAG/semantic-recall
-- workloads — ~1.5× faster than a tuned IVFFlat at comparable recall, better
-- tolerance of concurrent writes, no "build after bulk load" footgun. IVFFlat
-- only wins on build time / memory at >50M rows, not the hive-memory profile.
--
-- Parameters (identical to private):
--   m = 16                — graph connectivity (pgvector default, good recall)
--   ef_construction = 200 — build-time effort (doubled from default 64)
--
-- Callers tune ef_search at query time via
--   SET LOCAL hnsw.ef_search = 80;
-- inside a transaction before recall; 40 is the pgvector default.

DROP INDEX IF EXISTS idx_hive_embedding_ivfflat;

CREATE INDEX IF NOT EXISTS idx_hive_embedding_hnsw
    ON hive_memories
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 200);

INSERT INTO hive_schema_version (version, description)
VALUES (3, 'Upgrade hive_memories embedding index from IVFFlat to HNSW (TAP-2676)');
