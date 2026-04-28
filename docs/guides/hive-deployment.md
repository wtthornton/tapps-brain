# Hive Deployment Guide

> **Hive is a feature of tapps-brain, not a separate service.** "Deploying Hive" means deploying tapps-brain — the `hive_*` tables live in the same Postgres as `private_memories` and are served by the same `tapps-brain-http` container over the same `/mcp/` + `/v1/*` API. This guide covers the three deployment shapes: **unified** (default), **split-DB** (advanced), and **Kubernetes**.

## Quick Start (unified — recommended default)

One Postgres, one brain container, one API. Hive rides along automatically because the brain falls back to `TAPPS_BRAIN_DATABASE_URL` when `TAPPS_BRAIN_HIVE_DSN` is unset.

```bash
# 1. Copy the env template and generate strong random values
cp docker/.env.example docker/.env
# Edit docker/.env and fill the four required variables:
#   TAPPS_BRAIN_DB_PASSWORD       — owner-role password (DDL during bootstrap only)
#   TAPPS_BRAIN_RUNTIME_PASSWORD  — tapps_runtime role password (brain's live DSN)
#   TAPPS_BRAIN_AUTH_TOKEN        — public bearer token
#   TAPPS_BRAIN_ADMIN_TOKEN       — operator MCP bearer token
# Suggested commands are inline in docker/.env.example.

# 2. Bring up the unified stack (Postgres + migrate sidecar + brain + dashboard)
make hive-deploy                        # OR, directly:
# docker compose -p tapps-brain -f docker/docker-compose.hive.yaml \
#   --env-file docker/.env up -d --build

# 3. Verify
docker compose -p tapps-brain -f docker/docker-compose.hive.yaml ps
curl http://localhost:8080/health       # {"status":"ok","service":"tapps-brain",...}
```

The migrate sidecar (`tapps-brain-migrate`) runs once, as the DB owner role `tapps`: applies Hive + private + federation schema, creates the least-privilege `tapps_runtime` role, grants DML on all tables, sets `TAPPS_BRAIN_RUNTIME_PASSWORD` on the role, then exits. The `tapps-brain-http` container then starts and connects as `tapps_runtime` (no superuser, no `BYPASSRLS`, no table ownership) — the privileged-role audit guard stays on.

There is no "Hive service" to start separately; the brain serves private memory + Hive + Federation from the same DSN over the same `/mcp/` + `/v1/*` API.

## Single-Host Deployment (default: unified DSN)

For a single host (development, small team, or personal use), the compose file is already configured — you just fill in `docker/.env` and run `make hive-deploy`. If you need to run the brain outside Docker (CLI, embedded library, tests), point it at the same DB:

1. Run the Docker Compose stack as shown above (Quick Start).
2. Point the CLI / library at the brain's Postgres — **one DSN is enough**:

   ```bash
   # Connect as tapps_runtime (the DML-only role the migrate sidecar creates).
   # For admin work that needs DDL (re-run migrations, inspect system views),
   # connect as the `tapps` owner with TAPPS_BRAIN_DB_PASSWORD instead.
   export TAPPS_BRAIN_DATABASE_URL="postgres://tapps_runtime:${TAPPS_BRAIN_RUNTIME_PASSWORD}@localhost:5432/tapps_brain"
   ```

   Private memory, Hive, and Federation all use this DSN. You do **not** need to set `TAPPS_BRAIN_HIVE_DSN`.

3. Run the CLI or embed the Python library as usual. The `tapps-brain-http` container in the compose stack auto-migrates via its migrate sidecar; external processes do not need to set `TAPPS_BRAIN_AUTO_MIGRATE` unless they're running an independent DB. (SQLite Hive was removed in v3; ADR-007.)

## Advanced: split-DB deployment (optional)

Only use this when you need Hive on a **separate** Postgres from private memory — typical reasons: distinct backup cadence, tenant isolation, capacity separation across teams, or a managed HA Postgres for the shared layer while private stays on local disk.

