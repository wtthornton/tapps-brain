-- EPIC-059 stage 2: relocate the JSONL audit log (EPIC-007) into Postgres.
-- Old: PostgresPrivateBackend.append_audit was a no-op stub; AuditReader read
-- a memory_log.jsonl file written by the deleted SQLite MemoryPersistence.
-- New: append_audit INSERTs into the audit_log table; AuditReader queries it.

CREATE TABLE IF NOT EXISTS audit_log (
    project_id    TEXT        NOT NULL,
    agent_id      TEXT        NOT NULL,
    id            BIGSERIAL   NOT NULL,
    event_type    TEXT        NOT NULL,
    key           TEXT        NOT NULL DEFAULT '',
    details       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    timestamp     TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (project_id, agent_id, id)
);

-- Hot path: per-scope chronological scan, optionally filtered by event_type.
CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp
    ON audit_log (project_id, agent_id, timestamp DESC);

-- Per-key lookup ("show me everything that happened to memory entry X").
CREATE INDEX IF NOT EXISTS idx_audit_log_key
    ON audit_log (project_id, agent_id, key)
    WHERE key <> '';

-- Per-event-type filtering ("show me all save events").
CREATE INDEX IF NOT EXISTS idx_audit_log_event_type
    ON audit_log (project_id, agent_id, event_type);

INSERT INTO private_schema_version (version, description)
VALUES (5, 'Add audit_log table (Postgres-only AuditReader replaces JSONL audit file)');
