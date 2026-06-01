# Migrate-sidecar de-privilege cutover (TAP-2686)

This runbook covers the **production cutover** that moves the migrate sidecar
off the `tapps` superuser and onto a de-privileged `tapps_migrator`
(`NOSUPERUSER NOBYPASSRLS`) role, so the deploy no longer sets
`TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE=1` and no longer emits the ERROR-level
`tenant isolation is NOT enforced` audit line.

> **Risk class:** ownership reassignment on a live ~12k-row table +
> introduction of a new DB credential. Do this in a maintenance window with a
> verified backup. The code is idempotent, but the REASSIGN is the one
> irreversible-feeling step — verify each gate before proceeding.

## What changed in code (already shipped on this branch)

- `src/tapps_brain/migrations/roles/002_migrator_deprivilege.sql` — idempotent:
  ensures `tapps_migrator` is `NOSUPERUSER NOBYPASSRLS`, grants it
  `CREATE/USAGE` on `public` + DML on existing tables, and reassigns ownership
  of `private_memories` + `project_profiles` (and any owned sequences) to it.
- `docker/migrate-entrypoint.sh` — schema-migration DDL now runs as
  `tapps_migrator` (`TAPPS_BRAIN_DATABASE_URL`); a short privileged bootstrap
  (`TAPPS_BRAIN_MIGRATE_BOOTSTRAP_DSN`, the superuser) only runs roles/002, the
  runtime/readonly grants, and the role-password ALTERs. **No
  `TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE`.**
- `docker/docker-compose.hive.yaml` — migrate service DSN → `tapps_migrator`
  creds + a separate superuser bootstrap DSN + `TAPPS_BRAIN_MIGRATOR_PASSWORD`.
- `docker/Dockerfile.migrate` — bakes `roles/002` into the image.

## Operator pre-step — add the migrator password to `docker/.env`

`docker/.env` is operator-owned (not in the repo). Add one line:

```bash
# Generate a strong value:
openssl rand -base64 32
# Then append to docker/.env (do NOT commit):
echo "TAPPS_BRAIN_MIGRATOR_PASSWORD=<generated-value>" >> docker/.env
```

`docker/.env.example` documents this var (TAP-2686 block at the bottom).

## Cutover steps

1. **Back up the DB** (or confirm a fresh, restorable snapshot exists):
   ```bash
   docker exec tapps-brain-db pg_dump -U tapps -d tapps_brain -Fc -f /tmp/pre-2686.dump
   docker cp tapps-brain-db:/tmp/pre-2686.dump ./pre-2686.dump
   ```

2. **Record the current ownership** (rollback reference):
   ```bash
   docker exec tapps-brain-db psql -U tapps -d tapps_brain -c \
     "SELECT relname, pg_get_userbyid(relowner) AS owner, relforcerowsecurity
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
       WHERE n.nspname='public' AND relname IN ('private_memories','project_profiles');"
   ```
   Expected before: owner=`tapps`, `relforcerowsecurity=t` on both.

3. **Rebuild + redeploy** the migrate sidecar (and the stack) with the new env:
   ```bash
   make hive-deploy   # or: docker compose -p tapps-brain -f docker/docker-compose.hive.yaml up -d --build
   ```
   The migrate sidecar runs Step 0 (privileged bootstrap → roles/002 reassign +
   migrator password) then Steps 1–4 as `tapps_migrator`.

4. **Verify the migrate sidecar log** shows no privileged-role line:
   ```bash
   docker logs tapps-brain-migrate 2>&1 | grep -i "privileged\|NOT enforced" || echo "clean — no privileged-role audit line"
   docker logs tapps-brain-migrate 2>&1 | tail -20
   ```
   Expect `tapps_migrator is NOSUPERUSER NOBYPASSRLS and owns the tenanted tables`
   and `done. Brain can now connect as tapps_runtime.`

5. **Verify ownership moved** to the de-privileged role:
   ```bash
   docker exec tapps-brain-db psql -U tapps -d tapps_brain -c \
     "SELECT relname, pg_get_userbyid(relowner) AS owner
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
       WHERE n.nspname='public' AND relname IN ('private_memories','project_profiles');
      SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname='tapps_migrator';"
   ```
   Expected: owner=`tapps_migrator`; `rolsuper=f`, `rolbypassrls=f`.

6. **Verify the brain is healthy** (still connects as `tapps_runtime`):
   ```bash
   curl -sf localhost:8080/health | python3 -m json.tool | head
   docker ps --filter name=tapps-brain-http --format '{{.Status}}'
   ```

## Rollback

If the brain fails to start or migrations error:

```bash
# Reassign ownership back to the superuser owner:
docker exec tapps-brain-db psql -U tapps -d tapps_brain \
  -f /opt/tapps-brain/migrations/roles/002_migrator_deprivilege.down.sql
# (or restore the pre-2686 dump if data integrity is in question)
```
Then redeploy the **previous** image tag (which still sets the override flag).

## Ownership scope

`roles/002` reassigns **every** application table + sequence in `public` to
`tapps_migrator` (a table's indexes/TOAST follow the table owner), not just the
two tenanted tables. This is required for the migrate path itself: a migration
applied as the de-privileged migrator may `ALTER`/`DROP` any object — e.g.
TAP-2676's IVFFlat→HNSW swap does `DROP INDEX idx_hive_embedding_ivfflat`, which
fails with `must be owner of index` unless the migrator owns `hive_memories`.
Validated against a restored copy of the production DB (12k+ rows): the
reassignment is metadata-only (sub-second) and the subsequent hive + federation
HNSW migrations apply cleanly as the migrator. Extensions, types, and functions
keep their original owner.
