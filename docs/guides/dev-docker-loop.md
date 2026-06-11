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
cp docker/.env.example docker/.env   # fill in secrets + keep TAPPS_BRAIN_ALLOWED_ORIGINS
make hive-deploy                     # full build: wheel + 3 images + migrate + up
make brain-healthcheck               # once: MCP wiring + project registration
```

`docker/.env` **must** include non-empty `TAPPS_BRAIN_ALLOWED_ORIGINS`. Compose sets
`TAPPS_BRAIN_STRICT=1`; without origins the brain refuses to start. The template ships a
local-dev default (`http://127.0.0.1:8088,http://localhost:8088` for tapps-visual). Agents:
when a user asks to upgrade local Docker, prefer `make dev-deploy` over `hive-deploy`.

Optional — warm embedding cache so restarts skip Hub download:

```bash
# After first successful deploy, add to docker/.env for dev:
# TAPPS_BRAIN_EMBEDDING_MODEL_OFFLINE=1
```

The `tapps-brain-hfcache` volume persists the HuggingFace model across container recreates.

### Full feature promotion

The reference Docker stack (`docker-compose.hive.yaml` + `docker/.env.example`) ships with:

- **`[reranker,otel]`** in the http image — FlashRank + OTLP export SDK
- **`TAPPS_BRAIN_IDEMPOTENCY=1`** — idempotent writes
- **`TAPPS_BRAIN_PER_TENANT_AUTH=1`** — per-project tokens (global bearer fallback until rotated)
- **All MCP tools eager** — full `tools/list` surface (no TAP-1985 defer curtain)

Set `OTEL_EXPORTER_OTLP_ENDPOINT` when you have a collector. Set `HF_TOKEN` and
`TAPPS_BRAIN_METRICS_TOKEN` in `docker/.env` before network exposure.

Wire IDE clients to the brain directly — see [mcp-client-repo-setup.md](mcp-client-repo-setup.md)
(`tapps-brain` HTTP MCP + `X-Brain-Profile: full` in `.mcp.json` / `.cursor/mcp.json`).

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
- For **env-only** changes in `docker/.env` (no image rebuild), recreate the brain container so new vars load:

  ```bash
  docker compose -p tapps-brain -f docker/docker-compose.hive.yaml up -d --no-deps --force-recreate tapps-brain-http
  make brain-smoke-live
  ```

- Dev pytest Postgres (`make brain-up`, project `tapps-brain-dev`) must stay off `tapps-brain_default` — see [postgres-dsn.md § Dev vs deploy](postgres-dsn.md#dev-vs-deploy-postgres-epic-076).

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Connection reset by peer` on `:8080` right after deploy | `TAPPS_BRAIN_ALLOWED_ORIGINS` missing/empty while `TAPPS_BRAIN_STRICT=1` | Add origins to `docker/.env`, recreate `tapps-brain-http` (see above) |
| `make dev-deploy` smoke fails immediately after recreate | Brain still loading embeddings (~30–60s on cold start) | Wait for `docker ps` → `(healthy)`, rerun `make brain-smoke-live` |
| `check-brain-env` aborts on `ALLOWED_ORIGINS` | Pre-flight guard before build | Copy the line from `docker/.env.example` or add your dashboard origins |

## When to escalate

| Change | Use |
|--------|-----|
| Python handlers / MCP tools | `dev-deploy` |
| SQL migrations / roles | `MIGRATE=1 make dev-deploy` or `make hive-reload` |
| nginx / brain-visual | `make hive-deploy` (visual image) |
| Auth / RLS / tenant headers | Tier C + `TAPPS_BRAIN_CROSS_TENANT_SMOKE=1` on release gate |
