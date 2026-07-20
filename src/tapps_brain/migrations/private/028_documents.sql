-- TAP-5002 (EPIC TAP-4998): document plane — durable documents beside vector RAG.
--
-- Two tables:
--   documents        — original bytes + metadata, keyed (project_id, doc_id).
--                      agent_id records the writer; reads are project-scoped
--                      (knowledge sharing across agents on one project).
--   document_chunks  — deterministic chunks for hybrid retrieval: tsvector
--                      (lexical) + vector(384) (semantic, same model as
--                      private_memories embeddings).
--
-- Design: docs/planning/DESIGN-DOCUMENT-STORE.md.  Explicitly NOT a TTL byte
-- cache (AgentForge ADR-040 owns that); expires_at is retention policy swept
-- by MemoryStore.gc().

CREATE TABLE IF NOT EXISTS documents (
    project_id    TEXT         NOT NULL,
    agent_id      TEXT         NOT NULL,
    doc_id        TEXT         NOT NULL,
    title         TEXT         NOT NULL,
    content_type  TEXT         NOT NULL DEFAULT 'text/plain',
    content       BYTEA        NOT NULL,
    size_bytes    BIGINT       NOT NULL,
    sha256        TEXT         NOT NULL,
    tags          TEXT[]       NOT NULL DEFAULT '{}',
    index_status  TEXT         NOT NULL DEFAULT 'none'
        CHECK (index_status IN ('none', 'pending', 'indexed', 'error')),
    index_error   TEXT,                              -- diagnostic when index_status='error'
    retention     TEXT         NOT NULL DEFAULT 'project',
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    expires_at    TIMESTAMPTZ,                       -- NULL = keep until deleted

    PRIMARY KEY (project_id, doc_id)
);

CREATE INDEX IF NOT EXISTS idx_documents_created_at
    ON documents (project_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_documents_expires_at
    ON documents (project_id, expires_at)
    WHERE expires_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_documents_tags_gin
    ON documents USING GIN (tags);

CREATE TABLE IF NOT EXISTS document_chunks (
    project_id    TEXT     NOT NULL,
    doc_id        TEXT     NOT NULL,
    chunk_no      INTEGER  NOT NULL,
    content       TEXT     NOT NULL,

    -- tsvector maintained by trigger below (same pattern as session_chunks)
    search_vector tsvector,

    -- Semantic embedding (BAAI/bge-small-en-v1.5, dim 384 — matches
    -- private_memories.embedding).  NULL when embeddings are unavailable.
    embedding     vector(384),

    PRIMARY KEY (project_id, doc_id, chunk_no),
    FOREIGN KEY (project_id, doc_id)
        REFERENCES documents (project_id, doc_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_document_chunks_search_vector_gin
    ON document_chunks USING GIN (search_vector);

-- HNSW parameters match private/002_hnsw_upgrade.sql.
CREATE INDEX IF NOT EXISTS idx_document_chunks_embedding_hnsw
    ON document_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 200);

CREATE OR REPLACE FUNCTION document_chunks_search_vector_update() RETURNS trigger AS $$
BEGIN
    NEW.search_vector := to_tsvector('english', COALESCE(NEW.content, ''));
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_document_chunks_search_vector ON document_chunks;
CREATE TRIGGER trg_document_chunks_search_vector
    BEFORE INSERT OR UPDATE ON document_chunks
    FOR EACH ROW
    EXECUTE FUNCTION document_chunks_search_vector_update();

-- ---------------------------------------------------------------------------
-- Fail-closed tenant isolation (mirrors private_memories: 009 + 012 pattern,
-- ENABLE + FORCE in the same migration like 016/024).
-- ---------------------------------------------------------------------------

ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS documents_tenant_isolation ON documents;

CREATE POLICY documents_tenant_isolation ON documents
    AS PERMISSIVE
    FOR ALL
    TO PUBLIC
    USING (
        current_setting('app.project_id', TRUE) IS NOT NULL
        AND current_setting('app.project_id', TRUE) <> ''
        AND project_id = current_setting('app.project_id', TRUE)
    )
    WITH CHECK (
        current_setting('app.project_id', TRUE) IS NOT NULL
        AND current_setting('app.project_id', TRUE) <> ''
        AND project_id = current_setting('app.project_id', TRUE)
    );

ALTER TABLE document_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_chunks FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS document_chunks_tenant_isolation ON document_chunks;

CREATE POLICY document_chunks_tenant_isolation ON document_chunks
    AS PERMISSIVE
    FOR ALL
    TO PUBLIC
    USING (
        current_setting('app.project_id', TRUE) IS NOT NULL
        AND current_setting('app.project_id', TRUE) <> ''
        AND project_id = current_setting('app.project_id', TRUE)
    )
    WITH CHECK (
        current_setting('app.project_id', TRUE) IS NOT NULL
        AND current_setting('app.project_id', TRUE) <> ''
        AND project_id = current_setting('app.project_id', TRUE)
    );

INSERT INTO private_schema_version (version, description)
VALUES (28, 'documents + document_chunks tables with RLS tenancy (TAP-5002)');
