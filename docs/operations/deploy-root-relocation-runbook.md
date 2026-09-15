# Deploy Root Relocation Runbook (TAP-7539)

**Audience:** Operator performing the live-appliance relocation.
**Status:** PREPARATION ONLY. Nothing in this document has been executed against the
running appliance. Filing this runbook does not move, restart, or recreate anything —
that is an operator-authorised step that happens after this document is reviewed.
**Scope boundary:** acceptance box b1 ("the live compose project working_dir is a
durable path outside `/tmp`") belongs to the operator, not to any lane. This runbook
gives the operator the exact commands; it does not run them.

## Why

Measured 2026-09-15: the live `tapps-brain-http` container's compose project
`working_dir` label resolves to `/tmp/tapps-brain-deploy-cc218c28/docker`. `/tmp` on
this host is swept by `systemd-tmpfiles-clean.timer` per the `D /tmp 1777 root root
30d` rule in `/usr/lib/tmpfiles.d/tmp.conf` (exact atime/mtime/ctime semantics not
confirmed — do not quote a precise deletion date as measured). A durable deploy root
outside `/tmp` removes that exposure.

## Chosen durable directory

**`/home/wtthornton/tapps-brain-deploy`**

Rationale:
- Under the operator's home directory — not `/tmp`, not subject to the tmpfiles
  cleaner.
- Consistent with where the existing `.env` backup already lives
  (`/home/wtthornton/tapps-brain-backups/live-deploy-env-20260914/docker.env`), so the
  operator is not introducing a third location.
- A plain directory under `$HOME`, not a system path — no extra permissions or root
  ownership needed to create or maintain it.

## Required keys to carry forward

See [`deploy-env-key-reference.md`](deploy-env-key-reference.md) for the full table
(purpose + safe default for every key). Do not hand-copy values into this runbook —
copy the `.env` file itself.

## Pre-flight (read-only, safe to run anytime)

```bash
# 1. Confirm current compose working_dir (read-only, the sole source of truth)
docker inspect tapps-brain-http --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}'
# Expected (current, pre-relocation): /tmp/tapps-brain-deploy-cc218c28/docker

# 2. Confirm the backup .env is present and non-empty (never print its contents)
test -s /home/wtthornton/tapps-brain-backups/live-deploy-env-20260914/docker.env && echo "backup present"
```

## Relocation steps (OPERATOR ONLY — not run by this lane)

```bash
# 1. Create the durable root
mkdir -p /home/wtthornton/tapps-brain-deploy

# 2. Copy the full deploy directory tree (compose file, docker/ assets, .env) from the
#    current /tmp root to the durable root. Use the live working_dir from the pre-flight
#    inspect output above as the source — never a path found by searching the
#    filesystem for a similarly-named directory.
rsync -a --exclude '.git' /tmp/tapps-brain-deploy-cc218c28/ /home/wtthornton/tapps-brain-deploy/

# 3. Verify the copied .env matches the known-good backup (md5 compare, never print
#    contents)
md5sum /home/wtthornton/tapps-brain-deploy/docker/.env
# Expected: b391cdcef3ac39dfde5c19f6894ee902 (per TAP-7539 measured state, 2026-09-14)

# 4. Bring the stack up from the new root, pointing compose at the new project dir.
#    This is the only step that touches the running appliance — confirm with the
#    operator immediately before running it.
cd /home/wtthornton/tapps-brain-deploy/docker
docker compose -p tapps-brain -f docker-compose.hive.yaml --env-file .env up -d

# 5. Confirm the label now reflects the durable path
docker inspect tapps-brain-http --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}'
# Expected (post-relocation): /home/wtthornton/tapps-brain-deploy/docker
```

## Rollback

If step 4 fails or the post-relocation inspect in step 5 does not show the expected
durable path:

```bash
# Bring the stack back up from the original /tmp root — it is untouched by steps 1-3
# above (they only copy, never move or delete).
cd /tmp/tapps-brain-deploy-cc218c28/docker
docker compose -p tapps-brain -f docker-compose.hive.yaml --env-file .env up -d

# Confirm rollback
docker inspect tapps-brain-http --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}'
# Expected: /tmp/tapps-brain-deploy-cc218c28/docker (back to pre-relocation state)
```

Do not delete `/tmp/tapps-brain-deploy-cc218c28` until the operator has confirmed the
relocated stack is healthy (`curl http://localhost:8080/health`,
`docker compose ps`) for a full cycle of expected traffic.

## Known divergence to preserve, not silently fix

`TAPPS_BRAIN_PER_TENANT_AUTH=0` in the live `.env` overrides the compose default of `1`
(`docker/docker-compose.hive.yaml:167`). This is tracked separately on TAP-7328 and is
an operator decision. The relocation must copy the `.env` file verbatim (step 2 above)
so this override survives the move unchanged — do not reconcile it as part of
relocation.