1. Provision two pgvector databases (or two schemas on the same instance).
2. Run migrations against **both** (both accept the same CLI entrypoints used by the single-DB migrate sidecar):

   ```bash
   tapps-brain maintenance migrate --project-dir .          # private (uses TAPPS_BRAIN_DATABASE_URL)
   tapps-brain maintenance migrate-hive \
     --dsn "postgres://tapps:SECRET@hive-host:5432/tapps_brain"
   ```

   Then apply the role split on each DB:

   ```bash
   psql "postgres://tapps:SECRET@<host>:5432/tapps_brain" \
     -f src/tapps_brain/migrations/roles/001_db_roles.sql
   psql "postgres://tapps:SECRET@<host>:5432/tapps_brain" \
     -c "ALTER ROLE tapps_runtime WITH LOGIN PASSWORD '$TAPPS_BRAIN_RUNTIME_PASSWORD';"
   ```

3. Set both DSNs on the brain container, both pointing at `tapps_runtime`:

   ```bash
   export TAPPS_BRAIN_DATABASE_URL="postgres://tapps_runtime:$RT_PW@private-host:5432/tapps_brain"
   export TAPPS_BRAIN_HIVE_DSN="postgres://tapps_runtime:$RT_PW@hive-host:5432/tapps_brain"
   ```

4. The API surface does not change — agents still hit the same `/mcp/` + `/v1/*` on the same container. Only the physical database for Hive rows is different.

## Multi-Host Deployment (teams sharing one brain)

For teams, the normal pattern is **one brain deployment, many agent hosts** — not one Hive DB with many brains. Agents on each workstation/server point their MCP clients at the shared brain URL:

1. Deploy `tapps-brain-http` + Postgres on a reachable host (single-host or split-DB shape above).
2. On each client, point the MCP client at the shared brain:

   ```bash
   export TAPPS_BRAIN_BASE_URL="https://brain.internal:8080"
   export TAPPS_BRAIN_AUTH_TOKEN="<value of TAPPS_BRAIN_AUTH_TOKEN from docker/.env>"
   ```

3. Private rows are isolated per `(project_id, agent_id)`; Hive rows are the shared layer. Both live in the brain's Postgres — clients do not connect to Postgres directly.

## Kubernetes Patterns

### Database: StatefulSet

```yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: tapps-brain-db
spec:
  serviceName: tapps-brain-db
  replicas: 1
  selector:
    matchLabels:
      app: tapps-brain-db
  template:
    metadata:
      labels:
        app: tapps-brain-db
    spec:
      containers:
        - name: pgvector
          image: pgvector/pgvector:pg17
          env:
            - name: POSTGRES_DB
              value: tapps_brain
            - name: POSTGRES_USER
              value: tapps
            - name: POSTGRES_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: tapps-brain-secret
                  key: db-password
          ports:
            - containerPort: 5432
          volumeMounts:
            - name: pgdata
              mountPath: /var/lib/postgresql/data
  volumeClaimTemplates:
    - metadata:
        name: pgdata
      spec:
        accessModes: ["ReadWriteOnce"]
        resources:
          requests:
            storage: 10Gi
```

### Migration: Job

Use the `docker-tapps-brain-migrate` image (built from `docker/Dockerfile.migrate`) — its entrypoint is `docker/migrate-entrypoint.sh`, which applies private + Hive + Federation schema, creates the `tapps_runtime` role, and sets its password.

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: tapps-brain-migrate
spec:
  template:
    spec:
      containers:
        - name: migrate
          image: your-registry/tapps-brain-migrate:latest
          env:
            # Owner-role DSN — DDL capable, used by this one-shot job only.
            - name: TAPPS_BRAIN_DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: tapps-brain-secret
                  key: owner-dsn
            # Password the migrate job installs on the DML-only role.
            - name: TAPPS_BRAIN_RUNTIME_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: tapps-brain-secret
                  key: runtime-password
      restartPolicy: Never
