# Partition Retention — `experience_events`

**Audience:** tapps-brain operators running the deployed `tapps-brain-http` container in production.
**Scope:** the `experience_events` table only. `private_memories`, `kg_*`, and Hive tables are not partitioned and have their own GC paths (`maintenance_gc`, `decay`).

> Filed for [TAP-1974](https://linear.app/tappscodingagents/issue/TAP-1974) under EPIC-302 — referenced (but not previously documented) by the 3.15.0 release notes.

---

## Why a retention policy exists

`experience_events` is **append-only** and **range-partitioned monthly** on `event_time` ([migration 020](../../src/tapps_brain/migrations/private/020_experience_events.sql)). Every workflow event an agent emits — every `brain_record_event` call, every successful `record_events_batch_per_event_tx` row — lands here. With the tapps-mcp EPIC-202 + EPIC-203 work emitting events from every validate / every migration step, projects with heavy event volume can rack up tens of millions of rows per quarter.

Two reasons to drop old partitions instead of letting them grow:

1. **Storage cost.** Each partition carries its own indexes (BRIN, btree, GIN). At 1M events/month, the indexes alone outweigh the rows after ~6 months.
2. **Plan-time pruning.** PG 17's partition pruner is fast, but it still has to look at every partition's range constraint. Beyond ~24 partitions the planner overhead becomes measurable on hot-path `brain_get_neighbors` queries.

The default is **12 months** — enough to cover quarter-over-quarter trend reports and EWMA diagnostics windows, short enough to keep storage bounded.

---

## What the bundled migration ships

[Migration 020](../../src/tapps_brain/migrations/private/020_experience_events.sql) pre-creates **12 monthly partitions** starting `2026-05`, plus a `DEFAULT` partition that catches anything outside the pre-created range. **No retention job runs automatically** — operators decide when and how to prune.

This means:

- **Up to 2027-04-30:** no operator action required. Events land in the pre-created partitions.
- **After 2027-05-01:** new events fall into the `DEFAULT` partition. The `DEFAULT` partition still works, but query performance degrades because it isn't bounded — every `event_time` query has to scan it linearly. Operators **must** either pre-create future partitions or install `pg_partman` (see below) before the pre-created window expires.

---

## Configuring retention

### Environment variable

```bash
# How many months of history to retain. Default 12; minimum 1; max 60.
# Honored only by the optional pg_partman migration (see below). Setting
# this without applying the migration is a no-op.
TAPPS_BRAIN_EVENTS_RETENTION_MONTHS=12
```

### Per-deployment `.brain.yaml` (advanced)

If you front the brain with a config file rather than env vars, set:

```yaml
retention:
  experience_events_months: 12
```

> Not yet wired into the brain startup — tracked separately; for now the env var is authoritative.

---

## Operator options

There are three operationally-sane shapes. Pick one — don't combine.

### Option A — bundled `pg_partman` migration (recommended)

ⓘ **Opt-in.** Requires the [pg_partman](https://github.com/pgpartman/pg_partman) extension already available on the Postgres cluster. Cloud-hosted PG (RDS, Cloud SQL, Supabase) typically ship `pg_partman` 5.x; check `SELECT extversion FROM pg_extension WHERE extname = 'pg_partman'`.

Apply:

```bash
tapps-brain-migrate apply --include-optional partman_experience_events
```

This invokes [migration 022](../../src/tapps_brain/migrations/private/022_partman_experience_events.sql) which:

- Creates the `partman` schema if missing.
- Registers `public.experience_events` with `part_config`, configured for monthly RANGE partitions.
- Sets `retention = '<TAPPS_BRAIN_EVENTS_RETENTION_MONTHS> months'` (default `12 months`).
- Sets `retention_keep_table = false` so old partitions are dropped, not detached.
- Sets `premake = 4` so partitions are pre-created 4 months ahead.
- Schedules `partman.run_maintenance()` via the operator's preferred mechanism (no built-in scheduler; see "Scheduling" below).

The migration is **idempotent** and **skips silently** when the `pg_partman` extension is not installed — so it's safe to apply on every deploy even when only some environments have the extension.

Rollback: `022_partman_experience_events.down.sql` un-registers the table from `part_config` (`partman.undo_partition`) but does **not** drop existing partitions — operators decide whether to drop them by hand.

### Option B — manual monthly pruning

If you can't install `pg_partman`, schedule the following query monthly (cron, GitHub Actions, whatever you use):

```sql
-- Run as the brain DB owner. Drops partitions older than 12 months.
DO $$
DECLARE
    cutoff date := (now() - interval '12 months')::date;
    partition_name text;
BEGIN
    FOR partition_name IN
        SELECT inhrelid::regclass::text
        FROM pg_inherits
        WHERE inhparent = 'experience_events'::regclass
    LOOP
        -- partition naming: experience_events_yYYYYmMM
        IF partition_name ~ '_y[0-9]{4}m[0-9]{2}$' THEN
            DECLARE
                y int := substring(partition_name from '_y(\d{4})m')::int;
                m int := substring(partition_name from 'm(\d{2})$')::int;
                ptime date := make_date(y, m, 1);
            BEGIN
                IF ptime < cutoff THEN
                    EXECUTE format('DROP TABLE %I', partition_name);
                    RAISE NOTICE 'dropped %', partition_name;
                END IF;
            END;
        END IF;
    END LOOP;
END $$;
```

Pre-create future partitions the same way — the `experience_events_default` partition catches anything that falls through, so missing a month doesn't break writes, but query performance degrades.

### Option C — no retention (development only)

For local development and integration test clusters, do nothing. The pre-created window covers 12 months from migration apply; events written outside that window land in the `DEFAULT` partition. Storage grows unbounded — fine for an empty test DB, not fine for prod.

---

## Scheduling `partman.run_maintenance()`

`pg_partman` does not run its own jobs — it expects to be invoked from outside the database. Pick the deployment-native mechanism:

| Environment | Recommended scheduler |
|---|---|
| Docker Compose (this repo's `docker/docker-compose.hive.yaml`) | A sidecar `cron` container running `psql -c "CALL partman.run_maintenance_proc()"` daily |
| Kubernetes | a `CronJob` with the same SQL |
| Managed PG (Supabase, Neon) | the platform's built-in scheduled jobs feature |
| Bare-metal | `pg_cron` if available, otherwise OS-level `cron` calling `psql` |

Frequency: **once per day** is the documented `pg_partman` default and works for monthly partitions. Running more often is harmless; less often risks the `DEFAULT` partition catching writes if `premake` runs out.

---

## Verifying the policy

After applying the optional migration:

```sql
-- Confirm the table is registered.
SELECT parent_table, premake, retention, retention_keep_table
FROM partman.part_config
WHERE parent_table = 'public.experience_events';

-- List current partitions oldest-first.
SELECT inhrelid::regclass::text AS partition, pg_size_pretty(pg_relation_size(inhrelid))
FROM pg_inherits
WHERE inhparent = 'experience_events'::regclass
ORDER BY 1;
```

After `partman.run_maintenance_proc()` has run at least once:

```sql
-- Partitions newer than `now() - retention` should be present.
-- Partitions older than `now() - retention` should be gone.
```

---

## Restoring dropped data

Dropped partitions are **unrecoverable** from the running database. If you need historical events back:

1. Restore from the most recent base backup.
2. `pg_dump` the target month's partition.
3. Attach it back: `ALTER TABLE experience_events ATTACH PARTITION ... FOR VALUES FROM (...) TO (...);`

Note: re-attached old partitions will be re-dropped on the next `run_maintenance_proc()` cycle unless you bump `retention` first.

---

## See also

- [Migration 020 — partitioned table DDL](../../src/tapps_brain/migrations/private/020_experience_events.sql)
- [Migration 022 — optional `pg_partman` registration](../../src/tapps_brain/migrations/private/022_partman_experience_events.sql)
- [experience-events.md](experience-events.md) — payload schema, `event_type` catalogue
- [ADR-007 — Postgres-only persistence](../../docs/adrs/) for the reasoning behind the no-SQLite fallback
- [TAP-1818](https://linear.app/tappscodingagents/issue/TAP-1818) — migration rollback contract every `*.up.sql` must satisfy
