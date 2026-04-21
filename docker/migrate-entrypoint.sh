#!/usr/bin/env bash
# docker/migrate-entrypoint.sh — One-shot bootstrap for the tapps-brain DB.
#
# Runs as a high-privilege role (the Postgres owner, `tapps`) so it can
# perform DDL. Does three things in order:
#
#   1. Applies Hive schema migrations via `tapps-brain maintenance migrate-hive`.
#      Private + Federation schemas get applied by the brain on first connect
#      via TAPPS_BRAIN_AUTO_MIGRATE / TAPPS_BRAIN_HIVE_AUTO_MIGRATE logic
#      when the brain uses this owner role; we run them here too so the
#      runtime role never needs DDL rights.
#
#   2. Applies the least-privilege role split from
#      src/tapps_brain/migrations/roles/001_db_roles.sql — creates
#      tapps_runtime (DML-only) and tapps_readonly, grants them on the
#      now-existing tables.
#
#   3. Sets TAPPS_BRAIN_RUNTIME_PASSWORD on the tapps_runtime role so the
#      brain container can log in as it. Re-run is idempotent; the password
#      is the one from docker/.env so changing it rolls the runtime creds.
#
# After this sidecar exits 0, the brain connects as tapps_runtime (no
# superuser, no BYPASSRLS, no table ownership) and TAPPS_BRAIN_ALLOW_
# PRIVILEGED_ROLE is not set.

set -euo pipefail

: "${TAPPS_BRAIN_DATABASE_URL:?TAPPS_BRAIN_DATABASE_URL must be set}"
: "${TAPPS_BRAIN_RUNTIME_PASSWORD:?TAPPS_BRAIN_RUNTIME_PASSWORD must be set}"

echo "[migrate] Step 1/4 — apply Hive schema migrations"
tapps-brain maintenance migrate-hive --dsn "${TAPPS_BRAIN_DATABASE_URL}"

echo "[migrate] Step 2/4 — apply private + federation schema migrations"
# The CLI's `maintenance migrate` only covers the private schema (via
# MemoryStore's auto-migrate path); federation lives in a sibling function
# `apply_federation_migrations` in postgres_migrations.py that we call
# directly. Running them both here keeps all DDL in the owner-privileged
# bootstrap step.
python3 -c "
import os
os.environ['TAPPS_BRAIN_AUTO_MIGRATE'] = '1'
os.environ['TAPPS_BRAIN_HIVE_AUTO_MIGRATE'] = '1'
os.environ['TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE'] = '1'  # bootstrap-only
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
# `private_relations` is created lazily on first use by
# postgres_private.py via a runtime CREATE TABLE IF NOT EXISTS — but
# tapps_runtime has no CREATE privilege, so pre-create it here while we
# still have owner rights.
psql "${TAPPS_BRAIN_DATABASE_URL}" -v ON_ERROR_STOP=1 <<'SQL' > /dev/null
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

echo "[migrate] Step 3/4 — apply role split (tapps_runtime, tapps_readonly)"
# The roles SQL file is baked into the image at /opt/tapps-brain/
# migrations/roles/001_db_roles.sql. It enumerates tables by name for the
# per-table grants, but the enumeration was last updated in EPIC-063 —
# newer migrations (project_profiles, audit_log, idempotency_keys, etc.)
# add tables that the file doesn't cover. We follow the file with a
# catch-all `GRANT ... ON ALL TABLES IN SCHEMA public` so the runtime role
# gets DML on every existing table regardless of when it was added. Also
# covers sequences + functions so id serials and trigger functions work.
psql "${TAPPS_BRAIN_DATABASE_URL}" -v ON_ERROR_STOP=1 \
    -f /opt/tapps-brain/migrations/roles/001_db_roles.sql \
    > /tmp/roles.log 2>&1 || { cat /tmp/roles.log; exit 1; }
tail -n 3 /tmp/roles.log

psql "${TAPPS_BRAIN_DATABASE_URL}" -v ON_ERROR_STOP=1 <<'SQL' > /dev/null
  GRANT USAGE ON SCHEMA public TO tapps_runtime, tapps_readonly;
  GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO tapps_runtime;
  GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO tapps_runtime;
  GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO tapps_runtime;
  GRANT SELECT ON ALL TABLES IN SCHEMA public TO tapps_readonly;
  GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO tapps_readonly;
  -- Future tables/sequences created by the DB owner inherit the same grants.
  ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO tapps_runtime;
  ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO tapps_runtime;
  ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT EXECUTE ON FUNCTIONS TO tapps_runtime;
  ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO tapps_readonly;
SQL
echo "[migrate] catch-all grants applied to tapps_runtime + tapps_readonly"

echo "[migrate] Step 4/4 — set TAPPS_BRAIN_RUNTIME_PASSWORD on tapps_runtime"
# ALTER ROLE is idempotent; re-running rolls the password cleanly.
psql "${TAPPS_BRAIN_DATABASE_URL}" -v ON_ERROR_STOP=1 \
    -c "ALTER ROLE tapps_runtime WITH LOGIN PASSWORD '${TAPPS_BRAIN_RUNTIME_PASSWORD}';" \
    > /dev/null

echo "[migrate] done. Brain can now connect as tapps_runtime."
