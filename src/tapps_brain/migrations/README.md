# tapps-brain — Database Migrations

This folder contains **forward-only** SQL migrations for all tapps-brain Postgres backends.
Each subdirectory corresponds to one backend/schema group.

## Directory structure

```
migrations/
  hive/
    001_initial.sql       — Hive schema: hive_memories, groups, feedback, agent_registry
  federation/
    001_initial.sql       — Federation schema: federated_memories, subscriptions, meta
  private/
    001_initial.sql … 024_profile_scoped_data.sql — Private agent memory + KG + experience events
  roles/
    001_db_roles.sql      — Least-privilege DB roles (tapps_migrator / tapps_runtime / tapps_readonly)
```

## Apply order

Migrations within each group are sequential (applied in file-name order).
The roles migration **must be applied last** — after all schema migrations — because it
grants privileges on tables that must already exist:

```
1. hive/001_initial.sql
2. federation/001_initial.sql
3. private/001 … 024 (sequential)
4. roles/001_db_roles.sql   ← requires tables above to exist
```

## Private migration summary (001–024)

| Version | File | Summary |
|---------|------|---------|
| 001 | `001_initial.sql` | `private_memories`, FTS, pgvector, `private_schema_version` |
| 002 | `002_hnsw_upgrade.sql` | HNSW index upgrade |
| 003 | `003_feedback_and_session.sql` | `feedback_events`, `session_chunks` |
| 004 | `004_diagnostics_history.sql` | `diagnostics_history` |
| 005 | `005_audit_log.sql` | `audit_log` |
| 006 | `006_gc_archive.sql` | `gc_archive` |
| 007 | `007_flywheel_meta.sql` | `flywheel_meta` |
| 008 | `008_project_profiles.sql` | `project_profiles` (EPIC-069) |
| 009 | `009_project_rls.sql` | Row Level Security |
| 010 | `010_idempotency_keys.sql` | HTTP idempotency keys |
| 011 | `011_per_tenant_auth.sql` | Per-project bearer tokens |
| 012 | `012_rls_force.sql` | FORCE RLS |
| 013 | `013_temporal_sensitivity.sql` | Per-entry decay velocity |
| 014 | `014_failed_approaches.sql` | Dead-end investigation notes |
| 015 | `015_integrity_hash_version.sql` | Integrity hash version column |
| 015 | `015_memory_status.sql` | Lifecycle `status` column |
| 015 | `015_memory_class.sql` | `memory_class` column |
| 016–019 | `016_kg_entities` … `019_kg_aliases` | Knowledge graph tables |
| 020–023 | `020_experience_events` … `023_experience_events_query_index` | Experience events + indexes |
| 024 | `024_profile_scoped_data.sql` | Profile-scoped learned KV |

Full column-level detail: [`docs/engineering/data-stores-and-schema.md`](../../../docs/engineering/data-stores-and-schema.md).

## Idempotency

- Schema migrations use `CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`, and
  `CREATE OR REPLACE FUNCTION` — safe to re-apply on a live database.
- The roles migration uses `DO $$ IF NOT EXISTS … $$` blocks for role creation.

## Running migrations

### Development / Docker Compose

```bash
make brain-up          # dev Postgres + pgvector
make brain-migrate     # applies all migrations in correct order
```

### Production / CI

Run schema migrations as `tapps_migrator` (or superuser in CI).
Run `roles/001_db_roles.sql` as **superuser**.
Set the application DSN to `tapps_runtime` credentials before starting the service.
See `docs/operations/db-roles-runbook.md` for the production checklist.

CI applies migrations via `scripts/apply_all_migrations.py` before pytest.

## Roles

| Role | Privileges | Used by |
|------|-----------|---------|
| `tapps_migrator` | DDL in `public` schema | Migration jobs, CI only |
| `tapps_runtime` | DML on all tapps-brain tables | Running application |
| `tapps_readonly` | SELECT on all tapps-brain tables | Reporting, debugging |

**Never** use `tapps_migrator` credentials in the running application.
**Never** use a superuser DSN as `TAPPS_BRAIN_DATABASE_URL` in production.

## Adding new migrations

1. Add `NNN_description.sql` to the relevant subdirectory (sequential numeric prefix).
2. Use `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` for idempotency.
3. Append a `INSERT INTO <group>_schema_version (version, description) VALUES (N, '...')` row.
4. If your migration adds new tables, add the corresponding `GRANT` statements to a new
   `roles/002_*.sql` migration (or update `roles/001_db_roles.sql` and document the change).
