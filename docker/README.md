# Docker Artifacts for tapps-brain

Quick reference for Docker-based Hive deployment.

## Files

| File | Purpose |
|------|---------|
| `docker-compose.hive.yaml` | Reference Compose file: pgvector DB + migration sidecar + visual frontend |
| `Dockerfile.migrate` | Slim image that runs `tapps-brain maintenance migrate-hive` |
| `Dockerfile.visual` | nginx image serving the brain-visual static frontend |
| `init-hive.sql` | Bootstraps the `vector` extension on first DB start |
| `.env.example` | Sample environment variables (copy to `.env` and edit) |
| `secrets/` | Directory for Docker secrets (e.g. `tapps_hive_password.txt`) |

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

### Manual steps (if not using make)

```bash
# 1. Create the secrets directory and password file
echo "your-secure-password" > docker/secrets/tapps_hive_password.txt

# 2. Build the wheel
uv build

# 3. Build images and start the stack
docker compose -f docker/docker-compose.hive.yaml build
docker compose -f docker/docker-compose.hive.yaml run --rm tapps-hive-migrate
docker compose -f docker/docker-compose.hive.yaml up -d tapps-visual

# 4. Verify
docker compose -f docker/docker-compose.hive.yaml ps
```

The migration container (`tapps-hive-migrate`) runs once, applies any pending schema
migrations, and exits. The database container stays running with a health
check on `pg_isready`.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TAPPS_BRAIN_HIVE_DSN` | (none) | Full Postgres connection string |
| `TAPPS_HIVE_PORT` | `5432` | Host port mapped to Postgres |
| `TAPPS_HIVE_PASSWORD` | `tapps` | Postgres password (Compose default) |
| `TAPPS_BRAIN_HIVE_AUTO_MIGRATE` | (none) | Set to `true` to auto-migrate on startup |
| `TAPPS_VISUAL_PORT` | `8088` | Host port for the brain-visual frontend |

## brain-visual frontend

The `tapps-visual` service serves the brain-visual snapshot UI at `http://localhost:8080` (or `$TAPPS_VISUAL_PORT`). Load a `brain-visual.json` export from `tapps-brain visual export` to explore memory health, retrieval stats, and the scorecard.

See `docs/guides/hive-deployment.md` for full deployment guidance.
