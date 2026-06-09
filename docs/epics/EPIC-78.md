# Epic 78: AgentForge REST DX — experience edge keys and KG resolve

<!-- docsmcp:start:metadata -->
**Status:** In Progress
**Priority:** P1 - High
**Estimated LOE:** ~3-5 days (1 developer)
**Dependencies:** EPIC-076 (experience_events), v3.22.4 resilient writes (TAP-2865/2866/2868), TAP-2725 /v1/kg/resolve_entity
**Blocks:** AgentForge BrainBridge can simplify task-completion KG writes after ship
**Linear:** TAP-3247

<!-- docsmcp:end:metadata -->

---

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

We are doing this so that AgentForge and other HTTP-only brain consumers can record task-completion experience events in a single POST /v1/experience round-trip without pre-resolving entity UUIDs, and can resolve entity keys without misusing /v1/kg/neighbors.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:goal -->
## Goal

Close the EntitySpec/EdgeSpec DX asymmetry exposed during AgentForge 4.37.0 integration with tapps-brain-http 3.24.0: same-transaction edge key resolution (subject_key/object_key), batch REST entity resolve, and consumer contract docs for remember tiers and experience wire format.

<!-- docsmcp:end:goal -->

<!-- docsmcp:start:motivation -->
## Motivation

AgentForge BrainBridge hit contract drift on /v1/remember (invalid_tier cache), /v1/kg/neighbors (non-UUID entity_ids), and /v1/experience (EdgeSpec UUIDs + EvidenceSpec XOR). AF patched locally with two-phase writes and neighbors piggyback on entity_refs. EntitySpec already accepts key/type shorthand (TAP-2675); EdgeSpec still requires pre-resolved UUIDs despite entities upserting in the same transaction. No brain regressions were found — the gaps are API/DX.

<!-- docsmcp:end:motivation -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [x] Single POST /v1/experience with AF-native entities[{key,type}] and edges[{subject_key,object_key,predicate}] creates entities+edges without warnings
- [x] POST /v1/kg/resolve_entities batch endpoint returns ordered results keyed by entity_refs input
- [x] agentforge-integration.md documents remember tiers (no cache tier), experience v3.22+ wire format, neighbors entity_refs ordering
- [x] OpenAPI and brain_record_event docs updated for EdgeSpec key shorthands

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:stories -->
## Stories

### 78.1 — experience.py: same-transaction edge key resolution (TAP-3248)

**Points:** 5 | **Status:** Done

EdgeSpec accepts subject_key/object_key and subject_ref/object_ref; ExperienceEventRecorder resolves against same-event entity upserts before edge insert.

### 78.2 — http_adapter.py: POST /v1/kg/resolve_entities batch (TAP-3249)

**Points:** 3 | **Status:** Done

Batch REST endpoint wrapping kg_service.resolve_entity_refs with entity_ids + results arrays.

### 78.3 — agentforge-integration.md: consumer contract docs (TAP-3250)

**Points:** 2 | **Status:** Done

Documents remember tiers, experience wire format, neighbors entity_refs ordering, and cross-links TAP-2865/2866/3196/2725.

<!-- docsmcp:end:stories -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- Edge resolution builds `(entity_type, canonical_name) → uuid` map after entity upsert in `record()` and `record_many()`.
- Unresolved edge keys emit TAP-2866-style warnings (kind `edge`, type `unresolved`); UUID-only payloads unchanged.
- `resolve_entity_refs` now returns `{entity_ids, results}` for batch REST consumers.

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:non-goals -->
## Out of Scope / Future Considerations

- Adding a cache MemoryTier
- Making TAP-2866 warnings fatal again
- Changing neighbors entity_refs convenience (STORY-74.5)
- Brain health ReadTimeouts during container restart
- Evidence entity_key shorthand (optional follow-up, not in scope)

<!-- docsmcp:end:non-goals -->

<!-- docsmcp:start:refs -->
## Refs

TAP-3247, TAP-3248, TAP-3249, TAP-3250, TAP-2675, TAP-2866, TAP-2865, TAP-2725

<!-- docsmcp:end:refs -->
