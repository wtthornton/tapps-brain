# ADR-012: Evidence is required for KG edges (EPIC-074)

## Status

Accepted (2026-05-07)

## Context

Early knowledge graph designs in tapps-brain stored edges as bare `(subject, predicate, object)`
triples — the same as the regex-extracted `private_relations` triples. Bare triples carry no
provenance: an agent cannot tell whether an edge came from a reliable source, a stale inference,
or a hallucination. Without provenance, contradiction detection and confidence propagation are
impossible to implement correctly.

The 2026-05-07 architecture review identified three failure modes that bare triples cause:

1. **Silent contradiction** — two agents write opposing edges; both persist; neither is flagged.
2. **Stale inference decay** — without source tracking, there is no way to invalidate edges when
   the memory that supported them is garbage-collected or superseded.
3. **Security audit gap** — no record of which agent wrote which edge or why; RLS cannot protect
   against agent-to-agent edge injection.

## Decision

Every row in `kg_edges` must be supported by at least one row in `kg_evidence`.

1. `kg_evidence` has a `NOT NULL` FK to either `kg_edges.id` or `kg_entities.id` (XOR CHECK
   constraint). An evidence row with neither FK is rejected.
2. Application code inserting a new edge must insert at least one evidence row in the same
   transaction, or the insert is rejected by the FK constraint.
3. `kg_evidence` captures: `source_type` (memory/hive/external), `source_id`, `source_uri`,
   `source_hash`, `quote`, `source_agent`, `confidence`, `utility_score`.
4. Evidence rows have their own lifecycle fields (confidence, status, valid_at/invalid_at) so
   evidence can be rejected or superseded without deleting the edge history.
5. The evidence-required rule is enforced at the database layer (FK + CHECK), not only in
   application code.

## Consequences

- **Insert verbosity** — every edge write requires a parallel evidence insert. Application code
  (EPIC-075) must expose a `KnowledgeGraphStore.add_edge(edge, evidence)` API that writes both
  atomically.
- **Contradiction detection** — possible because all edges are traceable to a source. A conflict
  resolver can compare source hashes and confidence scores.
- **Orphan-evidence rejection** — `kg_evidence` rows without a matching edge or entity FK are
  rejected at insert time; no orphan cleanup job needed.
- **Audit trail** — `source_agent` on every evidence row satisfies the security audit requirement
  that agent writes are attributable.

## Supersedes / updates

- Extends ADR-011 (KG schema in Postgres) with the evidence FK contract.
- Cited by: EPIC-075 (KnowledgeGraphStore.add_edge must write evidence atomically).
