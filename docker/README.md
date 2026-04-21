# Docker Artifacts for tapps-brain

Quick reference for the Docker deployment of tapps-brain. The stack is a **unified** tapps-brain-http container (serves private memory, Hive, and Federation on the same `/mcp/` + `/v1/*` API) + Postgres + an optional nginx dashboard.

> **Hive is a feature of tapps-brain, not a separate service.** The filenames and Makefile targets below keep the legacy `hive-*` / `hive.yaml` prefix, but what you're deploying is **the brain** — Hive tables live in the same Postgres as `private_memories` by default. Set `TAPPS_BRAIN_HIVE_DSN` to a different DSN only when you want Hive on a separate physical database (advanced — see [hive-deployment.md](../docs/guides/hive-deployment.md#advanced-split-db-deployment-optional)).

## Files

| File | Purpose |
|------|---------|
| `docker-compose.hive.yaml` | Reference Compose file: Postgres (pgvector) + unified `tapps-brain-http` + migration sidecar + nginx dashboard |
| `Dockerfile.http` | Slim image that runs `tapps-brain serve` — HTTP adapter + `/mcp/` on :8080, operator MCP on :8090 |
| `Dockerfile.migrate` | Slim image that runs `tapps-brain maintenance migrate-hive` (applies Hive schema to whichever DSN you point it at) |
| `Dockerfile.visual` | nginx image serving the brain-visual static frontend |
| `nginx-visual.conf` | nginx config: static files + `/snapshot` proxy to `tapps-brain-http` |
| `nginx-visual-tls.conf` | nginx config variant with HTTPS/TLS (see [hive-tls.md](../docs/guides/hive-tls.md)) |
| `init-hive.sql` | Bootstraps the `vector` extension on first DB start |
| `secrets/` | Docker secrets directory (`.txt` files are git-ignored; `.example` files are templates) |

## Before You Deploy

> **These steps are required before running `make hive-deploy` in any environment
> that is not purely local/dev.**

1. **Change the database password**
   ```bash
   openssl rand -base64 32 > docker/secrets/tapps_hive_password.txt
   ```

2. **Set the HTTP adapter auth token**
   ```bash
   openssl rand -base64 32 > docker/secrets/tapps_http_auth_token.txt
   ```

3. **Configure TLS** (if exposing the dashboard to a network)
   See [docs/guides/hive-tls.md](../docs/guides/hive-tls.md) for nginx SSL and Caddy options.

`make hive-deploy` will abort with a clear error message if either secret still contains
its default placeholder value.

## Quick Start

The recommended way to build and deploy is through the Makefile targets from the repository root:

```bash
# Full deploy: build wheel → build images → run migrations → restart services
make hive-deploy
```

Other useful targets:

| Target | What it does |
|--------|--------------|
| `make hive-deploy` | Full deploy (safe to rerun on every release) |
| `make hive-build` | Build wheel + Docker images only |
| `make hive-up` | Start services without rebuilding |
| `make hive-down` | Stop containers (keeps volumes) |
| `make hive-logs` | Tail logs from all hive services |
| `make hive-smoke` | End-to-end smoke test: boots full stack, asserts all endpoints, tears down |

### Manual steps (if not using make)

```bash
# 1. Create secrets (see "Before You Deploy" above)
openssl rand -base64 32 > docker/secrets/tapps_hive_password.txt
openssl rand -base64 32 > docker/secrets/tapps_http_auth_token.txt

# 2. Build the wheel
uv build

# 3. Build images, run migrations, start services
docker compose -f docker/docker-compose.hive.yaml build
docker compose -f docker/docker-compose.hive.yaml run --rm tapps-hive-migrate
docker compose -f docker/docker-compose.hive.yaml up -d tapps-brain-http tapps-visual

# 4. Verify
docker compose -f docker/docker-compose.hive.yaml ps
curl http://localhost:8080/health   # tapps-brain-http
curl http://localhost:8088/snapshot # proxied through tapps-visual nginx
```

The migration container (`tapps-hive-migrate`) runs once, applies any pending schema
migrations, and exits. The database and HTTP adapter containers stay running.

## Services

| Service | Port | Purpose |
|---------|------|---------|
| `tapps-hive-db` | 5432 | PostgreSQL + pgvector |
| `tapps-brain-http` | 8080 (internal) | HttpAdapter: `/health` `/ready` `/metrics` `/snapshot` |
| `tapps-visual` | 8088 (host) | nginx: dashboard static files + `/snapshot` proxy |
| `tapps-hive-migrate` | — | One-shot migration runner (exits after completion) |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TAPPS_BRAIN_HIVE_DSN` | (none) | Full Postgres connection string for the HTTP adapter |
| `TAPPS_HIVE_PORT` | `5432` | Host port mapped to Postgres |
| `TAPPS_HIVE_PASSWORD` | `tapps` | Postgres password (Compose interpolation — override via secret file) |
| `TAPPS_BRAIN_DATABASE_URL` | (none) | Private store DSN (optional; enables live MemoryStore in `/snapshot`) |
| `TAPPS_BRAIN_HTTP_HOST` | `0.0.0.0` | Bind address for the HTTP adapter |
| `TAPPS_BRAIN_HTTP_PORT` | `8080` | TCP port for the HTTP adapter (internal) |
| `TAPPS_HTTP_PORT` | `8080` | Host port mapped to the HTTP adapter |
| `TAPPS_VISUAL_PORT` | `8088` | Host port for the brain-visual frontend |

## brain-visual frontend

The `tapps-visual` service serves the brain-visual snapshot UI at `http://localhost:8088`
(or `$TAPPS_VISUAL_PORT`). The dashboard fetches live data from the `/snapshot` endpoint
proxied through nginx to `tapps-brain-http:8080`.

The static `brain-visual.json` is no longer baked into the image. If `tapps-brain-http`
is not running, `/snapshot` returns a 502 — this is intentional so the failure is visible.
To load a static export offline, mount it as a volume:

```bash
docker run -v ./my-export.json:/usr/share/nginx/html/brain-visual.json tapps-visual
```

Generate an export with:

```bash
tapps-brain visual export -o brain-visual.json
```

See [docs/guides/hive-deployment.md](../docs/guides/hive-deployment.md) for full deployment guidance.
See [docs/guides/hive-tls.md](../docs/guides/hive-tls.md) for TLS configuration.
