-- TAP-1816: kg_edges — add confidence_history column.
--
-- Stores the full list of confidence values contributed by each individual
-- extraction that was merged into a deduplicated triple.  Allows downstream
-- auditing of "how many independent memory entries asserted X → Y" rather than
-- collapsing to a single max-confidence scalar.
--
-- Idempotency: ALTER TABLE … ADD COLUMN IF NOT EXISTS is safe to re-run.
-- Default: empty JSON array so existing rows are valid without backfill.

ALTER TABLE kg_edges
    ADD COLUMN IF NOT EXISTS confidence_history JSONB NOT NULL DEFAULT '[]'::jsonb;

-- GIN index for containment queries, e.g.
--   WHERE confidence_history @> '[0.9]'
-- (skip if you never query by individual history values)
CREATE INDEX IF NOT EXISTS idx_kg_edges_confidence_history_gin
    ON kg_edges USING GIN (confidence_history);

-- ---------------------------------------------------------------------------
-- Schema version bump
-- ---------------------------------------------------------------------------

INSERT INTO private_schema_version (version, description)
VALUES (21, 'kg_edges.confidence_history JSONB column for provenance accumulation (TAP-1816)');
