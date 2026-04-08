# Hive Operations Guide

Day-to-day operational procedures for the tapps-brain Hive Postgres backend.

## Backup and Restore

### Creating a Backup

```bash
# SQL format (human-readable, works with psql)
tapps-brain maintenance backup-hive \
  --dsn "$TAPPS_BRAIN_HIVE_DSN" \
  --output hive-backup-2026-04-08.sql

# Custom format (compressed, works with pg_restore)
tapps-brain maintenance backup-hive \
  --dsn "$TAPPS_BRAIN_HIVE_DSN" \
  --format custom \
  --output hive-backup-2026-04-08.dump
```

When `--output` is omitted, a timestamped filename is generated automatically
(e.g. `hive-backup-2026-04-08T120000.sql`).

### Restoring from Backup

```bash
# From a .sql backup
tapps-brain maintenance restore-hive \
  --dsn "$TAPPS_BRAIN_HIVE_DSN" \
  hive-backup-2026-04-08.sql

# From a custom-format backup
tapps-brain maintenance restore-hive \
  --dsn "$TAPPS_BRAIN_HIVE_DSN" \
  hive-backup-2026-04-08.dump
```

The restore command auto-detects the format based on the file extension
(`.sql` uses `psql`, anything else uses `pg_restore --clean --if-exists`).

### Scheduled Backups

Use cron or a Kubernetes CronJob:

```bash
# crontab example — daily at 02:00 UTC
0 2 * * * tapps-brain maintenance backup-hive --dsn "$TAPPS_BRAIN_HIVE_DSN" --output /backups/hive-$(date +\%Y-\%m-\%dT\%H\%M\%S).sql
```

## Schema Migration

### Manual Migration

```bash
# Check current schema version and pending migrations
tapps-brain maintenance hive-schema-status --dsn "$TAPPS_BRAIN_HIVE_DSN"

# Dry run — see what would be applied
tapps-brain maintenance migrate-hive --dsn "$TAPPS_BRAIN_HIVE_DSN" --dry-run

# Apply migrations
tapps-brain maintenance migrate-hive --dsn "$TAPPS_BRAIN_HIVE_DSN"
```

### Auto-Migration on Startup

Set the environment variable to have migrations run automatically when
`AgentBrain` or the MCP server starts:

```bash
export TAPPS_BRAIN_HIVE_AUTO_MIGRATE=true
```

Accepted values: `true`, `1`, `yes` (case-insensitive). Any other value
(including unset) disables auto-migration.

This is convenient for development and single-host deployments. For
production multi-host setups, prefer running the migration container or
CLI command explicitly to avoid race conditions.

## Adding and Removing Projects

Projects are represented as Hive namespaces. To list existing namespaces:

```bash
tapps-brain hive list --dsn "$TAPPS_BRAIN_HIVE_DSN"
```

To remove a project's data from the Hive (irreversible):

```bash
tapps-brain hive clear --namespace "project-name" --dsn "$TAPPS_BRAIN_HIVE_DSN"
```

## Adding and Removing Agents

```bash
# List registered agents
tapps-brain agent list --dsn "$TAPPS_BRAIN_HIVE_DSN"

# Register a new agent
tapps-brain agent register --name "agent-name" --dsn "$TAPPS_BRAIN_HIVE_DSN"

# Unregister
tapps-brain agent unregister --name "agent-name" --dsn "$TAPPS_BRAIN_HIVE_DSN"
```

## Scaling Postgres

### Vertical Scaling

Increase `shared_buffers`, `work_mem`, and `effective_cache_size` in
`postgresql.conf`. For pgvector workloads, also tune:

- `maintenance_work_mem` — affects index builds
- `max_parallel_workers_per_gather` — parallel seq scans for vector search

### Connection Pooling

For many concurrent agents, place PgBouncer or Pgpool-II in front of Postgres:

```
Agent 1 ─┐
Agent 2 ─┼─► PgBouncer ─► PostgreSQL
Agent 3 ─┘
```

Set `TAPPS_BRAIN_HIVE_DSN` to point at the pooler instead of directly at
Postgres.

### Read Replicas

pgvector supports streaming replication. Point read-heavy agents at a
read replica DSN and write-capable agents at the primary.
