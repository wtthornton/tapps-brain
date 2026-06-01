#!/usr/bin/env bash
# docker/migrate-entrypoint.sh — One-shot bootstrap for the tapps-brain DB.
#
# TAP-2686: the schema-migration DDL now runs as the de-privileged
# tapps_migrator role (NOSUPERUSER NOBYPASSRLS), so the privileged-role guard
# passes WITHOUT TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE=1 and no "tenant isolation
# is NOT enforced" audit line is emitted on deploy. A short one-time privileged
# bootstrap (the superuser) only does the things that genuinely need superuser:
# create/de-privilege the migrator role, reassign ownership of the tenanted
# tables to it, apply the runtime/readonly grants, and set role passwords.
#
# Connection roles:
#   TAPPS_BRAIN_MIGRATE_BOOTSTRAP_DSN  — superuser (tapps). Used ONLY for the
#       privileged bootstrap below. Never opens an audit-guarded MemoryStore.
#   TAPPS_BRAIN_DATABASE_URL           — tapps_migrator (de-privileged). Used
#       for ALL schema-migration DDL. The connection the guard inspects.
#
# Order:
#   0. [super] roles/002 — create/de-privilege tapps_migrator, reassign the
#      tenanted tables, set the migrator password.
#   1. [migrator] apply Hive migrations          (no override flag)
#   2. [migrator] apply private + federation migrations  (no override flag)
#   2b.[migrator] pre-create lazy-DDL tables the runtime role can't
#   3. [super] roles/001 + catch-all grants for tapps_runtime / tapps_readonly
#   4. [super] set the tapps_runtime password
#
# After this sidecar exits 0, the brain connects as tapps_runtime (no
# superuser, no BYPASSRLS, no table ownership) and the migrate path no longer
# needs or sets TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE.

set -euo pipefail

: "${TAPPS_BRAIN_DATABASE_URL:?TAPPS_BRAIN_DATABASE_URL (tapps_migrator DSN) must be set}"
: "${TAPPS_BRAIN_MIGRATE_BOOTSTRAP_DSN:?TAPPS_BRAIN_MIGRATE_BOOTSTRAP_DSN (superuser DSN) must be set}"
: "${TAPPS_BRAIN_MIGRATOR_PASSWORD:?TAPPS_BRAIN_MIGRATOR_PASSWORD must be set}"
: "${TAPPS_BRAIN_RUNTIME_PASSWORD:?TAPPS_BRAIN_RUNTIME_PASSWORD must be set}"

BOOTSTRAP_DSN="${TAPPS_BRAIN_MIGRATE_BOOTSTRAP_DSN}"
MIGRATOR_DSN="${TAPPS_BRAIN_DATABASE_URL}"

echo "[migrate] Step 0/4 — privileged bootstrap: de-privilege migrator + reassign ownership"
# Create/de-privilege tapps_migrator and reassign the tenanted tables to it.
# Idempotent and safe on a fresh DB (IF EXISTS guards skip not-yet-created
# tables — the migrator will create and therefore own them in steps 1-2).
psql "${BOOTSTRAP_DSN}" -v ON_ERROR_STOP=1 \
    -f /opt/tapps-brain/migrations/roles/002_migrator_deprivilege.sql \
    > /tmp/roles002.log 2>&1 || { cat /tmp/roles002.log; exit 1; }
tail -n 3 /tmp/roles002.log
# Set the migrator password (kept out of the SQL file, like the runtime one).
psql "${BOOTSTRAP_DSN}" -v ON_ERROR_STOP=1 \
    -c "ALTER ROLE tapps_migrator WITH LOGIN PASSWORD '${TAPPS_BRAIN_MIGRATOR_PASSWORD}';" \
    > /dev/null
echo "[migrate] tapps_migrator is NOSUPERUSER NOBYPASSRLS and owns the tenanted tables"

# Pre-create the extensions the migrations need. CREATE EXTENSION requires
# superuser, which the de-privileged migrator is NOT — so create them here in
# the privileged bootstrap. The migrations use CREATE EXTENSION IF NOT EXISTS,
# which is a no-op for a non-superuser once the extension already exists, so the
# migrator's calls succeed against an already-bootstrapped DB.
psql "${BOOTSTRAP_DSN}" -v ON_ERROR_STOP=1 <<'SQL' > /dev/null
  CREATE EXTENSION IF NOT EXISTS vector;
  CREATE EXTENSION IF NOT EXISTS pgcrypto;
  CREATE EXTENSION IF NOT EXISTS pg_trgm;
SQL
echo "[migrate] required extensions present (vector, pgcrypto, pg_trgm)"

echo "[migrate] Step 1/4 — apply Hive schema migrations (as tapps_migrator)"
tapps-brain maintenance migrate-hive --dsn "${MIGRATOR_DSN}"

