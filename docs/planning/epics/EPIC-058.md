---
id: EPIC-058
title: "Docker & Deployment Support — Postgres Hive infrastructure"
status: planned
priority: high
created: 2026-04-08
tags: [docker, postgres, deployment, infrastructure, health]
---

# EPIC-058: Docker & Deployment Support

## Context

tapps-brain is currently distributed as a Python package with no Docker artifacts. The target architecture requires:

1. A **Postgres container** for the shared Hive and Federation backends
2. **Reference docker-compose** showing how agent containers connect to the shared Postgres
3. **Health checks** that verify Hive connectivity (not just local store health)
4. **Schema initialization** that runs automatically on first deploy
5. **Backup and restore** guidance for the Postgres Hive

This epic provides the infrastructure glue so that deploying the Hive-aware architecture is copy-paste simple for any project (AgentForge, or future projects).

**Depends on:** EPIC-055 (Postgres backend must exist)
**Consumed by:** AgentForge EPIC-38

## Success Criteria

- [ ] `docker-compose.hive.yaml` reference file in tapps-brain repo
- [ ] Postgres container with pgvector extension, auto-initialized schema
- [ ] Health check endpoint/CLI that verifies Hive Postgres connectivity
- [ ] Backup/restore commands for Hive data
- [ ] Migration runs automatically on container startup
- [ ] Documentation: how to add Hive Postgres to any project's docker-compose

## Stories

### STORY-058.1: Reference docker-compose for Hive Postgres

**Status:** planned
**Effort:** M
**Depends on:** EPIC-055.1 (schema exists)
**Context refs:** `src/tapps_brain/migrations/hive/`
**Verification:** `docker compose -f docker-compose.hive.yaml up -d && tapps-brain maintenance hive-schema-status`

#### Why

Every project that wants shared Hive needs a Postgres instance. A reference docker-compose eliminates guesswork: correct image (pgvector), correct extensions, correct environment variables, volume mounts, and health checks.

#### Acceptance Criteria

- [ ] `docker/docker-compose.hive.yaml` in tapps-brain repo with:
  ```yaml
  services:
    tapps-hive-db:
      image: pgvector/pgvector:pg17
      environment:
        POSTGRES_DB: tapps_hive
        POSTGRES_USER: tapps
        POSTGRES_PASSWORD_FILE: /run/secrets/tapps_hive_password
      volumes:
        - tapps-hive-pgdata:/var/lib/postgresql/data
        - ./init-hive.sql:/docker-entrypoint-initdb.d/01-init.sql
      healthcheck:
        test: ["CMD-SHELL", "pg_isready -U tapps -d tapps_hive"]
        interval: 10s
        timeout: 5s
        retries: 5
      ports:
        - "5432:5432"  # configurable, not exposed in production
  ```
- [ ] `docker/init-hive.sql` — creates pgvector extension and runs initial schema migration
- [ ] `docker/docker-compose.hive.yaml` includes a `tapps-brain-migrate` init container that runs schema migrations
- [ ] Docker secrets support for password (not plain-text env var in production)
- [ ] Volume for persistent Postgres data
- [ ] Example `.env` file with `TAPPS_BRAIN_HIVE_DSN=postgres://tapps:password@tapps-hive-db:5432/tapps_hive`

---

### STORY-058.2: Auto-migration on startup

**Status:** planned
**Effort:** M
**Depends on:** EPIC-055.9 (migration tooling), STORY-058.1
**Context refs:** `src/tapps_brain/migrations/`
**Verification:** `pytest tests/integration/test_auto_migration.py -v --tb=short -m "not benchmark"`

#### Why

When a new version of tapps-brain deploys with schema changes, migrations must run before agents can use the Hive. An auto-migration option ensures this happens without manual operator intervention, while still supporting explicit migration for teams that prefer it.

#### Acceptance Criteria

- [ ] `TAPPS_BRAIN_HIVE_AUTO_MIGRATE=true` env var (default: false — opt-in for safety)
- [ ] When enabled: `AgentBrain.__init__` checks schema version and runs pending migrations before opening the backend
- [ ] When disabled: `AgentBrain.__init__` checks schema version and raises `SchemaMigrationRequired` if behind
- [ ] Migration holds an advisory lock (`pg_advisory_lock`) to prevent concurrent migration from multiple containers
- [ ] Advisory lock timeout: 30 seconds (configurable via `TAPPS_BRAIN_HIVE_MIGRATE_TIMEOUT`)
- [ ] Migration result logged at INFO level (which migrations ran, duration)
- [ ] Init container pattern documented as alternative to auto-migration

---

### STORY-058.3: Hive-aware health checks

