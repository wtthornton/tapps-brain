# Postgres Backup Runbook — tapps-brain (Ops On-Call)

**Audience:** On-call engineers and SREs.
**Purpose:** Step-by-step checklist for common backup and restore operations.

> For background, strategy comparisons, and configuration detail see
> [docs/guides/postgres-backup.md](../guides/postgres-backup.md).

---

## Prerequisites

```bash
# Verify environment variables are set
echo "Private DSN: $TAPPS_BRAIN_DATABASE_URL"
echo "Hive DSN:    $TAPPS_BRAIN_HIVE_DSN"
echo "Fed DSN:     $TAPPS_BRAIN_FEDERATION_DSN"

# Test connectivity
psql "$TAPPS_BRAIN_DATABASE_URL" -c "SELECT version();"
psql "$TAPPS_BRAIN_HIVE_DSN"    -c "SELECT version();"
```

---

## Runbook 1 — Daily backup (pg_dump)

```bash
BACKUP_DIR="/backups/tapps-brain"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

mkdir -p "$BACKUP_DIR"

# 1. Dump private memory + audit tables
pg_dump --format=custom --compress=9 \
  --file="$BACKUP_DIR/private_${TIMESTAMP}.pgdump" \
  "$TAPPS_BRAIN_DATABASE_URL"

# 2. Dump Hive (skip if same DSN as private)
if [ "$TAPPS_BRAIN_HIVE_DSN" != "$TAPPS_BRAIN_DATABASE_URL" ]; then
  pg_dump --format=custom --compress=9 \
    --file="$BACKUP_DIR/hive_${TIMESTAMP}.pgdump" \
    "$TAPPS_BRAIN_HIVE_DSN"
fi

# 3. Dump Federation (skip if same DSN)
if [ "$TAPPS_BRAIN_FEDERATION_DSN" != "$TAPPS_BRAIN_DATABASE_URL" ]; then
  pg_dump --format=custom --compress=9 \
    --file="$BACKUP_DIR/federation_${TIMESTAMP}.pgdump" \
    "$TAPPS_BRAIN_FEDERATION_DSN"
fi

# 4. Prune backups older than 14 days
find "$BACKUP_DIR" -name "*.pgdump" -mtime +14 -delete

echo "Backup complete: $BACKUP_DIR/*_${TIMESTAMP}.pgdump"
```

---

## Runbook 2 — Restore from pg_dump

### Full restore (catastrophic loss)

```bash
BACKUP_FILE="/backups/tapps-brain/private_20260410_020000.pgdump"

# 1. Create a fresh database (if the old one is gone)
createdb tapps_brain

# 2. Restore
pg_restore \
  --dbname="$TAPPS_BRAIN_DATABASE_URL" \
  --jobs=4 \
  --verbose \
  "$BACKUP_FILE"

# 3. Spot-check
psql "$TAPPS_BRAIN_DATABASE_URL" \
  -c "SELECT COUNT(*) AS memories FROM private_memories;"
```

### Partial restore — roll back one project's private memory

```bash
PROJECT_ID="my-project"
BACKUP_FILE="/backups/tapps-brain/private_20260410_020000.pgdump"

# 1. Delete the target project's rows
psql "$TAPPS_BRAIN_DATABASE_URL" \
  -c "DELETE FROM private_memories WHERE project_id = '$PROJECT_ID';"

# 2. Restore only that project from backup (schema must exist)
pg_restore \
  --dbname="$TAPPS_BRAIN_DATABASE_URL" \
  --data-only \
  --table=private_memories \
  "$BACKUP_FILE" \
  | psql "$TAPPS_BRAIN_DATABASE_URL"
# Note: pg_restore will replay all rows; duplicates for other projects
# will conflict on the primary key and be skipped. Use --on-conflict-do-nothing
# if your pg_restore version supports it, or restore to a staging DB first
# and INSERT SELECT the target rows.
```

---

## Runbook 3 — pgBackRest backup

```bash
# Full backup (run weekly)
pgbackrest --stanza=tapps-brain --type=full backup

# Differential backup (run daily)
pgbackrest --stanza=tapps-brain --type=diff backup

# Check backup integrity
pgbackrest --stanza=tapps-brain check

# List available backups
pgbackrest --stanza=tapps-brain info
```

