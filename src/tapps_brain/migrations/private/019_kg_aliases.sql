-- TAP-1490 STORY-074.3: kg_aliases table — reversible entity name aliases.
--
-- Aliases give a kg_entities row additional names by which it can be looked up
-- (synonyms, abbreviations, former names, alternate spellings).  Each alias
-- row carries its own confidence + status so a wrong merge can be marked
-- 'rejected' without deleting the entity or other valid aliases.
--
-- Design notes
-- ============
-- alias_norm: STORED generated column = lower(alias).  Same PG17-safe pattern
--   as kg_entities.canonical_name_norm.  Used for case-insensitive uniqueness
--   and fast lookups without a functional-index workaround.
--
-- evidence_id: optional FK to kg_evidence.  An alias may be created without
--   evidence (confidence is then the sole quality signal), or linked to the
--   evidence row that produced it.  ON DELETE SET NULL so deleting evidence
--   does not cascade-delete the alias.
--
-- Uniqueness semantics:
--   - Table-level UNIQUE (brain_id, alias_norm, entity_id) prevents the same
--     normalised alias from appearing twice for the same entity in the same
--     brain, regardless of status.  This is intentional: if an alias is
--     rejected, it should be updated (status → rejected) rather than deleted
--     and re-inserted.
--   - A separate partial unique index WHERE status='active' makes the uniqueness
--     constraint visible for active-alias queries and provides a named
--     artefact for tests to verify.
--
-- RLS pattern: identical to 012_rls_force.sql + 016/017/018 — fail-closed,
--   FORCE so the table owner cannot accidentally bypass isolation.
--
-- Idempotency: CREATE TABLE/INDEX IF NOT EXISTS + DROP/CREATE POLICY.

-- ---------------------------------------------------------------------------
-- Core table
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS kg_aliases (
    -- Identity
    id              UUID        NOT NULL DEFAULT gen_random_uuid(),

    -- Tenant / scope columns
    tenant_id       TEXT        NOT NULL,   -- RLS key (= project_id at runtime)
    brain_id        TEXT        NOT NULL,   -- logical brain identity
    project_id      TEXT        NOT NULL,   -- project-level scope

    -- Relationship
    entity_id       UUID        NOT NULL REFERENCES kg_entities(id) ON DELETE CASCADE,
    evidence_id     UUID        REFERENCES kg_evidence(id) ON DELETE SET NULL,

    -- Alias value
    alias           TEXT        NOT NULL,
    alias_norm      TEXT        NOT NULL
        GENERATED ALWAYS AS (lower(alias)) STORED,

    -- Quality signals
    confidence      REAL        NOT NULL DEFAULT 0.6,
    source_agent    TEXT        NOT NULL DEFAULT 'unknown',

    -- Lifecycle
    status          VARCHAR(20) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'rejected', 'superseded', 'archived')),

    -- Timestamps
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Primary key
    PRIMARY KEY (id),

    -- One normalised alias string per entity per brain (across all statuses).
    -- Use UPDATE status='rejected' to mark a bad alias; do not delete+re-insert.
    UNIQUE (brain_id, alias_norm, entity_id)
);

-- ---------------------------------------------------------------------------
-- Row Level Security
-- ---------------------------------------------------------------------------

ALTER TABLE kg_aliases ENABLE ROW LEVEL SECURITY;
ALTER TABLE kg_aliases FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS kg_aliases_tenant_isolation ON kg_aliases;

CREATE POLICY kg_aliases_tenant_isolation ON kg_aliases
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

-- Partial unique index: active alias lookup.  Redundant with the table UNIQUE
-- constraint for correctness, but provides a named index for tests and
-- makes the active-alias hot path visible in EXPLAIN.
CREATE UNIQUE INDEX IF NOT EXISTS uix_kg_aliases_active_brain_alias_entity
    ON kg_aliases (brain_id, alias_norm, entity_id)
    WHERE status = 'active';

-- Fast entity alias listing.
CREATE INDEX IF NOT EXISTS idx_kg_aliases_entity_status
    ON kg_aliases (entity_id, status);

-- Reverse lookup: find entity by alias within a brain.
CREATE INDEX IF NOT EXISTS idx_kg_aliases_brain_alias_norm
    ON kg_aliases (brain_id, alias_norm);

-- ---------------------------------------------------------------------------
-- Schema version bump
-- ---------------------------------------------------------------------------

INSERT INTO private_schema_version (version, description)
VALUES (19, 'kg_aliases table with confidence + status lifecycle (TAP-1490 STORY-074.3)');
