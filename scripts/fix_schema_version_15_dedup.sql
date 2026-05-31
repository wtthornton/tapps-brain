-- fix_schema_version_15_dedup.sql — consolidate duplicate v15 rows (TAP-2679).
--
-- Two migration files shipped as 015_* (TAP-732 status/stale columns and
-- TAP-733 memory_class), so `private_schema_version` recorded TWO rows at
-- version 15. Both migrations are applied; the duplicate is cosmetic, but any
-- tool that GROUP BYs or joins on `version` double-counts.
--
-- Safe because the migration loader keys "applied" on the version-number SET
-- (postgres_migrations._get_schema_status builds `applied_set` from the version
-- column), so v15 stays applied after we collapse the two rows into one.
--
-- Idempotent: the UPDATE re-asserts the same merged description, and the DELETE
-- targets a description that no longer exists after the first run (0 rows).
-- Run once against the brain DB:  psql "$TAPPS_BRAIN_DATABASE_URL" -v ON_ERROR_STOP=1 -f scripts/fix_schema_version_15_dedup.sql

BEGIN;

-- Keep the first v15 row (TAP-732) and fold the TAP-733 note into it.
UPDATE private_schema_version
SET description =
    'Add status/stale_reason/stale_date lifecycle columns (TAP-732) + '
    'memory_class column/index (TAP-733) — two 015_* migrations consolidated (TAP-2679)'
WHERE version = 15
  AND description = 'Add status/stale_reason/stale_date lifecycle columns to private_memories (TAP-732)';

-- Drop the duplicate v15 row (its migration is already applied).
DELETE FROM private_schema_version
WHERE version = 15
  AND description = 'Add memory_class column + index to private_memories (TAP-733)';

COMMIT;
