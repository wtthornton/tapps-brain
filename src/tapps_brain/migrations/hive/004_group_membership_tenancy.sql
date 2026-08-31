-- TAP-6695: hive_group_members has no project_id, so get_agent_groups() is
-- keyed on agent_id alone. An agent registered as a group member under one
-- project therefore gains group-scoped recall (the scope:group:<name>
-- admission in _recall_group_tags / _scope_predicate) in EVERY project it
-- holds private_memories rows under -- Ruling 16 authorises widening recall
-- to "a group the requesting agent belongs to", and a global membership key
-- silently reinterprets that as "belongs to anywhere, therefore everywhere".
--
-- Backfill: existing rows get project_id = '' (empty string) -- not NULL and
-- not a real project. This is FAIL-CLOSED, not "all projects":
-- project_resolver.validate_project_id requires project_id to match
-- ^[a-z0-9][a-z0-9_-]{0,63}$ -- a non-empty, alnum-leading slug -- so ''
-- can never be a real project_id, and get_agent_groups(agent_id, project_id)
-- additionally guards against an empty/falsy project_id before it ever
-- reaches SQL (returns [] immediately), so a '' row can never be matched by
-- a live lookup. The 169 existing rows grant membership in NO project until
-- deliberately re-registered via add_group_member(group_name, agent_id,
-- project_id) for a specific project. Using '' rather than NULL keeps
-- project_id usable as a NOT NULL primary-key column (below) without a
-- NULL-vs-NULL equality special case.
--
-- Composite PK: the same agent_id can be a legitimate member of the same
-- group under several DIFFERENT projects (e.g. a shared fleet identity
-- onboarded per-repo) -- (group_name, agent_id) alone would let a later
-- project's registration silently overwrite an earlier project's row.

ALTER TABLE hive_group_members ADD COLUMN IF NOT EXISTS project_id TEXT NOT NULL DEFAULT '';

ALTER TABLE hive_group_members DROP CONSTRAINT IF EXISTS hive_group_members_pkey;
ALTER TABLE hive_group_members ADD PRIMARY KEY (group_name, agent_id, project_id);

-- get_agent_groups() now runs "WHERE agent_id = %s AND project_id = %s" --
-- give it a dedicated index rather than relying on the PK, whose leading
-- column (group_name) doesn't match that predicate's column order.
CREATE INDEX IF NOT EXISTS idx_hive_group_members_agent_project
    ON hive_group_members (agent_id, project_id);

INSERT INTO hive_schema_version (version, description)
VALUES (4, 'Group membership tenancy: project_id on hive_group_members, fail-closed backfill (TAP-6695)');
