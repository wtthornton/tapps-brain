-- TAP-1490 STORY-074.3: kg_evidence table — trust anchors for ADR-009.
--
-- Evidence rows attach to either a kg_edges row or a kg_entities row (XOR
-- enforced via CHECK constraint).  They capture provenance, source quality,
-- and lifecycle so every edge or entity assertion can be traced back to the
-- artifact that produced it.
--
-- Design notes
-- ============
-- XOR attachment: exactly one of (edge_id, entity_id) must be non-null.
--   A single CHECK constraint enforces this.  Two separate FKs with ON DELETE
--   CASCADE mean removing an edge or entity cleans up its evidence automatically.
--
-- source_hash: caller-supplied SHA-256 (or similar) of the source artifact.
--   Stored as TEXT (hex) — no pgcrypto dependency.
--
-- source_span: free-form position string (e.g. "lines 42-55", "§3.2") — not
--   parsed by the DB.
--
-- confidence / utility_score: agent-assigned floats.  Both are REAL (4-byte)
--   because sub-1% precision is not meaningful here.
--
-- status CHECK list is intentionally narrower than kg_entities / kg_edges —
--   evidence can only be active, rejected (wrong provenance), superseded
--   (replaced by newer evidence), or archived.
--
-- RLS pattern: identical to 012_rls_force.sql + 016/017 — fail-closed,
--   FORCE so table owner cannot accidentally bypass isolation.
--
-- Idempotency: CREATE TABLE/INDEX IF NOT EXISTS + DROP/CREATE POLICY.

-- ---------------------------------------------------------------------------
-- Core table
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS kg_evidence (
    -- Identity
    id                  UUID        NOT NULL DEFAULT gen_random_uuid(),

    -- Tenant / scope columns
    tenant_id           TEXT        NOT NULL,   -- RLS key (= project_id at runtime)
    brain_id            TEXT        NOT NULL,   -- logical brain identity
    project_id          TEXT        NOT NULL,   -- project-level scope

    -- Attachment: exactly one of edge_id / entity_id must be non-null (XOR).
    edge_id             UUID        REFERENCES kg_edges(id) ON DELETE CASCADE,
    entity_id           UUID        REFERENCES kg_entities(id) ON DELETE CASCADE,

    CONSTRAINT chk_evidence_xor_attachment CHECK (
        (edge_id IS NOT NULL AND entity_id IS NULL)
        OR  (edge_id IS NULL  AND entity_id IS NOT NULL)
    ),

    -- Source provenance
    source_type         TEXT        NOT NULL DEFAULT 'agent',   -- e.g. 'file', 'url', 'agent'
    source_id           TEXT,                                   -- opaque source identifier
    source_key          TEXT,                                   -- human-readable source key
    source_uri          TEXT,                                   -- full URI / file path
    source_hash         TEXT,                                   -- SHA-256 hex or similar
    source_span         TEXT,                                   -- position within the source
    quote               TEXT,                                   -- verbatim excerpt from source

    -- Supplementary data
    metadata            JSONB       NOT NULL DEFAULT '{}'::jsonb,

    -- Quality signals
    source_agent        TEXT        NOT NULL DEFAULT 'unknown',
    confidence          REAL        NOT NULL DEFAULT 0.6,
    utility_score       REAL        NOT NULL DEFAULT 0.0,

    -- Lifecycle
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_at            TIMESTAMPTZ,
    invalid_at          TIMESTAMPTZ,
    status              VARCHAR(20) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'rejected', 'superseded', 'archived')),

    -- Primary key
    PRIMARY KEY (id)
);

-- ---------------------------------------------------------------------------
-- Row Level Security
-- ---------------------------------------------------------------------------

ALTER TABLE kg_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE kg_evidence FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS kg_evidence_tenant_isolation ON kg_evidence;

CREATE POLICY kg_evidence_tenant_isolation ON kg_evidence
    AS PERMISSIVE
    FOR ALL
    TO PUBLIC
    USING (
        current_setting('app.project_id', TRUE) IS NOT NULL
        AND current_setting('app.project_id', TRUE) <> ''
        AND tenant_id = current_setting('app.project_id', TRUE)
    )
    WITH CHECK (
        current_setting('app.project_id', TRUE) IS NOT NULL
        AND current_setting('app.project_id', TRUE) <> ''
        AND tenant_id = current_setting('app.project_id', TRUE)
    );

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------

-- Lookup all evidence for a given edge, filtered by status.
CREATE INDEX IF NOT EXISTS idx_kg_evidence_edge_status
    ON kg_evidence (edge_id, status);

-- Lookup all evidence for a given entity, filtered by status.
CREATE INDEX IF NOT EXISTS idx_kg_evidence_entity_status
    ON kg_evidence (entity_id, status);

-- Source-tracing: find evidence by (brain, source_type, source_id).
CREATE INDEX IF NOT EXISTS idx_kg_evidence_brain_source
    ON kg_evidence (brain_id, source_type, source_id);

-- Project-level recency ordering.
CREATE INDEX IF NOT EXISTS idx_kg_evidence_project_created
    ON kg_evidence (project_id, created_at DESC);

-- Arbitrary JSONB attribute queries.
CREATE INDEX IF NOT EXISTS idx_kg_evidence_metadata_gin
    ON kg_evidence USING GIN (metadata);

-- ---------------------------------------------------------------------------
-- Schema version bump
-- ---------------------------------------------------------------------------

INSERT INTO private_schema_version (version, description)
VALUES (18, 'kg_evidence table with XOR attachment + RLS (TAP-1490 STORY-074.3)');
