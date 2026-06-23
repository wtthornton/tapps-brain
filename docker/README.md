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
   #   TAPPS_BRAIN_ALLOWED_ORIGINS   (required — compose sets STRICT=1)
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
| `make dev-deploy` | **Fast inner loop** — reload http (or migrate if SQL changed) + `brain-smoke-live` |
| `make hive-reload-http` | Wheel + http image only; restart `tapps-brain-http` (DB + visual unchanged) |
| `make hive-reload` | Wheel + http + migrate images; run migrate sidecar; restart brain |
| `make hive-build` | Build wheel + Docker images only |
| `make hive-up` | Start services without rebuilding |
| `make hive-down` | Stop containers (keeps volumes) |
| `make hive-logs` | Tail logs from all services |
| `make hive-smoke` | End-to-end smoke test: boots full stack on throwaway ports, asserts all endpoints, tears down |

See [docs/guides/dev-docker-loop.md](../docs/guides/dev-docker-loop.md) for tiered gates and 10–20 deploys/day workflow.

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
| `tapps-visual` | 8088 (host) | nginx: dashboard static files + `/snapshot` proxy + `/healthz` liveness |

### Visual dashboard quick triage (STORY-078.15)

| Check | Command | Healthy signal |
|-------|---------|----------------|
| Visual nginx up | `curl -sS http://localhost:8088/healthz` | `{"ok":true,"service":"tapps-visual"}` |
| Brain HTTP up | `curl -sS http://localhost:8080/healthz` | `"ok": true`, `"db_ok": true` |
| Snapshot JSON | `curl -sS -H "Authorization: Bearer <token>" http://localhost:8080/snapshot \| head -c 200` | `"schema_version": 2`, `fingerprint_sha256` |
| Recent errors | `docker logs tapps-brain-http --tail 50` | No crash loop / migration failures |

Runbook: [docs/guides/visual-snapshot.md#visual-dashboard-troubleshooting](../docs/guides/visual-snapshot.md#visual-dashboard-troubleshooting).

## Environment Variables

All values come from `docker/.env` via compose variable substitution.

| Variable | Default | Description |
|----------|---------|-------------|
| `TAPPS_BRAIN_DB_PASSWORD` | (required, no default — `:?` fail-fast) | Owner role password, used by DB container init + migrate sidecar |
| `TAPPS_BRAIN_RUNTIME_PASSWORD` | (required) | DML-only `tapps_runtime` role password — brain logs in with this |
| `TAPPS_BRAIN_AUTH_TOKEN` | (required) | Bearer token for the public data plane on :8080 |
| `TAPPS_BRAIN_ADMIN_TOKEN` | (required) | Bearer token for the operator MCP on :8090 |
| `TAPPS_BRAIN_ALLOWED_ORIGINS` | (required in `docker/.env`) | Comma-separated browser origins. **Required** — compose sets `TAPPS_BRAIN_STRICT=1`. Local dev: `http://127.0.0.1:8088,http://localhost:8088` |
| `TAPPS_HTTP_PORT` | `8080` | Host port mapped to the HTTP adapter |
| `TAPPS_VISUAL_PORT` | `8088` | Host port for the brain-visual frontend |
| `TAPPS_OPERATOR_MCP_PORT` | `8090` | Operator MCP port (loopback-only by default) |
| `TAPPS_OPERATOR_MCP_BIND` | `127.0.0.1` | Operator MCP bind address. Set to `0.0.0.0` only behind a reverse proxy with auth. |

### Full feature promotion (reference stack defaults)

The compose file and `docker/.env.example` enable the full tapps-brain surface by default:

| Area | Compose default | Notes |
|------|-----------------|-------|
| **FlashRank reranker** | On (image installs `[reranker]`) | Cross-encoder reranking after hybrid retrieval |
| **OTel SDK** | On (`[otel]` in image; `TAPPS_BRAIN_OTEL_ENABLED=1`) | Set `OTEL_EXPORTER_OTLP_ENDPOINT` to ship spans |
| **Write idempotency** | `TAPPS_BRAIN_IDEMPOTENCY=1` | `X-Idempotency-Key` replay cache |
| **Per-tenant auth** | `TAPPS_BRAIN_PER_TENANT_AUTH=1` | Requires `X-Project-Id`; falls back to global token until `project rotate-token` |
| **Embeddings** | `TAPPS_BRAIN_EMBEDDING_REQUIRED=1` | Hard-fail if model cannot load |
| **MCP tools/list** | All profile tools eager | No `defer_loading` in bundled `mcp_profiles.yaml` |

Strongly recommended in `docker/.env` (not defaulted to secrets): `HF_TOKEN`, `TAPPS_BRAIN_METRICS_TOKEN`.

After changing `docker/.env` or the Dockerfile extras, rebuild the http image:

```bash
make dev-deploy
```

Client wiring: add direct `tapps-brain` HTTP MCP with `X-Brain-Profile: full` — see [mcp-client-repo-setup.md](../docs/guides/mcp-client-repo-setup.md).

## brain-visual frontend

The `tapps-visual` service serves the brain-visual snapshot UI at `http://localhost:8088`
(or `$TAPPS_VISUAL_PORT`). The dashboard fetches live data from the `/snapshot` endpoint
proxied through nginx to `tapps-brain-http:8080`.

### `/snapshot` proxy timeout contract (STORY-078.4)

| Layer | Timeout | Notes |
|-------|---------|-------|
| nginx `proxy_connect_timeout` | **30s** | `docker/nginx-visual.conf` — time to establish upstream TCP |
| nginx `proxy_read_timeout` | **30s** | Must cover cold snapshot builds up to **25s** without nginx 504 |
| `brain_smoke_live.sh` urllib | **30s** | `scripts/brain_smoke_live.sh` — keep aligned with nginx |
| UI poll interval (default) | **30s** | `examples/brain-visual/index.html` — one in-flight fetch per cycle |

When `tapps-brain-http` is unreachable or nginx times out waiting for upstream, `/snapshot`
returns **JSON** (not nginx HTML) with `Content-Type: application/json`:

```json
{"error":"upstream_timeout","upstream":"tapps-brain-http"}
```

HTTP status is **502**, **503**, or **504** depending on the failure mode. Successful
upstream responses (HTTP 200 with VisualSnapshot JSON) are not modified by
`proxy_intercept_errors`.

The static `brain-visual.json` is no longer baked into the image. If `tapps-brain-http`
is not running, `/snapshot` returns **502** with the JSON body above — intentional so
the failure is visible to the dashboard and smoke tests.
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
