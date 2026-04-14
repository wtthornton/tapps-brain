-- EPIC-069 STORY-069.1: Per-project profile registry for multi-tenant deployments.
-- See docs/planning/adr/ADR-010-multi-tenant-project-registration.md
--
-- private_memories already carries (project_id, agent_id, key) as its PK
-- (see 001_initial.sql) so data partitioning is already in place.  This
-- migration adds the *profile* side: one authoritative MemoryProfile per
-- project_id, authored out-of-band by an admin (not discovered from the
-- filesystem at runtime).
--
-- Resolution order at runtime collapses to:
--   1. project_profiles[project_id] if present
--   2. built-in 'repo-brain' default
--
-- source column values:
--   'admin'  — registered via CLI / POST /admin/projects (trusted)
--   'auto'   — auto-created on first connection from an unknown project_id
--              when TAPPS_BRAIN_STRICT_PROJECTS is NOT set (lax mode);
--              starts with approved=false until an admin flips it
--   'import' — bulk-imported from a legacy filesystem seed

CREATE TABLE IF NOT EXISTS project_profiles (
    project_id   TEXT        PRIMARY KEY,

    -- Full MemoryProfile serialization (MemoryProfile.model_dump mode='json')
    profile      JSONB       NOT NULL,

    -- Approval gate for auto-registered rows; strict-mode deployments
    -- should require approved=true before serving requests.
    approved     BOOLEAN     NOT NULL DEFAULT FALSE,

    -- How this row came into existence — see header comment.
    source       TEXT        NOT NULL DEFAULT 'auto'
        CHECK (source IN ('admin', 'auto', 'import')),

    -- Human-readable notes (who/why) — optional, admin CLI sets it.
    notes        TEXT        NOT NULL DEFAULT '',

    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Quick lookup of pending-approval rows for admin review.
CREATE INDEX IF NOT EXISTS idx_project_profiles_pending
    ON project_profiles (created_at DESC)
    WHERE approved = FALSE;

-- Enforce slug shape on new rows: lowercase alnum + dash/underscore, 1-64 chars.
-- Rejects accidental path hashes with uppercase / length 16 being used as IDs
-- once a deployment commits to human-readable slugs.  Permissive enough to
-- accept both 16-hex legacy IDs (via derive_project_id) and clean slugs like
-- 'alpaca' or 'tapps-brain-dev'.
ALTER TABLE project_profiles
    ADD CONSTRAINT project_profiles_id_shape
    CHECK (project_id ~ '^[a-z0-9][a-z0-9_-]{0,63}$');

-- Touch updated_at on every write (mirrors the pattern used elsewhere in
-- this schema — see 003_feedback_and_session.sql).
CREATE OR REPLACE FUNCTION project_profiles_touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_project_profiles_touch ON project_profiles;
CREATE TRIGGER trg_project_profiles_touch
    BEFORE UPDATE ON project_profiles
    FOR EACH ROW
    EXECUTE FUNCTION project_profiles_touch_updated_at();

-- Schema version bump.
INSERT INTO private_schema_version (version, description)
VALUES (8, 'project_profiles registry for EPIC-069 multi-tenant profile delivery');
