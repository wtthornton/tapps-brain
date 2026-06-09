# Epic 74: Experience event query API — unblock tapps-mcp metrics migration

<!-- docsmcp:start:metadata -->
**Status:** Shipped
**Priority:** P0 - Critical
**Estimated LOE:** ~1-2 weeks (1 developer)
**Dependencies:** EPIC-076 (experience_events + brain_record_event already shipped)
**Blocks:** tapps-mcp TAP-1997 phase 2, TAP-1996 local-state removal
**Linear epic:** TAP-3155

<!-- docsmcp:end:metadata -->

---

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

We are doing this so that tapps-mcp can read back experience event payloads written via brain_record_event and retire per-project .tapps-mcp/metrics/*.jsonl local state. Writes already land in experience_events.payload; reads are blocked because brain_get_neighbors returns KG structure only, not event payloads.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:goal -->
## Goal

Ship brain_query_events (MCP + REST) to query experience_events by event_type, time range, and optional file-path filter; add EntitySpec type/id shorthand so tapps-mcp quality_metric writes link KG entities; document the quality_metric contract. Unblocks tapps-mcp TAP-1997 phase 2 and TAP-1996 epic.

<!-- docsmcp:end:goal -->

<!-- docsmcp:start:motivation -->
## Motivation

tapps-mcp emits quality_metric, quality_gate_fail, and checklist_outcome events on every tool call. Phase 1.5 duplicates rows via memory_save under metrics:tool_call:<call_id> because brain cannot return stored payloads. Dashboard and tapps_stats need score, duration_ms, gate_passed, and timestamps from Postgres, not local JSONL.

<!-- docsmcp:end:motivation -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [x] brain_query_events returns full payload round-trip for quality_metric events filtered by file path; REST POST /v1/experience:query mirrors MCP; index on (project_id, event_type, event_time DESC) ships in migration 023; EntitySpec accepts type/id shorthand from tapps-mcp; docs list quality_metric as known event type with smoke example; tool registered in full, operator, and reviewer profiles

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:stories -->
## Stories

### 74.1 -- brain_query_events service, MCP tool, and REST endpoint

**Points:** 8

Describe what this story delivers...

**Tasks:**
- [ ] Implement brain_query_events service, mcp tool, and rest endpoint
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** brain_query_events service, MCP tool, and REST endpoint is implemented, tests pass, and documentation is updated.

---

### 74.2 -- Migration 023 index and integration round-trip tests

**Points:** 5

Describe what this story delivers...

**Tasks:**
- [ ] Implement migration 023 index and integration round-trip tests
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** Migration 023 index and integration round-trip tests is implemented, tests pass, and documentation is updated.

---

### 74.3 -- EntitySpec type/id shorthand coercion for tapps-mcp entities

**Points:** 3

Describe what this story delivers...

**Tasks:**
- [ ] Implement entityspec type/id shorthand coercion for tapps-mcp entities
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** EntitySpec type/id shorthand coercion for tapps-mcp entities is implemented, tests pass, and documentation is updated.

---

### 74.4 -- Document quality_metric contract and smoke example

**Points:** 2

Describe what this story delivers...

**Tasks:**
- [ ] Implement document quality_metric contract and smoke example
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** Document quality_metric contract and smoke example is implemented, tests pass, and documentation is updated.

---

### 74.5 -- brain_get_neighbors optional entity_refs auto-resolve

**Points:** 5

Describe what this story delivers...

**Tasks:**
- [ ] Implement brain_get_neighbors optional entity_refs auto-resolve
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** brain_get_neighbors optional entity_refs auto-resolve is implemented, tests pass, and documentation is updated.

---

<!-- docsmcp:end:stories -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- Document architecture decisions for **Experience event query API — unblock tapps-mcp metrics migration**...

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:non-goals -->
## Out of Scope / Future Considerations

- UUIDv5 deterministic entity IDs (use brain_resolve_entity instead); extending brain_get_neighbors to return event payloads; experience_event_entities junction table (defer until entity_id UUID filter needed); Federation of profile data

<!-- docsmcp:end:non-goals -->

<!-- docsmcp:start:files-affected -->
## Files Affected

| File | Lines | Recent Commits | Public Symbols |
|------|-------|----------------|----------------|
| `src/tapps_brain/services/kg_service.py` | 909 | 5 recent: 3c90694 feat(TAP-2866): resilient experience wr... | 9 functions |
| `src/tapps_brain/mcp_server/tools_kg.py` | 795 | 5 recent: 7d7478a fix(ci): clear pre-existing lint/format... | 1 functions |
| `src/tapps_brain/http_adapter.py` | 4122 | 5 recent: 3c90694 feat(TAP-2866): resilient experience wr... | 4 classes, 5 functions |
| `src/tapps_brain/experience.py` | 806 | 5 recent: 3213972 fix(TAP-2868): evidence without attachm... | 8 classes |

<!-- docsmcp:end:files-affected -->

<!-- docsmcp:start:related-epics -->
## Related Epics

- **EPIC-065.md** -- references `src/tapps_brain/http_adapter.py`
- **EPIC-067.md** -- references `src/tapps_brain/http_adapter.py`
- **EPIC-070.md** -- references `src/tapps_brain/http_adapter.py`
- **EPIC-073.md** -- references `src/tapps_brain/http_adapter.py`

<!-- docsmcp:end:related-epics -->

<!-- docsmcp:start:refs -->
## Refs

| Story | Linear |
|---|---|
| STORY-74.1 | TAP-3157 |
| STORY-74.2 | TAP-3158 |
| STORY-74.3 | TAP-3159 |
| STORY-74.4 | TAP-3160 |
| STORY-74.5 | TAP-3161 |

tapps-mcp consumer: TAP-1996, TAP-1997, TAP-2000, TAP-2003

<!-- docsmcp:end:refs -->
