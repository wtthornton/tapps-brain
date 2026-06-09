-- TAP-3158 STORY-074.2: btree index for experience_events query path.
--
-- Supports brain_query_events filters on (project_id, event_type) with
-- event_time DESC ordering.  RLS still applies via tenant_id; project_id
-- is included so the planner can prune when project-scoped filters are added.

CREATE INDEX IF NOT EXISTS idx_experience_events_project_type_time
    ON experience_events (project_id, event_type, event_time DESC);

-- ---------------------------------------------------------------------------
-- Schema version bump
-- ---------------------------------------------------------------------------

INSERT INTO private_schema_version (version, description)
VALUES (
    23,
    'experience_events query index (project_id, event_type, event_time) — TAP-3158'
);
