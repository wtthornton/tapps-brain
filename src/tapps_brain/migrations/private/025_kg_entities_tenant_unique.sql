-- TAP-4274: Scope kg_entities uniqueness by tenant_id.
--
-- The original UNIQUE (brain_id, entity_type, canonical_name_norm) omitted
-- tenant_id.  brain_id is often a shared constant (e.g. 'tapps-brain'), so
-- cross-tenant ON CONFLICT DO UPDATE hit another tenant's row and failed the
-- RLS USING policy with InsufficientPrivilege.
--
-- Forward fix:
--   1. Drop the legacy unique constraint.
--   2. Add UNIQUE (tenant_id, brain_id, entity_type, canonical_name_norm).
--   3. Refresh lookup indexes to lead with tenant_id.
--
-- Idempotency: DROP IF EXISTS + DO block for ADD CONSTRAINT.

-- ---------------------------------------------------------------------------
-- Defensive dedupe (same tenant only — should be a no-op on healthy DBs)
-- ---------------------------------------------------------------------------
-- Keep the row with highest confidence; re-point kg_edges / kg_evidence FKs
-- from losers to the keeper before deleting duplicates.

DO $$
DECLARE
    dup RECORD;
    keeper_id UUID;
BEGIN
    FOR dup IN
        SELECT tenant_id, brain_id, entity_type, canonical_name_norm,
               array_agg(id ORDER BY confidence DESC, updated_at DESC) AS ids
        FROM kg_entities
        GROUP BY tenant_id, brain_id, entity_type, canonical_name_norm
        HAVING COUNT(*) > 1
    LOOP
        keeper_id := dup.ids[1];
        -- Before repointing, supersede loser active edges that would collide
        -- with an existing active keeper triple (uix_kg_edges_active_triple).
        UPDATE kg_edges e_loser
        SET status = 'superseded',
            invalid_at = COALESCE(e_loser.invalid_at, now())
        WHERE e_loser.status = 'active'
          AND e_loser.invalid_at IS NULL
          AND e_loser.subject_entity_id = ANY(dup.ids[2:array_length(dup.ids, 1)])
          AND EXISTS (
              SELECT 1 FROM kg_edges e_keep
              WHERE e_keep.brain_id = e_loser.brain_id
                AND e_keep.subject_entity_id = keeper_id
                AND e_keep.predicate = e_loser.predicate
                AND e_keep.object_entity_id = e_loser.object_entity_id
                AND e_keep.status = 'active'
                AND e_keep.invalid_at IS NULL
          );
        UPDATE kg_edges e_loser
        SET status = 'superseded',
            invalid_at = COALESCE(e_loser.invalid_at, now())
        WHERE e_loser.status = 'active'
          AND e_loser.invalid_at IS NULL
          AND e_loser.object_entity_id = ANY(dup.ids[2:array_length(dup.ids, 1)])
          AND EXISTS (
              SELECT 1 FROM kg_edges e_keep
              WHERE e_keep.brain_id = e_loser.brain_id
                AND e_keep.subject_entity_id = e_loser.subject_entity_id
                AND e_keep.predicate = e_loser.predicate
                AND e_keep.object_entity_id = keeper_id
                AND e_keep.status = 'active'
                AND e_keep.invalid_at IS NULL
          );
        -- Repoint remaining edges referencing duplicate entity IDs.
        UPDATE kg_edges
        SET subject_entity_id = keeper_id
        WHERE subject_entity_id = ANY(dup.ids[2:array_length(dup.ids, 1)]);
        UPDATE kg_edges
        SET object_entity_id = keeper_id
        WHERE object_entity_id = ANY(dup.ids[2:array_length(dup.ids, 1)]);
        -- Repoint evidence referencing duplicate entity IDs.
        UPDATE kg_evidence
        SET entity_id = keeper_id
        WHERE entity_id = ANY(dup.ids[2:array_length(dup.ids, 1)]);
        DELETE FROM kg_entities
        WHERE id = ANY(dup.ids[2:array_length(dup.ids, 1)]);
    END LOOP;
END $$;

-- ---------------------------------------------------------------------------
-- Replace uniqueness constraint
-- ---------------------------------------------------------------------------

ALTER TABLE kg_entities
    DROP CONSTRAINT IF EXISTS kg_entities_brain_id_entity_type_canonical_name_norm_key;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'kg_entities'::regclass
          AND conname = 'kg_entities_tenant_brain_type_name_key'
    ) THEN
        ALTER TABLE kg_entities
            ADD CONSTRAINT kg_entities_tenant_brain_type_name_key
            UNIQUE (tenant_id, brain_id, entity_type, canonical_name_norm);
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- Indexes — tenant-scoped lookups
-- ---------------------------------------------------------------------------

DROP INDEX IF EXISTS idx_kg_entities_brain_type_name;
CREATE INDEX IF NOT EXISTS idx_kg_entities_tenant_brain_type_name
    ON kg_entities (tenant_id, brain_id, entity_type, canonical_name_norm);

DROP INDEX IF EXISTS idx_kg_entities_active;
CREATE INDEX IF NOT EXISTS idx_kg_entities_active
    ON kg_entities (tenant_id, brain_id, entity_type, canonical_name_norm)
    WHERE status = 'active';

-- ---------------------------------------------------------------------------
-- Schema version bump
-- ---------------------------------------------------------------------------

INSERT INTO private_schema_version (version, description)
VALUES (
    25,
    'kg_entities UNIQUE includes tenant_id — TAP-4274 cross-tenant upsert fix'
);
