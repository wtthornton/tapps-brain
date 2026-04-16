-- Migration 007: flywheel_meta key-value table
-- Stores per-tenant metadata used by the flywheel feedback pipeline
-- (STORY-066.14 — backfilling methods the in-memory fake had but the
-- Postgres backend didn't).  Used as a monotonic cursor to skip
-- already-processed feedback events on re-runs of
-- ``flywheel.process_feedback``.

CREATE TABLE IF NOT EXISTS flywheel_meta (
    project_id  text NOT NULL,
    agent_id    text NOT NULL,
    key         text NOT NULL,
    value       text NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, agent_id, key)
);

-- private_schema_version has no UNIQUE/PK on `version` (see 001_initial.sql),
-- so an `ON CONFLICT (version)` clause errors with "no unique or exclusion
-- constraint matching the ON CONFLICT specification".  The migration runner
-- already skips re-applied versions (postgres_migrations._apply_migrations)
-- so a plain INSERT is sufficient and matches the pattern in 008-012.
INSERT INTO private_schema_version (version, description)
VALUES (7, 'flywheel_meta key-value table for feedback pipeline cursor (STORY-066.14)');
