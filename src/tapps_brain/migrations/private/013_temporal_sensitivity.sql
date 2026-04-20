-- TAP-735: Add temporal_sensitivity column to private_memories.
-- Allows callers to set per-entry decay velocity without changing the tier
-- classification.  NULL means no change (backward-compatible default).

ALTER TABLE private_memories
    ADD COLUMN IF NOT EXISTS temporal_sensitivity VARCHAR(6) DEFAULT NULL
        CHECK (temporal_sensitivity IN ('high', 'medium', 'low'));

INSERT INTO private_schema_version (version, description)
VALUES (13, 'Add temporal_sensitivity column to private_memories (TAP-735)');
