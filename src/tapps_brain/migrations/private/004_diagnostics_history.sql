-- EPIC-059 stage 2: relocate the EPIC-030 diagnostics history table from
-- SQLite (DiagnosticsHistoryStore in diagnostics.py) into the Postgres
-- private schema.  Scoped to (project_id, agent_id) like every other
-- private-memory table.

CREATE TABLE IF NOT EXISTS diagnostics_history (
    project_id        TEXT        NOT NULL,
    agent_id          TEXT        NOT NULL,
    id                TEXT        NOT NULL,        -- UUID from DiagnosticsHistoryStore.record()
    recorded_at       TIMESTAMPTZ NOT NULL,
    composite_score   REAL        NOT NULL,
    dimension_scores  JSONB       NOT NULL DEFAULT '{}'::jsonb,
    circuit_state     TEXT        NOT NULL DEFAULT 'closed',
    full_report       JSONB       NOT NULL DEFAULT '{}'::jsonb,

    PRIMARY KEY (project_id, agent_id, id)
);

-- Hot path: most-recent reports per scope, used by EWMA warm-start.
CREATE INDEX IF NOT EXISTS idx_diagnostics_history_recorded_at
    ON diagnostics_history (project_id, agent_id, recorded_at DESC);

-- Bookkeeping for the migration ledger.
INSERT INTO private_schema_version (version, description)
VALUES (4, 'Add diagnostics_history table (Postgres-only DiagnosticsHistoryStore)');
