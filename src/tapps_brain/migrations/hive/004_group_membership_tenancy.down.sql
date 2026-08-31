-- Undo TAP-6695: revert hive_group_members to the pre-tenancy shape.
--
-- Any (group_name, agent_id) pair registered under more than one project_id
-- collapses to a single row on downgrade -- which survivor is kept is
-- arbitrary (highest ctid) and irrelevant for a rollback path. This file
-- exists for local rollback testing on a throwaway container, never prod.

DROP INDEX IF EXISTS idx_hive_group_members_agent_project;

ALTER TABLE hive_group_members DROP CONSTRAINT IF EXISTS hive_group_members_pkey;

DELETE FROM hive_group_members
WHERE ctid NOT IN (
    SELECT max(ctid) FROM hive_group_members GROUP BY group_name, agent_id
);

ALTER TABLE hive_group_members ADD PRIMARY KEY (group_name, agent_id);
ALTER TABLE hive_group_members DROP COLUMN IF EXISTS project_id;

DELETE FROM hive_schema_version WHERE version = 4;
