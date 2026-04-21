# Docker Artifacts for tapps-brain

Quick reference for the Docker deployment of tapps-brain. The stack is a **unified** tapps-brain-http container (serves private memory, Hive, and Federation on the same `/mcp/` + `/v1/*` API) + Postgres + an optional nginx dashboard.

> **Hive is a feature of tapps-brain, not a separate service.** The filenames and Makefile targets below keep the legacy `hive-*` / `hive.yaml` prefix, but what you're deploying is **the brain** — Hive tables live in the same Postgres as `private_memories` by default. Set `TAPPS_BRAIN_HIVE_DSN` to a different DSN only when you want Hive on a separate physical database (advanced — see [hive-deployment.md](../docs/guides/hive-deployment.md#advanced-split-db-deployment-optional)).

## Files

| File | Purpose |
|------|---------|
| `docker-compose.hive.yaml` | Reference Compose file: `tapps-brain-db` (pgvector) + `tapps-brain-migrate` (one-shot bootstrap) + `tapps-brain-http` (unified HTTP + `/mcp/` + operator MCP) + `tapps-visual` (nginx dashboard) |
| `Dockerfile.http` | Slim image that runs `tapps-brain serve` — HTTP adapter + `/mcp/` on :8080, operator MCP on :8090 |
| `Dockerfile.migrate` | Slim image whose entrypoint (`migrate-entrypoint.sh`) applies private + Hive + Federation schema, creates the DML-only `tapps_runtime` role, and sets its password |
| `migrate-entrypoint.sh` | 4-step bootstrap run by the migrate sidecar |
| `Dockerfile.visual` | nginx image serving the brain-visual static frontend |
| `nginx-visual.conf` | nginx config: static files + `/snapshot` proxy to `tapps-brain-http` |
| `nginx-visual-tls.conf` | nginx config variant with HTTPS/TLS (see [hive-tls.md](../docs/guides/hive-tls.md)) |
| `init-db.sql` | Bootstraps the `vector` extension on first DB start |
| `.env.example` | Template for `docker/.env` (the four required vars + optional overrides) |

## Before You Deploy

1. **Copy the env template and fill in strong random values**:

   ```bash
   cp docker/.env.example docker/.env
   # Edit docker/.env — the four required variables have openssl commands inline:
   #   TAPPS_BRAIN_DB_PASSWORD       (owner role, used by migrate sidecar)
   #   TAPPS_BRAIN_RUNTIME_PASSWORD  (tapps_runtime DML-only role, used by the brain)
   #   TAPPS_BRAIN_AUTH_TOKEN        (public bearer token)
   #   TAPPS_BRAIN_ADMIN_TOKEN       (operator MCP bearer token)
   ```

2. **Configure TLS** if exposing the dashboard to a network — see [docs/guides/hive-tls.md](../docs/guides/hive-tls.md).

`make hive-deploy` aborts with a clear error if `docker/.env` is missing or still contains `REPLACE_ME` placeholder values.

## Quick Start

From the repository root:

```bash
make hive-deploy
```

Other useful targets:

| Target | What it does |
|--------|--------------|
| `make hive-deploy` | Full deploy — check env → build wheel + images → up (migrate runs automatically) |
| `make hive-build` | Build wheel + Docker images only |
| `make hive-up` | Start services without rebuilding |
| `make hive-down` | Stop containers (keeps volumes) |
| `make hive-logs` | Tail logs from all services |
| `make hive-smoke` | End-to-end smoke test: boots full stack on throwaway ports, asserts all endpoints, tears down |

### Manual steps (if not using make)

```bash
# 1. Fill in docker/.env — see "Before You Deploy" above.

# 2. Build the wheel + images, then bring the whole stack up. The migrate
#    sidecar runs via depends_on:service_completed_successfully, so you do
#    NOT need a separate `compose run --rm`.
docker compose -p tapps-brain -f docker/docker-compose.hive.yaml --env-file docker/.env up -d --build

# 3. Verify
docker compose -p tapps-brain -f docker/docker-compose.hive.yaml ps
curl http://localhost:8080/health    # {"status":"ok","service":"tapps-brain",...}
curl http://localhost:8088/snapshot  # proxied through tapps-visual nginx
```

## Services

| Service | Ports | Purpose |
|---------|-------|---------|
| `tapps-brain-db` | 5432 (internal) | PostgreSQL + pgvector, DB `tapps_brain`, owner role `tapps` |
| `tapps-brain-migrate` | — | One-shot bootstrap (exits 0). Applies all schema migrations, creates `tapps_runtime` role, sets its password. |
| `tapps-brain-http` | 8080 (host) + 127.0.0.1:8090 | Unified brain: `/health` `/ready` `/metrics` `/snapshot` + `/mcp/` + `/v1/*` + `/admin/*` + operator MCP on :8090 (loopback). Connects as `tapps_runtime`. |
| `tapps-visual` | 8088 (host) | nginx: dashboard static files + `/snapshot` proxy |

## Environment Variables

All values come from `docker/.env` via compose variable substitution.

| Variable | Default | Description |
|----------|---------|-------------|
| `TAPPS_BRAIN_DB_PASSWORD` | (required, no default — `:?` fail-fast) | Owner role password, used by DB container init + migrate sidecar |
| `TAPPS_BRAIN_RUNTIME_PASSWORD` | (required) | DML-only `tapps_runtime` role password — brain logs in with this |
| `TAPPS_BRAIN_AUTH_TOKEN` | (required) | Bearer token for the public data plane on :8080 |
| `TAPPS_BRAIN_ADMIN_TOKEN` | (required) | Bearer token for the operator MCP on :8090 |
| `TAPPS_BRAIN_ALLOWED_ORIGINS` | (empty) | Comma-separated CORS origins for `/snapshot` (set in production) |
| `TAPPS_HTTP_PORT` | `8080` | Host port mapped to the HTTP adapter |
| `TAPPS_VISUAL_PORT` | `8088` | Host port for the brain-visual frontend |
| `TAPPS_OPERATOR_MCP_PORT` | `8090` | Operator MCP port (loopback-only by default) |
| `TAPPS_OPERATOR_MCP_BIND` | `127.0.0.1` | Operator MCP bind address. Set to `0.0.0.0` only behind a reverse proxy with auth. |

## brain-visual frontend

The `tapps-visual` service serves the brain-visual snapshot UI at `http://localhost:8088`
(or `$TAPPS_VISUAL_PORT`). The dashboard fetches live data from the `/snapshot` endpoint
proxied through nginx to `tapps-brain-http:8080`.

The static `brain-visual.json` is no longer baked into the image. If `tapps-brain-http`
is not running, `/snapshot` returns a 502 — this is intentional so the failure is visible.
To load a static export offline, mount it as a volume:

```bash
docker run -v ./my-export.json:/usr/share/nginx/html/brain-visual.json docker-tapps-visual:latest
```

Generate an export with:

```bash
tapps-brain visual export -o brain-visual.json
```

See [docs/guides/hive-deployment.md](../docs/guides/hive-deployment.md) for full deployment guidance.
See [docs/guides/hive-tls.md](../docs/guides/hive-tls.md) for TLS configuration.
