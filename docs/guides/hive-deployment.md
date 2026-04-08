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

4. Run the MCP server or CLI as usual. Hive queries will use Postgres instead
   of the local SQLite hive.db.

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

4. Each client's local SQLite store remains independent; only the shared Hive
   layer is remote.

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

## Security

### SSL / TLS

Use `sslmode=require` (or `verify-full`) in your DSN:

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

### Common Issues

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `connection refused` | DB not running or wrong port | Check `docker ps` and `TAPPS_HIVE_PORT` |
| `password authentication failed` | Mismatched secret vs env var | Ensure `TAPPS_HIVE_PASSWORD` matches `secrets/tapps_hive_password.txt` |
| `extension "vector" does not exist` | Wrong Postgres image | Use `pgvector/pgvector:pg17` |
| Migration sidecar exits with error | Network timing | Check `depends_on` health condition; increase retries |
