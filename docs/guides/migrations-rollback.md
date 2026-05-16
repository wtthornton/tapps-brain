# Migration Rollback Guide

> **Forward-only by design.** tapps-brain applies schema migrations in one direction only. The down-migration files captured here are for **emergency operator use** and require explicit human judgment. They are not applied automatically.

## Overview

Every SQL migration under `src/tapps_brain/migrations/{private,hive,federation}/` has a paired `*.down.sql` sibling that reverts its schema changes. A CLI command (`tapps-brain maintenance migrations-rollback`) reads those files and applies them in reverse order down to a target version.

---

## When to Roll Back

Use rollback only when:

- A migration introduced a breaking defect not caught in CI.
- The forward migration cannot be corrected by a subsequent "fix-forward" migration.
- The deployment window is too short for a hotfix — an emergency revert is faster.

In all other cases, prefer **fix-forward** (write a new migration that corrects the schema state). Rollback deletes columns and tables; fix-forward preserves data.

---

## CLI Usage

```bash
# 1. Preview what would be rolled back (no changes made).
tapps-brain maintenance migrations-rollback \
    --schema private \
    --dry-run \
    14

# 2. Execute the rollback (confirmation prompt on TTY).
tapps-brain maintenance migrations-rollback \
    --schema private \
    14

# 3. Skip the confirmation prompt in scripts.
tapps-brain maintenance migrations-rollback \
    --schema private \
    --yes \
    14

# Roll back the Hive schema to version 1.
tapps-brain maintenance migrations-rollback \
    --schema hive \
    --yes \
    1

# Override the DSN inline (takes precedence over the environment variable).
tapps-brain maintenance migrations-rollback \
    --schema federation \
    --dsn "postgresql://user:pass@host/db" \
    --yes \
    0
```

### Options

| Option | Default | Description |
|--------|---------|-------------|
| `TARGET_VERSION` (positional) | required | Roll back all versions **above** this number. Pass `0` to roll back everything. |
| `--schema` | `private` | Schema plane: `private`, `hive`, or `federation`. |
| `--dsn` / `TAPPS_BRAIN_DATABASE_URL` | — | PostgreSQL connection string. Hive and Federation planes also accept `TAPPS_BRAIN_HIVE_DSN` / `TAPPS_BRAIN_FEDERATION_DSN`. |
| `--dry-run` | `false` | Preview only — print which down-migrations would run. |
| `--yes` | `false` | Skip the interactive confirmation prompt. |
| `--json` | `false` | Emit results as JSON (for scripting). |

---

## Manual Procedure (without the CLI)

If the CLI is unavailable, run the down files directly with `psql`:

```bash
# Roll back private migration 21 (kg_edges confidence_history).
psql "$TAPPS_BRAIN_DATABASE_URL" \
    -f src/tapps_brain/migrations/private/021_kg_edges_confidence_history.down.sql

# Roll back private migration 20 next.
psql "$TAPPS_BRAIN_DATABASE_URL" \
    -f src/tapps_brain/migrations/private/020_experience_events.down.sql
```

Run down files in **descending version order** (newest first). Each file is idempotent where possible (`DROP … IF EXISTS`, `ALTER TABLE … DROP COLUMN IF EXISTS`).

Each down file also includes a `DELETE FROM <version_table> WHERE version = N` statement so the version-tracking table stays accurate after a manual run.

---

## Writing Down Files for New Migrations

Whenever you add a new forward migration `NNN_my_feature.sql`, you **must** also add `NNN_my_feature.down.sql` in the same directory. The CI test `tests/unit/test_migration_down_files.py` will fail the build if the paired file is missing.

### Guidelines

1. **Undo only what the forward migration added.** Do not touch objects owned by earlier migrations.
2. **Use `IF EXISTS` everywhere** — down files must be idempotent.
3. **Cascade carefully.** `DROP TABLE … CASCADE` silently removes dependent objects. Prefer targeted `DROP INDEX IF EXISTS`, `DROP POLICY IF EXISTS`, `DROP TRIGGER IF EXISTS` before the table drop.
4. **Include the version cleanup.** End the file with:
   ```sql
   DELETE FROM <schema>_schema_version WHERE version = N;
   ```
   This keeps the version table correct when the file is run manually without the CLI.
5. **Test the pair.** Run `pytest tests/unit/test_migration_down_files.py -v` locally before pushing.

### Template

```sql
-- Undo migration NNN: one-line description of what is being reversed.

-- Drop objects in reverse order of creation.
DROP INDEX IF EXISTS idx_my_table_foo;
ALTER TABLE my_table DROP COLUMN IF EXISTS foo;

DELETE FROM private_schema_version WHERE version = NNN;
```

---

## Auditing Expectations

- **Code review:** every PR that adds a forward migration must include the paired down file. The `scripts/publish-checklist.md` pre-release gate includes this check.
- **CI gate:** `tests/unit/test_migration_down_files.py` runs on every push and blocks the build if a forward migration lacks a paired down file.
- **Production runbook:** before rolling back in production, snapshot the affected tables (`pg_dump --table=...`) so data can be recovered if the rollback itself has issues.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `Nothing to roll back` | Target version ≥ highest applied version | Check `--schema` and `TARGET_VERSION`. |
| `psycopg ImportError` | psycopg not installed | `pip install 'psycopg[binary]'` |
| CI fails with "missing down file" | New migration added without a down file | Add `NNN_*.down.sql` alongside the forward migration. |
| Down file errors on a column that no longer exists | Migration was already partially rolled back | Re-run is safe — `DROP COLUMN IF EXISTS` is a no-op. |
