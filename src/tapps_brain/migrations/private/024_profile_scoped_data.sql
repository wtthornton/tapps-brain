-- TAP-3162 STORY-075.1: Per-project per-profile learned KV storage.
--
-- Separate from private_memories tiers and KG entities.  Used by tapps-mcp
-- DomainWeightStore (TAP-1998) for adaptive domain weights keyed by
-- (project_id, profile_name, data_key).

CREATE TABLE IF NOT EXISTS profile_scoped_data (
    project_id    TEXT        NOT NULL,
    profile_name  TEXT        NOT NULL,
    data_key      TEXT        NOT NULL,
    value_json    JSONB       NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, profile_name, data_key)
);

CREATE INDEX IF NOT EXISTS idx_profile_scoped_data_project_profile
    ON profile_scoped_data (project_id, profile_name);

-- Fail-closed tenant isolation (mirrors private_memories).
ALTER TABLE profile_scoped_data ENABLE ROW LEVEL SECURITY;
ALTER TABLE profile_scoped_data FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS profile_scoped_data_tenant_isolation ON profile_scoped_data;

CREATE POLICY profile_scoped_data_tenant_isolation ON profile_scoped_data
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

CREATE OR REPLACE FUNCTION profile_scoped_data_touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_profile_scoped_data_touch ON profile_scoped_data;
CREATE TRIGGER trg_profile_scoped_data_touch
    BEFORE UPDATE ON profile_scoped_data
    FOR EACH ROW
    EXECUTE FUNCTION profile_scoped_data_touch_updated_at();

INSERT INTO private_schema_version (version, description)
VALUES (
    24,
    'profile_scoped_data learned KV table with RLS — TAP-3162'
);
