# Postgres Backup and Restore — tapps-brain

tapps-brain stores **all durable state** in PostgreSQL (ADR-007): private memories,
Hive, Federation, audit log, diagnostics history, and feedback events. Losing the
database loses everything. This guide makes disaster recovery a rehearsed procedure.

> **Quick reference:** For an ops-checklist format see
> [docs/operations/postgres-backup-runbook.md](../operations/postgres-backup-runbook.md).

---

## Schemas at a glance

| Schema / DSN variable           | Tables (key ones)                                        | Owned by        |
|---------------------------------|----------------------------------------------------------|-----------------|
| `TAPPS_BRAIN_DATABASE_URL`      | `private_memories`, `audit_log`, `diagnostics_history`, `feedback_events`, `session_chunks` | `tapps_runtime` |
| `TAPPS_BRAIN_HIVE_DSN`         | `hive_memories`, `agent_registry`, `hive_changes`        | `tapps_runtime` |
| `TAPPS_BRAIN_FEDERATION_DSN`   | `federation_memories`, `federation_projects`             | `tapps_runtime` |

The three DSNs may point to the same database (single-host) or separate databases
(large-scale). Back up every database that is in use.

---

## Strategy 1 — Logical backup (pg_dump)

Best for: development, point-in-time snapshots, schema-only migrations, partial
restores per schema/table.

### Full database dump

```bash
# Dump everything (all schemas) in custom format (recommended — smaller, parallel-restore)
pg_dump \
  --format=custom \
  --compress=9 \
  --file="tapps_brain_$(date +%Y%m%d_%H%M%S).pgdump" \
  "$TAPPS_BRAIN_DATABASE_URL"
```

### Schema-only dump (DDL without data)

```bash
pg_dump --schema-only --format=plain \
  --file="tapps_brain_schema_$(date +%Y%m%d).sql" \
  "$TAPPS_BRAIN_DATABASE_URL"
```

### Data-only dump (for selective restore)

```bash
# All data, no DDL
pg_dump --data-only --format=custom \
  --file="tapps_brain_data_$(date +%Y%m%d_%H%M%S).pgdump" \
  "$TAPPS_BRAIN_DATABASE_URL"

# Data for one schema only (e.g. private memory)
pg_dump --data-only --schema=public --format=custom \
  --table=private_memories \
  --file="private_memories_$(date +%Y%m%d_%H%M%S).pgdump" \
  "$TAPPS_BRAIN_DATABASE_URL"
```

### Restore from pg_dump

```bash
# Full restore (database must exist and be empty — run CREATE DATABASE first)
pg_restore \
  --dbname="$TAPPS_BRAIN_DATABASE_URL" \
  --jobs=4 \
  --verbose \
  tapps_brain_20260101_120000.pgdump

# Schema-only restore (re-apply DDL)
psql "$TAPPS_BRAIN_DATABASE_URL" < tapps_brain_schema_20260101.sql

# Data-only restore for one table (schema must already exist)
pg_restore \
  --dbname="$TAPPS_BRAIN_DATABASE_URL" \
  --data-only \
  --table=private_memories \
  tapps_brain_data_20260101_120000.pgdump
```

> **Tenant-aware partial restore** — because every row in `private_memories` is
> keyed by `(project_id, agent_id)`, you can roll back one tenant without affecting
> others using a filtered data dump:
>
> ```bash
> pg_dump --data-only \
>   --table=private_memories \
>   --where="project_id = 'my-project'" \
>   --format=custom \
>   --file="project_rollback.pgdump" \
>   "$TAPPS_BRAIN_DATABASE_URL"
> ```

### Example crontab (pg_dump)

```cron
# Daily full backup at 02:00, kept for 14 days
0 2 * * *  pg_dump --format=custom --compress=9 \
              --file="/backups/tapps_brain_$(date +\%Y\%m\%d).pgdump" \
              "$TAPPS_BRAIN_DATABASE_URL" \
           && find /backups -name "tapps_brain_*.pgdump" -mtime +14 -delete

# Hive backup at 02:30
30 2 * * *  pg_dump --format=custom --compress=9 \
              --file="/backups/tapps_hive_$(date +\%Y\%m\%d).pgdump" \
              "$TAPPS_BRAIN_HIVE_DSN" \
           && find /backups -name "tapps_hive_*.pgdump" -mtime +14 -delete
```