```

### ConfigMap for the brain

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: tapps-brain-config
data:
  TAPPS_BRAIN_STRICT: "1"
  # Brain connects as tapps_runtime (see runtime-dsn secret key below); no
  # auto-migrate needed — the migrate Job handles schema and grants.
```

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `TAPPS_BRAIN_DATABASE_URL` | (required) | Postgres DSN. On the brain pod, use the `tapps_runtime` role. On the migrate Job, use the owner (`tapps`) role. Private + Hive + Federation share this DSN by default. |
| `TAPPS_BRAIN_DB_PASSWORD` | (required) | Owner role password — used only by the migrate Job and the DB container's init. |
| `TAPPS_BRAIN_RUNTIME_PASSWORD` | (required) | DML-only `tapps_runtime` role password — the brain logs in with this. |
| `TAPPS_BRAIN_HIVE_DSN` | (fallback: `TAPPS_BRAIN_DATABASE_URL`) | **Optional.** Only set when Hive lives on a different Postgres than private memory. |
| `TAPPS_BRAIN_FEDERATION_DSN` | (fallback: `TAPPS_BRAIN_DATABASE_URL`) | **Optional.** Same rule for Federation. |
| `TAPPS_BRAIN_AUTH_TOKEN` | (required) | Bearer token for the public `/mcp/` + `/v1/*` data plane. |
| `TAPPS_BRAIN_ADMIN_TOKEN` | (required) | Bearer token for the operator MCP transport on `:8090` (loopback by default). |
| `TAPPS_BRAIN_ALLOWED_ORIGINS` | (empty) | Comma-separated CORS origins for `/snapshot` (**set in production** — empty accepts all Origin headers, which exposes the data plane to DNS-rebinding attacks from any browser tab on the host network). |
| `TAPPS_BRAIN_METRICS_TOKEN` / `_TOKEN_FILE` | (unset) | Bearer token gating the full per-`(project_id, agent_id)` Prometheus surface on `/metrics` (TAP-547). When unset, `/metrics` serves a label-redacted body to any caller that can reach `:8080`. **Set in production.** Use `_TOKEN_FILE` to read from a file so the secret never enters the process environment. |
| `HF_TOKEN` | (unset) | HuggingFace Hub token for the embedding-model cache rehydrate path. When unset, model downloads use the unauthenticated rate limit and the embedding provider stalls during a cache-cold deploy until the limit window resets. **Set in production**, especially on hosts with eviction-prone tmpfs caches. |
| `TAPPS_BRAIN_AUTO_MIGRATE` / `TAPPS_BRAIN_HIVE_AUTO_MIGRATE` | (unset) | Leave unset on the brain — `tapps_runtime` cannot run DDL; migrations run in the migrate Job/sidecar. |

## Production hardening checklist

