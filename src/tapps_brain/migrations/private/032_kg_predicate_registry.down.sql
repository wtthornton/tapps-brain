-- Revert migration 032 (kg_predicate_registry): drop the predicate registry.
--
-- Declarations are lost, not archived: without the table there is nowhere to
-- keep them. That is the correct fail-safe direction — TAP-5510's enforcement
-- reads this registry, so a project whose declarations vanish falls back to the
-- permissive behaviour that existed before 032 rather than to a stricter one it
-- can no longer inspect.
--
-- kg_edges is untouched: no edge ever depended on a registry row (there is no
-- FK from kg_edges.predicate to kg_predicates.predicate, deliberately — the
-- registry describes predicates, it does not own them).

DROP INDEX IF EXISTS idx_kg_predicates_lookup;
DROP INDEX IF EXISTS uix_kg_predicates_brain_predicate;

DROP POLICY IF EXISTS kg_predicates_tenant_isolation ON kg_predicates;

DROP TABLE IF EXISTS kg_predicates;

DELETE FROM private_schema_version WHERE version = 32;