---

## Strategy 2 — Physical backup + WAL archiving (PITR)

Best for: production, near-zero RPO, point-in-time recovery to any second.

PITR requires two components working together:
1. A **base backup** (physical copy of `PGDATA`)
2. **WAL segments** archived continuously between base backups

### Configure WAL archiving in postgresql.conf

```ini
wal_level = replica
archive_mode = on
archive_command = 'cp %p /wal-archive/%f'   # adjust path or use pgBackRest
archive_timeout = 60   # force a WAL segment switch every 60 s (limits max data loss)
```

Reload after editing: `pg_ctlcluster 17 main reload` or `SELECT pg_reload_conf();`

### Take a base backup

```bash
# Using pg_basebackup (built-in, no extra install)
pg_basebackup \
  --host=localhost --port=5432 \
  --username=tapps \
  --pgdata=/backups/base/$(date +%Y%m%d_%H%M%S) \
  --format=tar \
  --gzip \
  --wal-method=stream \
  --checkpoint=fast \
  --progress \
  --verbose
```

### Point-in-time restore

1. Stop the running Postgres instance.
2. Replace `PGDATA` with the chosen base backup.
3. Create `PGDATA/recovery.signal` (Postgres 12+).
4. Set recovery target in `postgresql.conf` (or `postgresql.auto.conf`):

```ini
restore_command = 'cp /wal-archive/%f %p'
recovery_target_time = '2026-04-10 14:30:00 UTC'
recovery_target_action = promote
```

5. Start Postgres. It will replay WAL up to the target time and then promote.
6. Verify with `SELECT pg_is_in_recovery();` → returns `false` when done.

---

## Strategy 3 — pgBackRest (recommended for production)

pgBackRest is the production-default recommendation. It handles base backups, WAL
archiving, parallel restore, backup expiry, and encryption in one tool.

### Installation

```bash
# Debian / Ubuntu
sudo apt-get install pgbackrest

# RHEL / Rocky
sudo dnf install pgbackrest
```

### Minimal stanza config (`/etc/pgbackrest/pgbackrest.conf`)

```ini
[global]
repo1-path=/var/lib/pgbackrest
repo1-retention-full=7          # keep 7 full backups
repo1-retention-diff=14         # keep 14 differential backups
start-fast=y
log-level-console=info
log-level-file=detail

[tapps-brain]
pg1-path=/var/lib/postgresql/17/main
pg1-host=localhost
pg1-user=postgres
```

### postgresql.conf additions for pgBackRest

```ini
archive_mode = on
archive_command = 'pgbackrest --stanza=tapps-brain archive-push %p'
wal_level = replica
```

### Common pgBackRest commands

```bash
# Create the stanza (once, before first backup)
pgbackrest --stanza=tapps-brain stanza-create

# Full backup
pgbackrest --stanza=tapps-brain --type=full backup

# Differential backup (only changes since last full)
pgbackrest --stanza=tapps-brain --type=diff backup

# List backups
pgbackrest --stanza=tapps-brain info

# Restore to latest
pgbackrest --stanza=tapps-brain restore

# Restore to a point in time
pgbackrest --stanza=tapps-brain restore \
  --target="2026-04-10 14:30:00 UTC" \
  --target-action=promote \
  --type=time

# Verify backup integrity
pgbackrest --stanza=tapps-brain check
```

### Example crontab (pgBackRest)

```cron
# Full backup every Sunday at 01:00
0 1 * * 0  pgbackrest --stanza=tapps-brain --type=full backup

# Differential backup Mon–Sat at 01:00
0 1 * * 1-6  pgbackrest --stanza=tapps-brain --type=diff backup

# WAL archiving is continuous via archive_command — no cron needed
```

