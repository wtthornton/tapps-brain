# Hive Deployment Guide

This guide covers deploying the tapps-brain Hive (shared Postgres brain) in
various environments.

## Quick Start

The fastest path uses Docker Compose with the reference files in `docker/`.

```bash
# 1. Create secrets
mkdir -p docker/secrets
echo "your-secure-password" > docker/secrets/tapps_hive_password.txt

# 2. Configure environment
cp docker/.env.example docker/.env
# Edit docker/.env — set TAPPS_HIVE_PASSWORD to match your secret

# 3. Start the stack
docker compose -f docker/docker-compose.hive.yaml up -d

# 4. Verify the DB is healthy
docker compose -f docker/docker-compose.hive.yaml ps
```

The migration sidecar (`tapps-hive-migrate`) runs once, applies pending schema
migrations, and exits. The database container stays running.

## Single-Host Deployment

For a single host (development, small team, or personal use):

1. Run the Docker Compose stack as shown above.
2. Point your tapps-brain config at the local Postgres:

   ```bash
   export TAPPS_BRAIN_HIVE_DSN="postgres://tapps:changeme@localhost:5432/tapps_hive"
   ```

3. Optionally enable auto-migration so the schema stays current when you
   upgrade tapps-brain:

   ```bash
   export TAPPS_BRAIN_HIVE_AUTO_MIGRATE=true
   ```

4. Run the MCP server or CLI as usual. Hive queries will use Postgres
   (SQLite Hive was removed in v3; ADR-007).

## Multi-Host Deployment

For teams sharing a single Hive across multiple machines:

1. Deploy pgvector on a reachable host (or managed Postgres with the `vector`
   extension enabled).
2. Run the migration container once against the remote DSN:

   ```bash
   tapps-brain maintenance migrate-hive \
     --dsn "postgres://tapps:SECRET@db-host:5432/tapps_hive"
   ```

3. On each client machine, set:

   ```bash
   export TAPPS_BRAIN_HIVE_DSN="postgres://tapps:SECRET@db-host:5432/tapps_hive"
   ```

4. Each client's local Postgres store remains independent (scoped by `(project_id, agent_id)`); only the shared Hive layer is remote.

## Kubernetes Patterns

### Database: StatefulSet

```yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: tapps-hive-db
spec:
  serviceName: tapps-hive-db
  replicas: 1
  selector:
    matchLabels:
      app: tapps-hive-db
  template:
    metadata:
      labels:
        app: tapps-hive-db
    spec:
      containers:
        - name: pgvector
          image: pgvector/pgvector:pg17
          env:
            - name: POSTGRES_DB
              value: tapps_hive
            - name: POSTGRES_USER
              value: tapps
            - name: POSTGRES_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: tapps-hive-secret
                  key: password
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

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: tapps-hive-migrate
spec:
  template:
    spec:
      containers:
        - name: migrate
          image: your-registry/tapps-brain:latest
          command: ["tapps-brain", "maintenance", "migrate-hive", "--dsn", "$(TAPPS_BRAIN_HIVE_DSN)"]
          env:
            - name: TAPPS_BRAIN_HIVE_DSN
              valueFrom:
                secretKeyRef:
                  name: tapps-hive-secret
                  key: dsn
      restartPolicy: Never
```

### ConfigMap for Clients

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: tapps-brain-config
data:
  TAPPS_BRAIN_HIVE_AUTO_MIGRATE: "false"
```

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `TAPPS_BRAIN_HIVE_DSN` | (none) | Full Postgres DSN for the Hive backend |
| `TAPPS_BRAIN_HIVE_POSTGRES_DSN` | (none) | Alias for `TAPPS_BRAIN_HIVE_DSN` |
| `TAPPS_BRAIN_HIVE_AUTO_MIGRATE` | (none) | Set to `true`, `1`, or `yes` to auto-migrate on startup |
| `TAPPS_HIVE_PORT` | `5432` | Host port for Compose port mapping |
| `TAPPS_HIVE_PASSWORD` | `tapps` | Compose default password (override in production) |

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
postgres://tapps:SECRET@db-host:5432/tapps_hive?sslmode=verify-full&sslrootcert=/path/to/ca.crt
```

### Docker Secrets

The reference Compose file reads the password from
`docker/secrets/tapps_hive_password.txt` via Docker secrets. Never commit
this file to version control.

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
docker compose -f docker/docker-compose.hive.yaml ps
```

### Schema Status

```bash
tapps-brain maintenance hive-schema-status --dsn "$TAPPS_BRAIN_HIVE_DSN"
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
| `connection refused` | DB not running or wrong port | Check `docker ps` and `TAPPS_HIVE_PORT` |
| `password authentication failed` | Mismatched secret vs env var | Ensure `TAPPS_HIVE_PASSWORD` matches `docker/secrets/tapps_hive_password.txt` |
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
