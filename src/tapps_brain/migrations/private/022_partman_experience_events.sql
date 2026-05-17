-- TAP-1974 (EPIC-302 story 302.2):
-- Optionally register `experience_events` with pg_partman so monthly
-- partitions are auto-created + old partitions auto-dropped per the
-- retention policy documented in docs/engineering/partition-retention.md.
--
-- ============================================================================
-- DESIGN: idempotent + opt-in via extension presence
-- ============================================================================
-- The brain's migration runner has no concept of "optional" migrations — it
-- applies every NNN_*.sql in src/tapps_brain/migrations/private/ in order.
-- Two operational constraints force the design below:
--
--   1. Many production clusters (and almost every CI / test cluster) do NOT
--      have the pg_partman extension installed.  This migration MUST succeed
--      on those clusters or it blocks every other migration that ships after.
--   2. Re-runs MUST be idempotent — pg_partman.create_parent() raises when
--      the parent is already registered, so we guard with a part_config
--      lookup first.
--
-- The migration therefore wraps the registration in a DO $$ ... $$ block
-- that:
--   * Detects whether the pg_partman extension is installed.
--   * If NOT installed → RAISE NOTICE and exit cleanly (the version row is
--     still inserted so the migration is recorded as applied; the down
--     migration is the operator's escape hatch).
--   * If installed → register the parent ONLY when not already registered,
--     reading the operator-overridable retention window from the
--     `app.events_retention_months` GUC (default 12).  Operators set this
--     once via ALTER DATABASE / postgresql.conf to match the
--     TAPPS_BRAIN_EVENTS_RETENTION_MONTHS env var documented in the
--     retention guide.
--
-- The `partman.run_maintenance_proc()` scheduler is NOT installed by this
-- migration — see docs/engineering/partition-retention.md "Scheduling" for
-- the per-environment options (cron sidecar, K8s CronJob, pg_cron, etc.).

-- ---------------------------------------------------------------------------
-- pg_partman registration (idempotent + opt-in)
-- ---------------------------------------------------------------------------

DO $$
DECLARE
    _has_partman boolean;
    _already_registered boolean;
    _retention_months integer;
    _retention_text text;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM pg_extension WHERE extname = 'pg_partman'
    ) INTO _has_partman;

    IF NOT _has_partman THEN
        RAISE NOTICE
            'pg_partman extension not installed; skipping experience_events '
            'auto-partition registration. See docs/engineering/partition-retention.md '
            'for manual pruning options.';
        RETURN;
    END IF;

    SELECT EXISTS (
        SELECT 1
        FROM partman.part_config
        WHERE parent_table = 'public.experience_events'
    ) INTO _already_registered;

    -- Operator-overridable retention window.  Set per-database with:
    --   ALTER DATABASE tapps_brain SET app.events_retention_months = '24';
    -- Default 12 months matches docs/engineering/partition-retention.md.
    _retention_months := COALESCE(
        NULLIF(current_setting('app.events_retention_months', TRUE), '')::integer,
        12
    );
    -- Clamp to the [1, 60] range documented in the retention guide.
    IF _retention_months < 1 THEN _retention_months := 1; END IF;
    IF _retention_months > 60 THEN _retention_months := 60; END IF;
    _retention_text := _retention_months::text || ' months';

    IF _already_registered THEN
        -- Already registered: keep configuration current (operator may have
        -- bumped the GUC since the last apply).
        UPDATE partman.part_config
        SET retention            = _retention_text,
            retention_keep_table = false,
            premake              = 4
        WHERE parent_table = 'public.experience_events';

        RAISE NOTICE
            'experience_events already registered with pg_partman; updated '
            'retention=% premake=4.', _retention_text;
        RETURN;
    END IF;

    -- First-time registration.  RANGE-partitioned monthly already done in
    -- migration 020; pg_partman just takes over future create + drop.
    PERFORM partman.create_parent(
        p_parent_table       => 'public.experience_events',
        p_control            => 'event_time',
        p_type               => 'range',
        p_interval           => '1 month',
        p_premake            => 4,
        p_start_partition    => to_char(date_trunc('month', now()), 'YYYY-MM-DD')
    );

    UPDATE partman.part_config
    SET retention            = _retention_text,
        retention_keep_table = false
    WHERE parent_table = 'public.experience_events';

    RAISE NOTICE
        'Registered experience_events with pg_partman: monthly partitions, '
        'retention=%, premake=4, retention_keep_table=false.', _retention_text;
END $$;

-- ---------------------------------------------------------------------------
-- Schema version bump (always runs, even when pg_partman is absent)
-- ---------------------------------------------------------------------------

INSERT INTO private_schema_version (version, description)
VALUES (22, 'experience_events pg_partman registration (TAP-1974, opt-in / idempotent)');