The runtime emits a startup warning for each of the gaps below — `docker logs tapps-brain-http` after a cold start will show the literal warning text. None block startup, so a dev stack still boots without them, but a production deploy that triggers any of them is leaking surface that downstream tenants and reconnaissance tooling can see. See [TAP-1076](https://linear.app/tappscodingagents/issue/TAP-1076) for the original ticket.

| Variable | Why it matters | Example value |
|----------|---------------|---------------|
| `TAPPS_BRAIN_ALLOWED_ORIGINS` | Empty → DNS-rebinding-class attacks against `:8080` succeed from any browser tab on the host network. | `https://dashboard.example.com,https://ide.example.com` |
| `TAPPS_BRAIN_METRICS_TOKEN` (or `_TOKEN_FILE`) | Unset → `/metrics` is callable unauthenticated; per-tenant labels are redacted but the request-rate surface still leaks. | `openssl rand -hex 32` |
| `HF_TOKEN` | Unset → embedding model re-downloads from HuggingFace are rate-limited; if the local cache evicts during a high-churn deploy the brain stops embedding for several minutes. | A read-only token from https://huggingface.co/settings/tokens |

All three are propagated by [`docker/docker-compose.hive.yaml`](../../docker/docker-compose.hive.yaml) — set them in `docker/.env` and they reach the container automatically. For Kubernetes, add them to the brain's ConfigMap (non-sensitive: `TAPPS_BRAIN_ALLOWED_ORIGINS`) or Secret (`TAPPS_BRAIN_METRICS_TOKEN`, `HF_TOKEN`).

```bash
# docker/.env (production hardening — append below the four required vars)
TAPPS_BRAIN_ALLOWED_ORIGINS=https://dashboard.example.com,https://ide.example.com
TAPPS_BRAIN_METRICS_TOKEN=$(openssl rand -hex 32)
HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

After redeploy, confirm none of the three startup warnings appear:

```bash
docker logs tapps-brain-http 2>&1 | grep -E "allowed_origins_empty|metrics_unauthenticated|HF_TOKEN"
# expect: no output
```

## Verifying Multi-Tenancy (Cross-Tenant Smoke Test)

The canonical end-to-end gate for "did multi-tenancy survive this change?" is:

```bash
tests/integration/test_cross_tenant_http.py
```

This test file (TAP-570) exercises all three isolation layers — the
`(project_id, agent_id)` composite key, RLS policies (migration 009), and
`FORCE ROW LEVEL SECURITY` (migration 012) — through the **live HTTP sidecar**
on `:8080`. It is the proof that downstream users of the AgentForge
multi-tenant topology actually depend on.

### What the test asserts

| Case | Assertion |
|------|-----------|
| 1 | `proj-a` saves a uniquely-valued memory → 200 |
| 2 | `proj-b` recall for `proj-a`'s sentinel value → **0 results** (RLS) |
| 3 | `proj-b` recall for `proj-a`'s exact key → **0 results** (RLS) |
| 4 | `proj-b` only sees its own data; `proj-a`'s rows never appear |
| 5 | `token-a` + `X-Project-Id: proj-b` → **403** (per-tenant token mismatch) |
| 6 | `token-b` + `X-Project-Id: proj-a` → **403** (symmetric) |
| 7 | `FORCE ROW LEVEL SECURITY` confirmed on both tenanted tables via SQL; owner-role SELECT scoped to `proj-b` returns **0 rows** for `proj-a`'s key |

### Running the smoke test

Start the compose stack, then:

```bash
# Required env vars:
export TAPPS_BRAIN_CROSS_TENANT_SMOKE=1
export TAPPS_BRAIN_ADMIN_TOKEN="your-admin-token"
export TAPPS_BRAIN_AUTH_TOKEN="your-data-token"

# Optional — enable per-tenant token mismatch assertions (cases 5–6).
# Must match the sidecar's TAPPS_BRAIN_PER_TENANT_AUTH setting.
export TAPPS_BRAIN_PER_TENANT_AUTH=1

# Optional — enable FORCE RLS SQL verification (case 7).
export TAPPS_TEST_POSTGRES_DSN="postgresql://tapps:tapps@localhost:5432/tapps_brain"

uv run pytest tests/integration/test_cross_tenant_http.py -v --tb=short -s
```

Or via the release gate (recommended pre-production):

```bash
TAPPS_BRAIN_CROSS_TENANT_SMOKE=1 \
TAPPS_BRAIN_ADMIN_TOKEN="..." \
TAPPS_BRAIN_AUTH_TOKEN="..." \
bash scripts/release-ready.sh
```

### When to run it

Run the cross-tenant smoke test before **any** production deployment that touches:
- `src/tapps_brain/http_adapter.py` (auth, tenant headers, data-plane routes)
- `src/tapps_brain/migrations/private/` (RLS policies, FORCE RLS)
- `src/tapps_brain/project_registry.py` (token storage, verification)
- `src/tapps_brain/postgres_connection.py` (role assertion, connection management)

The CI workflow runs it automatically on `workflow_dispatch` (manual trigger)
via the `cross-tenant-smoke` job.

## Security

### SSL / TLS

For HTTPS on the brain-visual dashboard endpoint, see [hive-tls.md](hive-tls.md)
for nginx SSL and Caddy reverse-proxy options.

For database connections, use `sslmode=require` (or `verify-full`) in your DSN:

```
postgres://tapps_runtime:SECRET@db-host:5432/tapps_brain?sslmode=verify-full&sslrootcert=/path/to/ca.crt
```

### Secrets via docker/.env

The reference Compose file reads all secrets (`TAPPS_BRAIN_DB_PASSWORD`, `TAPPS_BRAIN_RUNTIME_PASSWORD`, `TAPPS_BRAIN_AUTH_TOKEN`, `TAPPS_BRAIN_ADMIN_TOKEN`) from `docker/.env` via compose variable substitution. The file is gitignored — never commit it. The template is `docker/.env.example`.

### Network Isolation

- In Docker Compose, the database is on an internal bridge network by default.
  Only expose the port if external clients need direct access.
- In Kubernetes, use a `ClusterIP` service (no `NodePort` / `LoadBalancer`)
  for the database and restrict access with `NetworkPolicy`.

### HTTP + MCP bind address — loopback by default (TAP-597)

`tapps-brain serve` and `tapps-brain-http` now default to binding on `127.0.0.1`
instead of `0.0.0.0`. This prevents accidental public exposure when running
outside a container network.

**Docker Compose is unaffected** — `docker/docker-compose.hive.yaml` already sets
`TAPPS_BRAIN_HTTP_HOST: "0.0.0.0"` and `TAPPS_BRAIN_MCP_HOST: "0.0.0.0"` explicitly.

**Migration note (v3.9.x → v3.10+):** If you run `tapps-brain serve` or
`tapps-brain-http` directly on a multi-interface host and need remote access,
set the bind address explicitly:

```bash
# CLI
tapps-brain serve --host 0.0.0.0

# Environment (affects both CLI and tapps-brain-http)
export TAPPS_BRAIN_HTTP_HOST=0.0.0.0
```

When `--host 0.0.0.0` is used **without** `TAPPS_BRAIN_AUTH_TOKEN`,
`TAPPS_BRAIN_AUTH_TOKEN_FILE`, `TAPPS_BRAIN_HTTP_AUTH_TOKEN_FILE`, or
`TAPPS_BRAIN_PER_TENANT_AUTH=1`, a structured warning
`http_adapter.bind_all_interfaces_unauthenticated` is logged at startup.

### Operator MCP Port (8090) — loopback-only by default (TAP-551)

`docker-compose.hive.yaml` binds the operator MCP port to `127.0.0.1` by
default. This prevents accidental internet exposure on hosts without a
permissive-deny firewall.

**Migration note (v3.7.x → v3.8+):** If you previously relied on the old
`0.0.0.0` default to reach port 8090 from a remote host or reverse proxy,
set `TAPPS_OPERATOR_MCP_BIND=0.0.0.0` in your `.env` file (or the equivalent
in your orchestrator).  Deployments that only reach 8090 from the same host
(e.g. via `localhost:8090` in an admin runbook) are unaffected.

To expose via a local reverse proxy only:

```yaml
# In your override compose file:
services:
  tapps-brain:
    environment:
      TAPPS_OPERATOR_MCP_BIND: "127.0.0.1"  # default — keep as-is
```

Or to allow a dedicated ops network:

```yaml
    environment:
      TAPPS_OPERATOR_MCP_BIND: "0.0.0.0"   # pair with firewall / VPN rule
```

## Monitoring and Troubleshooting

### Health Check

```bash
# CLI health report (includes Hive fields when connected)
tapps-brain maintenance health --json

# Docker Compose health
docker compose -p tapps-brain -f docker/docker-compose.hive.yaml ps
```

### Schema Status

```bash
# Hive schema — accepts any DSN; use TAPPS_BRAIN_DATABASE_URL unless you
# have a split-DB deployment.
tapps-brain maintenance hive-schema-status --dsn "$TAPPS_BRAIN_DATABASE_URL"
```

### HNSW Index Startup Check (TAP-655)

At startup, `MemoryStore` calls `PostgresPrivateBackend.verify_expected_indexes()` which
queries `pg_indexes` to confirm `idx_priv_embedding_hnsw` (created by migration 002) is
present.  If the index is missing:

* A structured `WARNING` log is emitted: `event="private.indexes.missing"` with a `hint`
  pointing to migration 002.
* The Prometheus counter `tapps_brain_private_missing_indexes_total{project_id="..."}` is
  incremented so dashboards can alert on it.

**Alert rule (recommended):**
```
tapps_brain_private_missing_indexes_total > 0
```

**Resolution:** apply migration 002 against the private DB:
```bash
tapps-brain maintenance migrate --private
# or manually:
psql "$TAPPS_BRAIN_DATABASE_URL" -f src/tapps_brain/migrations/private/002_hnsw_upgrade.sql
```

Without this index, vector recall (`knn_search`) falls back to a sequential scan and is
significantly slower at scale.

### Common Issues

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `connection refused` | DB not running or wrong port | `docker compose -p tapps-brain ps` and check `TAPPS_HTTP_PORT` in `docker/.env` |
| `password authentication failed` for `tapps_runtime` | `TAPPS_BRAIN_RUNTIME_PASSWORD` in `docker/.env` doesn't match what the migrate sidecar set on the role | Restart the stack with `make hive-deploy` — the migrate sidecar is idempotent and will re-set the password to the current env value |
| `permission denied for schema public` on brain startup | Brain connecting as a role without USAGE on public, or migrate sidecar didn't run | Confirm the migrate sidecar exited 0; confirm the brain DSN points at `tapps_runtime` (`docker exec tapps-brain-http printenv TAPPS_BRAIN_DATABASE_URL`) |
| `extension "vector" does not exist` | Wrong Postgres image | Use `pgvector/pgvector:pg17` |
| Slow vector recall + `private.indexes.missing` warning | Migration 002 not applied | Run `002_hnsw_upgrade.sql` — see HNSW index startup check above |

---

## At-Rest Encryption (pg_tde)

The development Docker image (`pgvector/pgvector:pg17`) does **not** include pg_tde.
For production deployments requiring at-rest encryption, choose one of:

1. **pg_tde 2.1.2 on Percona Distribution for PostgreSQL 17** — full WAL + heap
   encryption with Vault/OpenBao key management. Requires replacing the Docker base
   image with `perconalab/percona-distribution-postgresql:17`.

2. **Cloud provider TDE** — AWS RDS, Google Cloud SQL, or Azure Database for PostgreSQL
   all provide managed at-rest encryption with no image changes required.

See the full runbook: [docs/guides/postgres-tde.md](./postgres-tde.md)
| Migration sidecar exits with error | Network timing | Check `depends_on` health condition; increase retries |

---

## Backup and Disaster Recovery

All durable Hive state lives in Postgres. Back it up regularly.

- **Guide (strategies + config):** [docs/guides/postgres-backup.md](./postgres-backup.md)
- **On-call checklist:** [docs/operations/postgres-backup-runbook.md](../operations/postgres-backup-runbook.md)

Key runbooks to rehearse before production go-live:

| Scenario | Runbook |
|----------|---------|
| Daily pg_dump backup | [Runbook 1](../operations/postgres-backup-runbook.md) |
| Full restore from pg_dump | [Runbook 2](../operations/postgres-backup-runbook.md) |
| Hive replica failover | [Runbook 5](../operations/postgres-backup-runbook.md) |
