# ADR-011: First-Class Knowledge Graph Schema in PostgreSQL (EPIC-074)

## Status

Accepted (2026-05-06)

## Context

tapps-brain's relation layer was purely regex-based: `src/tapps_brain/relations.py`
extracted triples into `private_relations` (7 hard-coded predicates, `MAX_RELATIONS_PER_ENTRY=5`,
no lifecycle, no evidence, no contradiction, no decay). This created a mismatch between
the richly lifecycle-managed `private_memories` layer and a relation layer that treated
edges as disposable parse artifacts.

The 2026-05-06 architecture review identified three root causes of the mismatch:
1. **No evidence model** — relations were inferred, not evidence-backed; confidence was arbitrary.
2. **No lifecycle** — edges couldn't be reinforced, superseded, or decayed alongside memories.
3. **No tenant isolation** — `private_relations` had no RLS, making it a potential
   cross-tenant data leak vector.

ADR-007 commits the project to PostgreSQL-only persistence. EPIC-058 / EPIC-066
standardised on `pgvector/pgvector:pg17` plus RLS (migrations 011/012). Adding KG tables
without RLS would have regressed the security posture established there.

## Decision

Introduce five new PostgreSQL tables under `src/tapps_brain/migrations/private/`
(migrations 016–020) that bring graph relationships up to the same lifecycle and
security standard as `private_memories`:

| Migration | Table | Purpose |
|-----------|-------|---------|
| 016 | `kg_entities` | Named entities with RLS, lifecycle, STORED casefold column |
| 017 | `kg_edges` | Evidence-backed directed edges with partial unique constraint |
| 018 | `kg_evidence` | First-class evidence attached to edges or entities |
| 019 | `kg_aliases` | Weighted aliases with confidence + status for merge control |
| 020 | `experience_events` | Range-partitioned event log for workflow step capture |

### Key design choices

1. **RLS on every table** — `ENABLE ROW LEVEL SECURITY; FORCE ROW LEVEL SECURITY`
   with a fail-closed `tenant_id` USING policy matching migration 012's pattern.
   The table owner cannot accidentally bypass isolation.

2. **`gen_random_uuid()` + STORED generated columns only** — no `uuidv7()`,
   no VIRTUAL generated columns, no `casefold()`. PG17-safe DDL throughout.

3. **Evidence is required for edges** — edges carry an `evidence_count` and a NOT NULL
   `first_evidence_id` FK to `kg_evidence`; the application layer enforces
   "no evidence, no edge" (see ADR-012).

4. **Lifecycle mirrors `MemoryEntry`** — every entity and edge carries `confidence`,
   `status`, `valid_at`, `invalid_at`, `superseded_by`, `stability`, `difficulty`,
   `reinforce_count`, `last_reinforced`, `contradicted`, `positive_feedback_count`,
   `negative_feedback_count`, and `temporal_sensitivity`. This allows the existing
   decay, reinforcement, and feedback machinery to operate on graph facts
   with no special-casing (see ADR-013).

5. **Partial unique constraint on edges** —
   `UNIQUE (brain_id, subject_entity_id, predicate, object_entity_id)
   WHERE status='active' AND invalid_at IS NULL` — so historical or superseded
   edges coexist with the active one without violating uniqueness.

6. **`experience_events` is range-partitioned monthly** — RANGE on `event_time`,
   12 pre-created monthly partitions, default partition for overflow, BRIN index
   on `event_time`. Producers emit one structured event per workflow step instead
   of orchestrating four separate writes.

7. **`brain_id` is the entity uniqueness scope** — entities are unique per
   `(brain_id, entity_type, canonical_name_norm)`. `brain_id` is a logical brain
   identity (project + agent composite, or a dedicated UUID), distinct from
   `tenant_id` (the RLS partition key) and `project_id` (cross-brain project scope).

## Consequences

- **Postgres schema grows** by five tables and ~25 indexes. The migration runner
  picks them up automatically via file discovery; no registration code changes needed.
- **`private_relations` stays as-is** — legacy table untouched. No data migration.
- **EPIC-075** can now implement `PostgresKnowledgeGraphStore` against this schema.
- **EPIC-076** can extend recall to include graph neighbourhood results.
- **CI** must exercise `TAPPS_BRAIN_AUTO_MIGRATE=1` against pgvector/pg17 for the
  new migrations; the downgrade guard rejects DB versions exceeding the bundled max.

## Alternatives considered

- **Extend `private_memories` with a relation type** — rejected; the `(project_id, agent_id, key)`
  PK and free-form `value` text column are a poor fit for structured graph edges. It would
  have required a second table for edge endpoints anyway.
- **Separate graph database (Neo4j / AGE)** — rejected; ADR-007 requires Postgres-only.
  A second engine would split the backup, encryption, and RLS story. `pg_age`
  (Apache AGE) is immature on PG17.
- **Keep regex relations as-is** — rejected; no lifecycle means no decay, no feedback loop,
  no contradiction — the feature is essentially dead code with no path to improvement.

## Refs

- `src/tapps_brain/migrations/private/009_project_rls.sql` — RLS pattern reference
- `src/tapps_brain/migrations/private/012_rls_force.sql` — FORCE RLS pattern
- `src/tapps_brain/migrations/private/016_kg_entities.sql` — first migration in the set
- `docs/planning/adr/ADR-007-postgres-only-no-sqlite.md`
- TAP-1485 (EPIC-074), TAP-1486 (EPIC-075), TAP-1487 (EPIC-076)