echo "[migrate] Step 2/4 — apply private + federation schema migrations (as tapps_migrator)"
# Runs as the de-privileged migrator. No TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE: the
# guard passes because the migrator is NOSUPERUSER/NOBYPASSRLS and owns only
# FORCE-RLS tenanted tables (TAP-2673 + the step-0 reassignment).
TAPPS_BRAIN_DATABASE_URL="${MIGRATOR_DSN}" python3 -c "
import os
os.environ['TAPPS_BRAIN_AUTO_MIGRATE'] = '1'
os.environ['TAPPS_BRAIN_HIVE_AUTO_MIGRATE'] = '1'
from tapps_brain.store import MemoryStore
from tapps_brain.postgres_migrations import apply_federation_migrations
from pathlib import Path
import tempfile
dsn = os.environ['TAPPS_BRAIN_DATABASE_URL']
with tempfile.TemporaryDirectory() as td:
    store = MemoryStore(Path(td))
    store.close()
print('[migrate] private + hive migrations applied')
applied = apply_federation_migrations(dsn)
print(f'[migrate] federation migrations applied: {applied}')
"

echo "[migrate] Step 2b/4 — pre-create lazy-DDL tables the runtime role can't"
# `private_relations` is created lazily on first use by postgres_private.py via
# a runtime CREATE TABLE IF NOT EXISTS — but tapps_runtime has no CREATE
# privilege. Pre-create it here as the migrator (which has CREATE) so the
# migrator owns it.
psql "${MIGRATOR_DSN}" -v ON_ERROR_STOP=1 <<'SQL' > /dev/null
  CREATE TABLE IF NOT EXISTS private_relations (
      project_id          TEXT        NOT NULL,
      agent_id            TEXT        NOT NULL,
      subject             TEXT        NOT NULL,
      predicate           TEXT        NOT NULL,
      object_entity       TEXT        NOT NULL,
      source_entry_keys   JSONB       NOT NULL DEFAULT '[]'::jsonb,
      confidence          REAL        NOT NULL DEFAULT 0.8,
      created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY (project_id, agent_id, subject, predicate, object_entity)
  );
  CREATE INDEX IF NOT EXISTS idx_priv_rel_project_agent
      ON private_relations (project_id, agent_id);
SQL
echo "[migrate] lazy-DDL tables pre-created"

echo "[migrate] Step 3/4 — apply role split + catch-all grants (privileged bootstrap)"
# Grants run as the superuser bootstrap: on an existing DB most tables remain
# owned by the original owner, so only a superuser can GRANT across all of them.
# The roles SQL file enumerates tables by name for the per-table grants; we
# follow it with a catch-all GRANT ... ON ALL TABLES so the runtime role gets
# DML on every existing table regardless of when its migration was added.
psql "${BOOTSTRAP_DSN}" -v ON_ERROR_STOP=1 \
    -f /opt/tapps-brain/migrations/roles/001_db_roles.sql \
    > /tmp/roles.log 2>&1 || { cat /tmp/roles.log; exit 1; }
tail -n 3 /tmp/roles.log

psql "${BOOTSTRAP_DSN}" -v ON_ERROR_STOP=1 <<'SQL' > /dev/null
  GRANT USAGE ON SCHEMA public TO tapps_runtime, tapps_readonly;
  GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO tapps_runtime;
  GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO tapps_runtime;
  GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO tapps_runtime;
  GRANT SELECT ON ALL TABLES IN SCHEMA public TO tapps_readonly;
  GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO tapps_readonly;
  -- Future tables/sequences created by the migrator inherit the same grants.
  ALTER DEFAULT PRIVILEGES FOR ROLE tapps_migrator IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO tapps_runtime;
  ALTER DEFAULT PRIVILEGES FOR ROLE tapps_migrator IN SCHEMA public
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO tapps_runtime;
  ALTER DEFAULT PRIVILEGES FOR ROLE tapps_migrator IN SCHEMA public
    GRANT EXECUTE ON FUNCTIONS TO tapps_runtime;
  ALTER DEFAULT PRIVILEGES FOR ROLE tapps_migrator IN SCHEMA public
    GRANT SELECT ON TABLES TO tapps_readonly;
SQL
echo "[migrate] catch-all grants applied to tapps_runtime + tapps_readonly"

echo "[migrate] Step 4/4 — set TAPPS_BRAIN_RUNTIME_PASSWORD on tapps_runtime"
# ALTER ROLE is idempotent; re-running rolls the password cleanly.
psql "${BOOTSTRAP_DSN}" -v ON_ERROR_STOP=1 \
    -c "ALTER ROLE tapps_runtime WITH LOGIN PASSWORD '${TAPPS_BRAIN_RUNTIME_PASSWORD}';" \
    > /dev/null

echo "[migrate] done. Brain can now connect as tapps_runtime."