**Status:** planned
**Effort:** M
**Depends on:** EPIC-055.3 (Postgres backend exists)
**Context refs:** `src/tapps_brain/health_check.py`, `src/tapps_brain/diagnostics.py`
**Verification:** `pytest tests/unit/test_health_check.py -v --tb=short -m "not benchmark"`

#### Why

Current health checks only verify local SQLite store connectivity. With a Postgres Hive, the health check must also verify Hive connectivity, schema version, and pool health. This is critical for Docker health checks and Kubernetes readiness probes.

#### Acceptance Criteria

- [ ] `HealthCheck` extended with Hive dimensions:
  - `hive_connected`: bool — can reach Postgres
  - `hive_schema_version`: int — current schema version
  - `hive_schema_current`: bool — no pending migrations
  - `hive_pool_size`: int — current connection pool size
  - `hive_pool_available`: int — available connections
  - `hive_latency_ms`: float — round-trip ping time
- [ ] `brain_status` MCP tool includes Hive health
- [ ] `tapps-brain status` CLI includes Hive health
- [ ] Health check completes in <2s (existing target, including Hive)
- [ ] If Hive is unreachable: health reports degraded (not failed — local still works)
- [ ] JSON output for machine parsing (Docker health check scripts)

---

### STORY-058.4: Backup and restore commands

**Status:** planned
**Effort:** M
**Depends on:** STORY-058.1
**Context refs:** `src/tapps_brain/cli.py` (maintenance sub-app)
**Verification:** `pytest tests/unit/test_backup_restore.py -v --tb=short -m "not benchmark"`

#### Why

The Hive Postgres contains shared knowledge across all agents and projects. Data loss is catastrophic. Backup/restore commands should be available as tapps-brain CLI operations (wrapping `pg_dump`/`pg_restore`) so operators don't need to know Postgres internals.

#### Acceptance Criteria

- [ ] `tapps-brain maintenance backup-hive --output hive-backup.sql` — runs `pg_dump` against Hive DSN
- [ ] `tapps-brain maintenance restore-hive --input hive-backup.sql` — runs `pg_restore`
- [ ] `--format` flag: `sql` (plain text, default) or `custom` (pg_dump custom format for large DBs)
- [ ] Backup includes: schema + data for all Hive tables (memories, groups, registry, feedback, federation)
- [ ] Restore verifies schema version compatibility before applying
- [ ] Backup command works with or without local tapps-brain store (operates on Postgres only)
- [ ] Timestamp appended to default filename if `--output` not specified: `hive-backup-2026-04-08T12:00:00.sql`
- [ ] Documentation in `docs/guides/hive-operations.md` covering backup schedules and disaster recovery

---

### STORY-058.5: Deployment documentation

**Status:** planned
**Effort:** M
**Depends on:** STORY-058.1, STORY-058.2, STORY-058.3, STORY-058.4
**Context refs:** `docs/guides/`
**Verification:** design-only — review documentation for completeness

#### Why

The new architecture has more moving parts than "pip install tapps-brain." Operators need a guide that covers: single-host Docker Compose, multi-host deployment, Kubernetes patterns, monitoring, and troubleshooting.

#### Acceptance Criteria

- [ ] `docs/guides/hive-deployment.md`:
  - **Quick start**: docker-compose up with reference files (5 minutes to working Hive)
  - **Single-host**: Multiple agent containers + Postgres on one machine
  - **Multi-host**: Agent containers on different machines, Postgres on dedicated host
  - **Kubernetes**: Postgres StatefulSet + agent Deployments + ConfigMap for DSN
  - **Environment variables**: Complete reference table for all `TAPPS_BRAIN_HIVE_*` vars
  - **Security**: SSL connections, password management (secrets, not env vars), network isolation
  - **Monitoring**: Health check integration, connection pool metrics, Postgres pg_stat
  - **Troubleshooting**: Common issues (connection refused, pool exhausted, migration failed, schema mismatch)
- [ ] `docs/guides/hive-operations.md`:
  - Backup/restore procedures
  - Schema migration (manual and auto)
  - Adding a new project to an existing Hive
  - Removing an agent or project from the Hive
  - Scaling Postgres (replicas, connection limits)
- [ ] `docker/README.md` — quick-reference for all Docker artifacts in the repo

## Priority Order

| Order | Story | Rationale |
|-------|-------|-----------|
| 1 | STORY-058.1 | Reference compose is the foundation |
| 2 | STORY-058.2 | Auto-migration makes first deploy smooth |
| 3 | STORY-058.3 | Health checks validate the deployment |
| 4 | STORY-058.4 | Backup/restore for production safety |
| 5 | STORY-058.5 | Documentation ties it all together |
