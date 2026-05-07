-- TAP-1491 STORY-074.4: experience_events RANGE-partitioned table.
--
-- Append-only workflow events, range-partitioned monthly on event_time.
-- A default partition captures rows that fall outside pre-created ranges.
--
-- Design notes
-- ============
-- Partition key: event_time TIMESTAMPTZ.  Monthly RANGE slices give the
--   planner fine-grained pruning while keeping the partition count manageable
--   at 200+ concurrent agents (ADR-007).
--
-- Primary key: (id, event_time).  PostgreSQL requires the partition key to be
--   included in every unique/primary-key constraint on a partitioned table.
--
-- created_entity_id / created_edge_id: nullable UUID references to KG objects
--   created by this event.  FK constraints are intentionally deferred to a
--   future ALTER TABLE migration once all EPIC-074 branches (016-019) have
--   been merged to main.  On this branch kg_entities / kg_edges may not yet
--   exist; the columns accept plain UUIDs and are validated at the app layer.
--
-- RLS: identical pattern to 012_rls_force.sql + 016-018 — fail-closed FORCE
--   policy so table owner cannot accidentally bypass tenant isolation.
--   RLS set on the parent automatically covers all partitions in PG 17.
--
-- Idempotency: CREATE TABLE IF NOT EXISTS for parent and every partition;
--   indexes use IF NOT EXISTS; DROP POLICY IF EXISTS before CREATE POLICY.

-- ---------------------------------------------------------------------------
-- Parent (partitioned) table
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS experience_events (
    -- Identity
    id                  UUID        NOT NULL DEFAULT gen_random_uuid(),

    -- Tenant / scope
    tenant_id           TEXT        NOT NULL,
    brain_id            TEXT        NOT NULL,
    project_id          TEXT        NOT NULL,
    agent_id            TEXT        NOT NULL DEFAULT 'unknown',
    session_id          TEXT,
    workflow_run_id     TEXT,

    -- Event metadata
    event_type          TEXT        NOT NULL,
    event_time          TIMESTAMPTZ NOT NULL DEFAULT now(),
    subject_key         TEXT,

    -- Quality signals
    utility_score       REAL        NOT NULL DEFAULT 0.0,

    -- Arbitrary payload
    payload             JSONB       NOT NULL DEFAULT '{}'::jsonb,

    -- Cross-references to objects created by this event.
    -- FK constraints (→ kg_entities, → kg_edges) are added in a subsequent
    -- ALTER TABLE migration once migrations 016-019 are merged to main.
    created_memory_key  TEXT,
    created_entity_id   UUID,
    created_edge_id     UUID,

    -- Timestamps
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Composite PK: partition key (event_time) must be part of every
    -- unique/primary constraint on a partitioned table (PG requirement).
    PRIMARY KEY (id, event_time)
) PARTITION BY RANGE (event_time);

-- ---------------------------------------------------------------------------
-- Default partition (catches inserts outside pre-created ranges)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS experience_events_default
    PARTITION OF experience_events DEFAULT;

-- ---------------------------------------------------------------------------
-- Pre-created monthly partitions — 12 months starting 2026-05
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS experience_events_y2026m05
    PARTITION OF experience_events
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');

CREATE TABLE IF NOT EXISTS experience_events_y2026m06
    PARTITION OF experience_events
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');

CREATE TABLE IF NOT EXISTS experience_events_y2026m07
    PARTITION OF experience_events
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');

CREATE TABLE IF NOT EXISTS experience_events_y2026m08
    PARTITION OF experience_events
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');

CREATE TABLE IF NOT EXISTS experience_events_y2026m09
    PARTITION OF experience_events
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');

CREATE TABLE IF NOT EXISTS experience_events_y2026m10
    PARTITION OF experience_events
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');

CREATE TABLE IF NOT EXISTS experience_events_y2026m11
    PARTITION OF experience_events
    FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');

CREATE TABLE IF NOT EXISTS experience_events_y2026m12
    PARTITION OF experience_events
    FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');

CREATE TABLE IF NOT EXISTS experience_events_y2027m01
    PARTITION OF experience_events
    FOR VALUES FROM ('2027-01-01') TO ('2027-02-01');

CREATE TABLE IF NOT EXISTS experience_events_y2027m02
    PARTITION OF experience_events
    FOR VALUES FROM ('2027-02-01') TO ('2027-03-01');

CREATE TABLE IF NOT EXISTS experience_events_y2027m03
    PARTITION OF experience_events
    FOR VALUES FROM ('2027-03-01') TO ('2027-04-01');

CREATE TABLE IF NOT EXISTS experience_events_y2027m04
    PARTITION OF experience_events
    FOR VALUES FROM ('2027-04-01') TO ('2027-05-01');

-- ---------------------------------------------------------------------------
-- Row Level Security
-- ---------------------------------------------------------------------------

ALTER TABLE experience_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE experience_events FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS experience_events_tenant_isolation ON experience_events;

CREATE POLICY experience_events_tenant_isolation ON experience_events
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
-- (Created on the parent; PG 17 propagates automatically to every partition
--  and to any partition created in future via partition creation.)
-- ---------------------------------------------------------------------------

-- BRIN on event_time: naturally ordered append-only data is ideal for BRIN.
CREATE INDEX IF NOT EXISTS idx_experience_events_event_time_brin
    ON experience_events USING BRIN (event_time);

-- Btree for agent-scoped time-ordered queries.
CREATE INDEX IF NOT EXISTS idx_experience_events_brain_agent_time
    ON experience_events (brain_id, agent_id, event_time DESC);

-- GIN on payload for JSONB attribute queries.
CREATE INDEX IF NOT EXISTS idx_experience_events_payload_gin
    ON experience_events USING GIN (payload);

-- Project-level recency lookup.
CREATE INDEX IF NOT EXISTS idx_experience_events_project_created
    ON experience_events (project_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- Schema version bump
-- ---------------------------------------------------------------------------

INSERT INTO private_schema_version (version, description)
VALUES (20, 'experience_events partitioned table with RLS (TAP-1491 STORY-074.4)');
