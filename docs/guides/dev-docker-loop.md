# Dev Docker loop — fast local deploy/test cycles

Use this guide when you deploy to the **local unified stack** (`docker/docker-compose.hive.yaml`, project `tapps-brain`) many times per day.

For production gates see [`scripts/publish-checklist.md`](../../scripts/publish-checklist.md) and [`hive-deployment.md`](hive-deployment.md).

## Tiered gates

| Tier | When | Commands |
|------|------|----------|
| **A — inner loop** | Every code change (10–20×/day) | `make dev-deploy` or `make hive-reload-http` + `make brain-smoke-live` |
| **B — merge confidence** | 2–4×/day | `make brain-lint`, `make brain-type`, `make brain-test-fast` |
| **C — pre-tag** | End of day / before release | `make brain-test`, `bash scripts/release-ready.sh`, `make brain-healthcheck` |

Post-deploy smoke: prefer **`make brain-smoke-live`** (~10s). Use **`make brain-healthcheck`** when MCP wiring or `.mcp.json` changed. Use **`make hive-smoke`** in CI only — it boots an isolated stack and tears it down.

## First-time setup

```bash
cp docker/.env.example docker/.env   # fill in secrets
make hive-deploy                     # full build: wheel + 3 images + migrate + up
make brain-healthcheck               # once: MCP wiring + project registration
```

Optional — warm embedding cache so restarts skip Hub download:

```bash
# After first successful deploy, add to docker/.env for dev:
# TAPPS_BRAIN_EMBEDDING_MODEL_OFFLINE=1
```

The `tapps-brain-hfcache` volume persists the HuggingFace model across container recreates.

## Inner loop (target ~3–8 min)

```bash
# One command: reload + smoke (auto-runs migrate when SQL/migration files changed)
make dev-deploy

# Force migrate sidecar even when SQL unchanged (e.g. after docker/.env password change)
MIGRATE=1 make dev-deploy
```

What `dev-deploy` does:

1. `check-brain-env` + compose isolation guard
2. **`hive-reload-http`** — `uv build`, rebuild **http image only**, restart `tapps-brain-http` (DB + visual untouched)
3. **`hive-reload`** instead when `scripts/migrations-changed.sh` detects changes under `src/tapps_brain/migrations/`, `docker/migrate-entrypoint.sh`, or `docker/Dockerfile.migrate` (or when `MIGRATE=1`)
4. **`brain-smoke-live`** — `/healthz`, `/ready`, experience API round-trip + version match

Manual equivalents:

```bash
make hive-reload-http      # code-only: wheel + http image + restart brain
make hive-reload           # wheel + http + migrate images, run migrate, restart brain
make brain-smoke-live      # verify running stack
```

## Makefile reference

| Target | Rebuilds | Runs migrate | Restarts |
|--------|----------|--------------|----------|
| `hive-deploy` | wheel + all 3 images | via `up -d` | full stack |
| `hive-reload-http` | wheel + http | no | `tapps-brain-http` |
| `hive-reload` | wheel + http + migrate | yes (`compose run`) | `tapps-brain-http` |
| `dev-deploy` | see above | conditional | + live smoke |
| `hive-up` | nothing | only if migrate image changed | all services |

BuildKit is enabled by default (`DOCKER_BUILDKIT=1`) for faster pip layers in Dockerfiles.

## Faster pytest (Tier B)

```bash
make brain-test-fast              # parallel (-n auto), no coverage, fail-fast
BRAIN_TEST_FAST_N=0 make brain-test-fast   # disable xdist
BRAIN_TEST_FAST_N=4 make brain-test-fast   # fixed worker count
```

## Keep the stack running

- Do **not** run `make hive-down` between iterations — volumes and DB stay warm.
- Use `docker compose -p tapps-brain restart tapps-brain-http` for env-only changes with no code rebuild.
- Dev pytest Postgres (`make brain-up`, project `tapps-brain-dev`) must stay off `tapps-brain_default` — see [postgres-dsn.md § Dev vs deploy](postgres-dsn.md#dev-vs-deploy-postgres-epic-076).

## When to escalate

| Change | Use |
|--------|-----|
| Python handlers / MCP tools | `dev-deploy` |
| SQL migrations / roles | `MIGRATE=1 make dev-deploy` or `make hive-reload` |
| nginx / brain-visual | `make hive-deploy` (visual image) |
| Auth / RLS / tenant headers | Tier C + `TAPPS_BRAIN_CROSS_TENANT_SMOKE=1` on release gate |
