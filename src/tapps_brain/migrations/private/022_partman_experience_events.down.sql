-- Undo migration 022: un-register experience_events from pg_partman.
--
-- Symmetric to the up migration: extension-presence guarded so the down step
-- succeeds cleanly on clusters that never had pg_partman.  Existing
-- partitions are NOT dropped — operators decide whether to drop them by
-- hand, per docs/engineering/partition-retention.md.

DO $$
DECLARE
    _has_partman boolean;
    _registered boolean;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM pg_extension WHERE extname = 'pg_partman'
    ) INTO _has_partman;

    IF NOT _has_partman THEN
        RAISE NOTICE 'pg_partman not installed; nothing to un-register.';
        RETURN;
    END IF;

    SELECT EXISTS (
        SELECT 1
        FROM partman.part_config
        WHERE parent_table = 'public.experience_events'
    ) INTO _registered;

    IF NOT _registered THEN
        RAISE NOTICE 'experience_events not registered with pg_partman; nothing to do.';
        RETURN;
    END IF;

    -- undo_partition removes the part_config row; partitions stay attached.
    -- p_keep_table => true preserves the existing partition tables so a later
    -- re-apply of 022 picks them back up cleanly.
    PERFORM partman.undo_partition(
        p_parent_table => 'public.experience_events',
        p_keep_table   => true
    );

    RAISE NOTICE
        'Un-registered experience_events from pg_partman.  Partition tables '
        'preserved — drop by hand if you want to reclaim storage.';
END $$;

-- ---------------------------------------------------------------------------
-- Schema version row removal (always runs)
-- ---------------------------------------------------------------------------

DELETE FROM private_schema_version WHERE version = 22;
