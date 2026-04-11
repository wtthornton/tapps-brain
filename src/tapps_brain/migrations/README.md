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
    001_initial.sql       — Private agent memory: private_memories
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
3. private/001_initial.sql
4. roles/001_db_roles.sql   ← requires tables above to exist
```

## Idempotency

- Schema migrations use `CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`, and
  `CREATE OR REPLACE FUNCTION` — safe to re-apply on a live database.
- The roles migration uses `DO $$ IF NOT EXISTS … $$` blocks for role creation, and relies
  on PostgreSQL's idempotent `GRANT` semantics (re-granting an existing privilege is a
  no-op, never an error).

## Running migrations

### Development / Docker Compose

```bash
# Bring up Postgres with pgvector
docker compose up -d postgres

# Apply schema migrations (uses tapps_migrator or a superuser)
psql "$TAPPS_BRAIN_HIVE_DSN" -f src/tapps_brain/migrations/hive/001_initial.sql
psql "$TAPPS_BRAIN_HIVE_DSN" -f src/tapps_brain/migrations/federation/001_initial.sql
psql "$TAPPS_BRAIN_HIVE_DSN" -f src/tapps_brain/migrations/private/001_initial.sql

# Apply roles (run as superuser)
psql "$TAPPS_BRAIN_SUPERUSER_DSN" -f src/tapps_brain/migrations/roles/001_db_roles.sql
```

Or use the Makefile shortcut:

```bash
make brain-migrate   # applies all migrations in the correct order
```

### Production / CI

Run schema migrations as `tapps_migrator` (or superuser in CI).
Run `roles/001_db_roles.sql` as **superuser** — role creation requires `CREATEROLE` or
superuser privilege.

Set the application DSN to `tapps_runtime` credentials before starting the service.
See `docs/operations/db-roles-runbook.md` for the full production checklist.

## Roles

| Role | Privileges | Used by |
|------|-----------|---------|
| `tapps_migrator` | DDL (CREATE, ALTER, DROP, TRUNCATE, REFERENCES) in `public` schema | Migration jobs, CI only |
| `tapps_runtime` | DML (SELECT, INSERT, UPDATE, DELETE) on all tapps-brain tables | Running application |
| `tapps_readonly` | SELECT on all tapps-brain tables | Reporting, debugging, read replicas |

**Never** use `tapps_migrator` credentials in the running application.  
**Never** use a superuser DSN as `TAPPS_BRAIN_HIVE_DSN` in production.

## Adding new migrations

1. Add `NNN_description.sql` to the relevant subdirectory (sequential numeric prefix).
2. Use `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` for idempotency.
3. Append a `INSERT INTO <group>_schema_version (version, description) VALUES (N, '...')` row.
4. If your migration adds new tables, add the corresponding `GRANT` statements to a new
   `roles/002_*.sql` migration (or update `roles/001_db_roles.sql` and document the change).
