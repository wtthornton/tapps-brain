-- TAP-731: Add failed_approaches column to private_memories.
-- Stores a JSONB array of dead-end investigation notes so future agents
-- don't repeat ruled-out approaches.  Purely additive; existing rows get
-- an empty array via the DEFAULT.

ALTER TABLE private_memories
    ADD COLUMN IF NOT EXISTS failed_approaches JSONB DEFAULT '[]'::jsonb;

INSERT INTO private_schema_version (version, description)
VALUES (14, 'Add failed_approaches JSONB column to private_memories (TAP-731)');
