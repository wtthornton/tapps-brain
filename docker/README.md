# Docker Artifacts for tapps-brain

Quick reference for Docker-based Hive deployment.

## Files

| File | Purpose |
|------|---------|
| `docker-compose.hive.yaml` | Reference Compose file: pgvector DB + migration sidecar |
| `Dockerfile.migrate` | Slim image that runs `tapps-brain maintenance migrate-hive` |
| `init-hive.sql` | Bootstraps the `vector` extension on first DB start |
| `.env.example` | Sample environment variables (copy to `.env` and edit) |
| `secrets/` | Directory for Docker secrets (e.g. `tapps_hive_password.txt`) |

## Quick Start

```bash
# 1. Create the secrets directory and password file
echo "your-secure-password" > docker/secrets/tapps_hive_password.txt

# 2. Copy and edit environment
cp docker/.env.example docker/.env

# 3. Start the stack
docker compose -f docker/docker-compose.hive.yaml up -d

# 4. Verify
docker compose -f docker/docker-compose.hive.yaml ps
```

The migration sidecar runs once on startup, applies any pending schema
migrations, and exits. The database container stays running with a health
check on `pg_isready`.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TAPPS_BRAIN_HIVE_DSN` | (none) | Full Postgres connection string |
| `TAPPS_HIVE_PORT` | `5432` | Host port mapped to Postgres |
| `TAPPS_HIVE_PASSWORD` | `tapps` | Postgres password (Compose default) |
| `TAPPS_BRAIN_HIVE_AUTO_MIGRATE` | (none) | Set to `true` to auto-migrate on startup |

See `docs/guides/hive-deployment.md` for full deployment guidance.
