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

# 3. Record container ids before relocation — the working_dir label alone only proves
#    relabelling, not that the appliance actually restarted from the new root. Compare
#    these ids against the post-relocation ids in step 5.
docker inspect tapps-brain-http tapps-brain-db tapps-visual --format '{{.Name}}: {{.Id}}'
# Measured 2026-09-15 (TAP-7539), for reference — expect different ids on this run:
#   tapps-brain-http=7d2021675b12  tapps-brain-db=d6b8b942b394  tapps-visual=7df4fef034b3
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
#
#    IMPORTANT: the service list is explicit and MUST stay explicit. A bare `up -d`
#    starts every service defined in docker-compose.hive.yaml, including
#    tapps-brain-maintenance — see "Incident: the bare `up -d` in step 4" below. Do
#    not simplify this back to a bare `up -d`, and do not "fix" it with a --scale or
#    --profile flag; docker-compose.hive.yaml defines no profiles, so a --profile
#    flag would silently do nothing.
cd /home/wtthornton/tapps-brain-deploy/docker
docker compose -p tapps-brain -f docker-compose.hive.yaml --env-file .env up -d \
  tapps-brain-db tapps-brain-migrate tapps-brain-http tapps-visual

# 5. Confirm the label now reflects the durable path
docker inspect tapps-brain-http --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}'
# Expected (post-relocation): /home/wtthornton/tapps-brain-deploy/docker

# 5a. Confirm the container ids actually changed (proves a real restart from the new
#     root, not just a relabel) — compare against the pre-flight ids from step 3 above.
docker inspect tapps-brain-http tapps-brain-db tapps-visual --format '{{.Name}}: {{.Id}}'
# Measured 2026-09-15 (TAP-7539), worked example — all three ids changed from pre-flight:
#   tapps-brain-http=fc02ea0ba5ad  tapps-brain-db=0fabb305039e  tapps-visual=7536d58b2252

# 5b. Confirm no unexpected service came up. Expected set is exactly the three long-
#     running services plus tapps-brain-migrate showing Exited (one-shot, by design).
#     tapps-brain-maintenance must NOT appear in this list.
docker compose -p tapps-brain -f docker-compose.hive.yaml --env-file .env ps
# Expected: tapps-brain-db (Up, healthy), tapps-brain-http (Up, healthy),
# tapps-visual (Up, healthy), tapps-brain-migrate (Exited, code 0). No
# tapps-brain-maintenance row.
```

## Rollback

If step 4 fails or the post-relocation inspect in step 5 does not show the expected
durable path:

```bash
# Bring the stack back up from the original /tmp root — it is untouched by steps 1-3
# above (they only copy, never move or delete). Same explicit service list as step 4 —
# a bare `up -d` here would start tapps-brain-maintenance just as readily as in the
# forward path. A rollback that starts a forbidden service is worse than the failure
# it is recovering from.
cd /tmp/tapps-brain-deploy-cc218c28/docker
docker compose -p tapps-brain -f docker-compose.hive.yaml --env-file .env up -d \
  tapps-brain-db tapps-brain-migrate tapps-brain-http tapps-visual

# Confirm rollback
docker inspect tapps-brain-http --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}'
# Expected: /tmp/tapps-brain-deploy-cc218c28/docker (back to pre-relocation state)
```

Do not delete `/tmp/tapps-brain-deploy-cc218c28` until the operator has confirmed the
relocated stack is healthy (`curl http://localhost:8080/health`,
`docker compose ps`) for a full cycle of expected traffic.

## Incident: the bare `up -d` in step 4 started a forbidden service

Relocation was executed against the live appliance with step 4 as originally written —
a bare `docker compose ... up -d` with no service list. `up -d` with no service list
starts every service defined in the compose file, and that included
`tapps-brain-maintenance`, which is under a standing "never start this" constraint. It
ran for 34 seconds before being stopped, and in that window it mutated live brain data
(one memory row deleted, one closed as stale, one learning demoted). Step 4 and the
rollback block above now pass an explicit service list for exactly this reason — the
command itself must be incapable of starting `tapps-brain-maintenance`, not merely
carry a warning next to it. Do not simplify either command block back to a bare
`up -d`; that is what triggered the incident.

## Known divergence, and what actually explains it

`TAPPS_BRAIN_PER_TENANT_AUTH` defaults to `1` in the compose file
(`docker/docker-compose.hive.yaml:169`), and `docker/.env:53` also reads `1` — in both
the live `.env` and its 2026-09-14 backup. The live appliance was nonetheless observed
reporting `0` at inspection time. That is **not** the `.env` overriding the compose
default (the `.env` value already agrees with the default); it was stale runtime state.
The container was created 2026-09-10T00:37:33Z, the `.env` was edited an hour later at
01:37 to something that briefly diverged, and the running container simply outlived
that edit without a recreate to pick it up. After the relocation's recreate, the
appliance correctly reports `1`, matching both the compose default and the `.env`.
This distinction matters for remediation: "the env file overrides the default" points
an operator at editing a file that is already correct, while "a running container
outlasted a config edit" points them at recreating the container instead — the two
diagnoses call for opposite fixes. The relocation still copies `.env` verbatim (step 2
above); there is nothing in it that needs reconciling.
