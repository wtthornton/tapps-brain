-- TAP-5508: project-scoped KG predicate registry with cardinality metadata.
--
-- `kg_edges.predicate` (migration 017) is bare TEXT with no registry and no
-- validation beyond a client-side `.strip()`. That is fine for exploratory
-- graphs and useless for ledger questions: a consumer cannot ask "is this
-- invariant violated" when nothing declares that `refunded` is functional.
--
-- This table lets a project DECLARE what a predicate means:
--
--   max_count    How many active objects one subject may have for this
--                predicate. NULL = unbounded (the permissive default).
--                1 = functional edge, e.g. `refunded`.
--   domain_type  Optional kg_entities.entity_type the SUBJECT must be.
--   range_type   Optional kg_entities.entity_type the OBJECT must be.
--
-- Registration is descriptive here. TAP-5510 enforces `max_count` on upsert;
-- keeping the schema change separate means a project can register and inspect
-- its predicates, and see what WOULD be rejected, before enforcement lands.
--
-- Open by default
-- ---------------
-- An unregistered predicate stays legal. Existing graphs use free-form
-- predicates, and a registry that rejected them on introduction would break
-- every current writer to buy a guarantee nobody asked for yet. Projects opt
-- into rejection via `kg.strict_predicates` in the profile.
--
-- Cardinality is per (brain_id, predicate), matching the grain of the partial
-- unique index on kg_edges — declaring it at any other grain would describe a
-- constraint the edge table cannot express.
--
-- Isolation
-- ---------
-- RLS ENABLE + FORCE with the same fail-closed tenant policy as kg_edges
-- (migration 017) and kg_entities (016): no admin bypass, and FORCE so the
-- table owner cannot accidentally read across tenants either.
--
-- Idempotency: CREATE TABLE/INDEX IF NOT EXISTS + DROP/CREATE POLICY.

CREATE TABLE IF NOT EXISTS kg_predicates (
    -- Identity
    id                  UUID        NOT NULL DEFAULT gen_random_uuid(),

    -- Tenant / scope columns (mirrors kg_edges)
    tenant_id           TEXT        NOT NULL,   -- RLS key (= project_id at runtime)
    brain_id            TEXT        NOT NULL,   -- logical brain identity
    project_id          TEXT        NOT NULL,   -- project-level scope

    -- The declaration
    predicate           TEXT        NOT NULL,
    max_count           INTEGER,                -- NULL = unbounded
    domain_type         TEXT,                   -- kg_entities.entity_type of subject
    range_type          TEXT,                   -- kg_entities.entity_type of object
    description         TEXT,

    -- Provenance
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    registered_by       TEXT        NOT NULL DEFAULT 'unknown',

    PRIMARY KEY (id),

    -- A max_count of 0 would declare a predicate that may never hold, which is
    -- a deletion expressed as a constraint. Reject it rather than let a caller
    -- lock themselves out of their own predicate with a typo.
    CONSTRAINT kg_predicates_max_count_positive
        CHECK (max_count IS NULL OR max_count >= 1)
);

-- One declaration per predicate per brain. Re-registering updates in place
-- rather than accumulating conflicting rows that readers would have to rank.
CREATE UNIQUE INDEX IF NOT EXISTS uix_kg_predicates_brain_predicate
    ON kg_predicates (tenant_id, brain_id, predicate);

-- ---------------------------------------------------------------------------
-- Row Level Security — pattern mirrors 017_kg_edges.sql
-- ---------------------------------------------------------------------------

ALTER TABLE kg_predicates ENABLE ROW LEVEL SECURITY;
ALTER TABLE kg_predicates FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS kg_predicates_tenant_isolation ON kg_predicates;

CREATE POLICY kg_predicates_tenant_isolation ON kg_predicates
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

-- Registry lookup on the write path (TAP-5510 reads this per upsert).
CREATE INDEX IF NOT EXISTS idx_kg_predicates_lookup
    ON kg_predicates (tenant_id, brain_id, predicate);

INSERT INTO private_schema_version (version, description)
VALUES (32, 'kg_predicates registry with cardinality metadata + RLS (TAP-5508)');