---

## Runbook 4 — Point-in-time recovery (pgBackRest)

```bash
TARGET_TIME="2026-04-10 14:30:00 UTC"

# 1. Stop the running Postgres
pg_ctlcluster 17 main stop

# 2. Restore (wipes PGDATA and replaces it)
pgbackrest --stanza=tapps-brain restore \
  --type=time \
  --target="$TARGET_TIME" \
  --target-action=promote \
  --delta

# 3. Start Postgres
pg_ctlcluster 17 main start

# 4. Verify recovery completed
psql "$TAPPS_BRAIN_DATABASE_URL" -c "SELECT pg_is_in_recovery();"
# Expected: f  (false = promoted, not in recovery)

# 5. Spot-check row counts
psql "$TAPPS_BRAIN_DATABASE_URL" \
  -c "SELECT COUNT(*) AS memories FROM private_memories;"
```

---

## Runbook 5 — Hive replica failover

```bash
# 1. Confirm primary is unreachable
psql "$TAPPS_BRAIN_HIVE_DSN" -c "SELECT 1;" 2>&1 | grep -i "error\|refused"

# 2. Promote the standby
REPLICA_HOST="replica-host"
ssh "$REPLICA_HOST" "pg_ctlcluster 17 main promote"
# Or: ssh "$REPLICA_HOST" "touch /var/lib/postgresql/17/main/promote.signal"

# 3. Wait for promotion (should be near-instant)
sleep 5
psql "postgres://tapps:SECRET@${REPLICA_HOST}:5432/tapps_hive" \
  -c "SELECT pg_is_in_recovery();"
# Expected: f

# 4. Update application DSN
export TAPPS_BRAIN_HIVE_DSN="postgres://tapps:SECRET@${REPLICA_HOST}:5432/tapps_hive"

# 5. Restart the MCP server or CLI to pick up the new DSN
# (implementation-specific: restart systemd unit, k8s rollout, etc.)

# 6. Verify Hive connectivity
tapps-brain maintenance health --json | grep hive_status
```

---

## Runbook 6 — Schema-only restore (migration replay)

Use this after a catastrophic DDL incident or when provisioning a new database.

```bash
# Private memory (all 5 migrations)
for f in src/tapps_brain/migrations/private/*.sql; do
  psql "$TAPPS_BRAIN_DATABASE_URL" -f "$f"
done

# Hive
tapps-brain maintenance migrate-hive --dsn "$TAPPS_BRAIN_HIVE_DSN"

# Federation
tapps-brain maintenance migrate-federation --dsn "$TAPPS_BRAIN_FEDERATION_DSN"
```

---

## Backup verification checklist

Run monthly (or after any significant backup configuration change):

```
[ ] pg_dump backup restores successfully to a test database
[ ] pgBackRest check passes without errors
[ ] Row counts in test restore match production (within expected delta)
[ ] PITR test: restore to a known timestamp, verify a known row is present
[ ] Replica lag < 30 s (check pg_stat_replication on primary)
[ ] Backup files present for the last 7 days in $BACKUP_DIR
[ ] pgBackRest info shows at least one FULL backup within the last 7 days
```

---

## Monitoring alerts (recommended)

| Condition | Threshold | Action |
|-----------|-----------|--------|
| Last successful pg_dump | > 25 hours | Page on-call |
| pgBackRest last full backup | > 8 days | Page on-call |
| WAL archive lag | > 5 minutes | Alert on-call |
| Replica lag | > 60 seconds | Alert on-call |
| Backup directory disk usage | > 80% | Alert on-call |

---

## Related docs

- [Postgres Backup Guide](../guides/postgres-backup.md) — strategy overview, configuration, and examples
- [Hive Deployment Guide](../guides/hive-deployment.md) — Compose / K8s setup
- [DB Roles Runbook](./db-roles-runbook.md) — least-privilege roles and credentials
- [pg_tde Encryption](../guides/postgres-tde.md) — at-rest encryption
