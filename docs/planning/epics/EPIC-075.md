# Epic 75: Profile-scoped learned data KV — unblock tapps-mcp domain weights

<!-- docsmcp:start:metadata -->
**Status:** Shipped
**Priority:** P2 - Medium
**Estimated LOE:** ~1 week (1 developer)
**Dependencies:** EPIC-074 (independent; can ship in parallel after 074 P0 if capacity allows)
**Blocks:** tapps-mcp TAP-1998
**Linear epic:** TAP-3156

<!-- docsmcp:end:metadata -->

---

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

We are doing this so that tapps-mcp can persist adaptive domain weights and other profile-scoped learned state in brain instead of .tapps-mcp/adaptive/domain_weights.yaml.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:goal -->
## Goal

Add profile_scoped_data Postgres table and brain_profile_set / brain_profile_get MCP + REST tools scoped to negotiated X-Brain-Profile and project_id. Enables TAP-1998 without overloading private_memories.

<!-- docsmcp:end:goal -->

<!-- docsmcp:start:motivation -->
## Motivation

Brain profile negotiation exists but has no read/write surface for learned profile data. DomainWeightStore needs durable per-project, per-profile KV separate from memory tiers and KG entities.

<!-- docsmcp:end:motivation -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [x] profile_scoped_data table with RLS on project_id (migration 024); brain_profile_set stores JSON by (project_id, profile_name, data_key); brain_profile_get returns value_json or ok=false; MCP brain_profile_set/get plus REST POST /v1/profile/data:set and /v1/profile/data:get; tools in full and operator profiles; Postgres integration test round-trip for domain_weights; federation deferred to follow-up

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:stories -->
## Stories

### 75.1 -- Migration profile_scoped_data table with RLS

**Points:** 5

Describe what this story delivers...

**Tasks:**
- [ ] Implement migration profile_scoped_data table with rls
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** Migration profile_scoped_data table with RLS is implemented, tests pass, and documentation is updated.

---

### 75.2 -- brain_profile_set and brain_profile_get service + MCP + REST

**Points:** 8

Describe what this story delivers...

**Tasks:**
- [ ] Implement brain_profile_set and brain_profile_get service + mcp + rest
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** brain_profile_set and brain_profile_get service + MCP + REST is implemented, tests pass, and documentation is updated.

---

### 75.3 -- Profile tool registration, OpenAPI, and integration tests

**Points:** 3

Describe what this story delivers...

**Tasks:**
- [ ] Implement profile tool registration, openapi, and integration tests
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** Profile tool registration, OpenAPI, and integration tests is implemented, tests pass, and documentation is updated.

---

<!-- docsmcp:end:stories -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- Document architecture decisions for **Profile-scoped learned data KV — unblock tapps-mcp domain weights**...

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:non-goals -->
## Out of Scope / Future Considerations

- Cross-project federation of profile data in v1; Replacing static mcp_profiles.yaml config; Hive propagation of domain weights

<!-- docsmcp:end:non-goals -->

<!-- docsmcp:start:files-affected -->
## Files Affected

| File | Lines | Recent Commits | Public Symbols |
|------|-------|----------------|----------------|
| `src/tapps_brain/migrations/private/` | *(not found)* | - | - |
| `src/tapps_brain/services/` | *(not found)* | - | - |
| `src/tapps_brain/mcp_server/` | *(not found)* | - | - |

<!-- docsmcp:end:files-affected -->

<!-- docsmcp:start:related-epics -->
## Related Epics

- **EPIC-066.md** -- references `src/tapps_brain/migrations/private/`
- **EPIC-069-next-session-prompt.md** -- references `src/tapps_brain/migrations/private/`
- **EPIC-070.md** -- references `src/tapps_brain/services/`
- **EPIC-073.md** -- references `src/tapps_brain/mcp_server/`
- **EPIC-074.md** -- references `src/tapps_brain/mcp_server/`, `src/tapps_brain/services/`

<!-- docsmcp:end:related-epics -->

<!-- docsmcp:start:refs -->
## Refs

| Story | Linear |
|---|---|
| STORY-75.1 | TAP-3162 |
| STORY-75.2 | TAP-3163 |
| STORY-75.3 | TAP-3164 |

tapps-mcp consumer: TAP-1998

<!-- docsmcp:end:refs -->
