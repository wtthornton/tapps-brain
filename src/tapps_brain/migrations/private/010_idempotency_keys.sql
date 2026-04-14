-- EPIC-070 STORY-070.5: Idempotency keys table for HTTP write operations.
--
-- Enables safe retries on POST /v1/remember and POST /v1/reinforce without
-- double-inserting entries.  Each key is scoped to a (project_id, key) pair
-- so keys do not collide across tenants.
--
-- Rows are expired (swept) after 24 hours by the GC maintenance sweep.
-- The sweep deletes rows older than TAPPS_BRAIN_IDEMPOTENCY_TTL_HOURS
-- (default 24) when TAPPS_BRAIN_IDEMPOTENCY=1.
--
-- Feature flag: TAPPS_BRAIN_IDEMPOTENCY=1 must be set at runtime.  When
-- the flag is off, the table exists but is never written to or read from.
--
-- Idempotency
-- -----------
-- CREATE TABLE IF NOT EXISTS and CREATE INDEX IF NOT EXISTS make this
-- migration re-runnable without errors.  Safe to apply multiple times.

-- ---------------------------------------------------------------------------
-- idempotency_keys — per-tenant write deduplication store
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS idempotency_keys (
    key             TEXT        NOT NULL,
    project_id      TEXT        NOT NULL,
    response_json   TEXT        NOT NULL,
    response_status INTEGER     NOT NULL DEFAULT 200,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (key, project_id)
);

-- Index supports the TTL sweep (DELETE WHERE created_at < threshold).
CREATE INDEX IF NOT EXISTS idempotency_keys_created_at_idx
    ON idempotency_keys (created_at);

-- Schema version bump.
INSERT INTO private_schema_version (version, description)
VALUES (10, 'Idempotency keys table (EPIC-070 STORY-070.5)');