---

## Schema-independent restore

Each schema family can be backed up and restored independently. This is particularly
useful when Hive or Federation live on a separate database host.

### Restore only private memory

```bash
# Re-create the private memory schema from migrations
psql "$TAPPS_BRAIN_DATABASE_URL" \
  -f src/tapps_brain/migrations/private/001_initial.sql \
  -f src/tapps_brain/migrations/private/002_hnsw_upgrade.sql \
  -f src/tapps_brain/migrations/private/003_feedback_sessions.sql \
  -f src/tapps_brain/migrations/private/004_diagnostics_history.sql \
  -f src/tapps_brain/migrations/private/005_audit_log.sql

# Restore data only
pg_restore --dbname="$TAPPS_BRAIN_DATABASE_URL" --data-only \
  private_memories_backup.pgdump
```

### Restore only Hive

```bash
tapps-brain maintenance migrate-hive --dsn "$TAPPS_BRAIN_HIVE_DSN"  # re-apply DDL
pg_restore --dbname="$TAPPS_BRAIN_HIVE_DSN" --data-only hive_backup.pgdump
```

### Restore only Federation

```bash
tapps-brain maintenance migrate-federation --dsn "$TAPPS_BRAIN_FEDERATION_DSN"
pg_restore --dbname="$TAPPS_BRAIN_FEDERATION_DSN" --data-only federation_backup.pgdump
```

---

## Hive failover to a streaming replica

### 1. Configure streaming replication on primary

```ini
# postgresql.conf (primary)
wal_level = replica
max_wal_senders = 5
wal_keep_size = 256MB   # keep WAL for replica catch-up
```

```bash
# pg_hba.conf — allow replica to connect
echo "host replication tapps_replica REPLICA_IP/32 scram-sha-256" \
  >> /etc/postgresql/17/main/pg_hba.conf
```

### 2. Create the replication role

```sql
CREATE ROLE tapps_replica WITH LOGIN REPLICATION PASSWORD 'replica-secret';
```

### 3. Bootstrap the replica

```bash
pg_basebackup \
  --host=PRIMARY_HOST --port=5432 \
  --username=tapps_replica \
  --pgdata=/var/lib/postgresql/17/replica \
  --wal-method=stream \
  --checkpoint=fast \
  --write-recovery-conf       # writes standby.signal + primary_conninfo
```

The generated `postgresql.auto.conf` will contain:

```ini
primary_conninfo = 'host=PRIMARY_HOST port=5432 user=tapps_replica password=replica-secret'
```

### 4. Promote the replica on primary failure

```bash
# Graceful promote (Postgres 12+)
pg_ctlcluster 17 replica promote

# Or
touch /var/lib/postgresql/17/replica/promote.signal
```

### 5. Point tapps-brain at the new primary

```bash
export TAPPS_BRAIN_HIVE_DSN="postgres://tapps:SECRET@REPLICA_HOST:5432/tapps_hive"
# Restart MCP server / CLI for the new DSN to take effect
```

---

## Verify backups

Never skip verification. A backup that has never been restored is not a backup.

```bash
# pg_dump: restore to a test database and spot-check row counts
createdb tapps_verify
pg_restore --dbname=tapps_verify tapps_brain_backup.pgdump
psql tapps_verify -c "SELECT COUNT(*) FROM private_memories;"
dropdb tapps_verify

# pgBackRest: built-in integrity check
pgbackrest --stanza=tapps-brain check

# pgBackRest: full restore test to a staging host
pgbackrest --stanza=tapps-brain restore \
  --pg1-path=/var/lib/postgresql/17/staging \
  --delta
```

---

## Related docs

- [Hive Deployment Guide](./hive-deployment.md) — Compose / K8s setup and env vars
- [DB Roles Runbook](../operations/db-roles-runbook.md) — least-privilege roles setup
- [Ops On-Call Runbook](../operations/postgres-backup-runbook.md) — checklist for on-call
- [pg_tde Encryption](./postgres-tde.md) — at-rest encryption with Percona pg_tde
