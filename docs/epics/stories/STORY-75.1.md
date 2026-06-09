# migrations: profile_scoped_data table RLS

## What

Add Postgres storage for per-project per-profile learned KV separate from private_memories.

## Where

- `src/tapps_brain/migrations/private/024_profile_scoped_data.sql:1-60`
- `src/tapps_brain/migrations/private/024_profile_scoped_data.down.sql:1-10`

## Acceptance

- [x] Table `profile_scoped_data` with columns `project_id`, `profile_name`, `data_key`, `value_json`, `updated_at`
- [x] UNIQUE `(project_id, profile_name, data_key)` and RLS FORCE on `project_id`
- [x] Down migration drops table cleanly; migration version 24 registered in private schema
- [x] No federation or Hive tables in v1

## Refs

docs/planning/epics/EPIC-075.md
