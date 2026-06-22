-- Undo migration 025: restore brain-scoped kg_entities uniqueness.
--
-- WARNING: fails if multiple tenants now share (brain_id, entity_type, name).

DROP INDEX IF EXISTS idx_kg_entities_active;
CREATE INDEX IF NOT EXISTS idx_kg_entities_active
    ON kg_entities (brain_id, entity_type, canonical_name_norm)
    WHERE status = 'active';

DROP INDEX IF EXISTS idx_kg_entities_tenant_brain_type_name;
CREATE INDEX IF NOT EXISTS idx_kg_entities_brain_type_name
    ON kg_entities (brain_id, entity_type, canonical_name_norm);

ALTER TABLE kg_entities
    DROP CONSTRAINT IF EXISTS kg_entities_tenant_brain_type_name_key;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'kg_entities'::regclass
          AND conname = 'kg_entities_brain_id_entity_type_canonical_name_norm_key'
    ) THEN
        ALTER TABLE kg_entities
            ADD CONSTRAINT kg_entities_brain_id_entity_type_canonical_name_norm_key
            UNIQUE (brain_id, entity_type, canonical_name_norm);
    END IF;
END $$;

DELETE FROM private_schema_version WHERE version = 25;
