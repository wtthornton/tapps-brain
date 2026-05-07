# ADR-013: KG tables inherit the MemoryEntry lifecycle (EPIC-074)

## Status

Accepted (2026-05-07)

## Context

`MemoryEntry` (defined in `src/tapps_brain/models.py`) carries a rich lifecycle:
`confidence`, `status` (active/stale/superseded/archived), `last_reinforced`,
`reinforce_count`, `stability`, `difficulty`, `temporal_sensitivity`,
`valid_at` / `invalid_at`, `superseded_by`, and `contradicted`.

These fields encode the epistemics of each memory: how confident the system is, how
recently the memory was reinforced, whether it has been superseded by a newer fact, and
whether it contradicts another entry. The decay, consolidation, GC, and flywheel subsystems
read these fields.

Defining a separate lifecycle for KG entities, edges, and evidence would create two
epistemics engines, two decay formulas, and two GC strategies — operational complexity
with no benefit.

## Decision

Every KG table (`kg_entities`, `kg_edges`, `kg_evidence`, `kg_aliases`) carries the
same lifecycle field set as `private_memories`:

| Field | Type | Mirrors |
|-------|------|---------|
| `confidence` | REAL | `MemoryEntry.confidence` |
| `status` | TEXT CHECK(active/stale/superseded/archived/rejected) | `MemoryEntry.status` |
| `last_reinforced` | TIMESTAMPTZ | `MemoryEntry.last_reinforced` |
| `reinforce_count` | INTEGER | `MemoryEntry.reinforce_count` |
| `stability` | REAL | `MemoryEntry.stability` |
| `difficulty` | REAL | `MemoryEntry.difficulty` |
| `temporal_sensitivity` | REAL | `MemoryEntry.temporal_sensitivity` |
| `valid_at` | TIMESTAMPTZ | `MemoryEntry.valid_at` |
| `invalid_at` | TIMESTAMPTZ | `MemoryEntry.invalid_at` |
| `superseded_by` | UUID | `MemoryEntry.superseded_by` |
| `contradicted` | BOOLEAN | `MemoryEntry.contradicted` |

The `status` column uses the same allowed values as `MemoryStatus`; the CHECK constraint
is defined inline to avoid a circular migration dependency.

Consequences:

1. **Unified decay** — the existing `decay.py` exponential-decay formula can be applied to
   KG rows with the same half-life configuration as memory entries.
2. **Unified GC** — `gc.py` can archive stale/superseded KG rows without a separate code path.
3. **Unified consolidation** — when two entities are merged (alias resolution), `superseded_by`
   carries the canonical entity's ID; the old row is not deleted.
4. **Schema consistency** — Python dataclasses in EPIC-075 can share field definitions with
   `MemoryEntry` via inheritance or composition.
5. **Migration footprint** — each KG migration is longer due to the lifecycle block, but the
   pattern is mechanical and reviewable; no bespoke column inventions.

## Non-goals

- KG lifecycle DOES NOT reuse `MemoryTier` (architectural/pattern/procedural/context) — tier
  is a storage-hint for memory retrieval ranking that does not apply to graph structure.
- `experience_events` is exempt from most lifecycle fields (it is an append-only event log);
  it carries `brain_id`, `tenant_id`, `agent_id`, `event_type`, and `event_time` only.

## Supersedes / updates

- Extends ADR-011 (KG schema in Postgres) and ADR-012 (evidence-required edges).
- Cited by: EPIC-075 (KnowledgeGraphStore), EPIC-076 (recall integration with decay).
