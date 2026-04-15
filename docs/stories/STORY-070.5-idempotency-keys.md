# Story 70.5 -- Idempotency keys for writes

<!-- docsmcp:start:user-story -->

> **As a** AgentForge worker, **I want** to safely retry writes after a timeout without double-inserting, **so that** AgentForge's bounded write queue can flush aggressively without corruption

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 3 | **Size:** S | **Status:** Proposed

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that network failures on the write path don't force clients to choose between at-least-once (double-insert) or at-most-once (data loss).

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Describe how **Idempotency keys for writes** will enable **AgentForge worker** to **to safely retry writes after a timeout without double-inserting**...

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `src/tapps_brain/service.py`
- `src/tapps_brain/http_adapter.py`
- `migrations/010_idempotency_keys.sql`
- `docs/guides/idempotency.md`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [ ] Implement idempotency keys for writes (`src/tapps_brain/service.py`)
- [ ] Write unit tests
- [ ] Update documentation

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] POST /v1/remember and /v1/reinforce accept X-Idempotency-Key header (UUID)
- [ ] Duplicate key within 24h returns the original response body and status
- [ ] New idempotency_keys Postgres table (migration 010): key (PK)
- [ ] project_id
- [ ] response_hash
- [ ] created_at
- [ ] TTL sweep runs as part of existing GC
- [ ] Feature-flagged OFF by default via TAPPS_BRAIN_IDEMPOTENCY=1 for first release
- [ ] MCP equivalent — params._meta.idempotency_key has identical semantics

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [ ] Idempotency keys for writes code reviewed and approved
- [ ] Tests passing (unit + integration)
- [ ] Documentation updated
- [ ] No regressions introduced

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_post_v1remember_v1reinforce_accept_xidempotencykey_header_uuid` -- POST /v1/remember and /v1/reinforce accept X-Idempotency-Key header (UUID)
2. `test_ac2_duplicate_key_within_24h_returns_original_response_body_status` -- Duplicate key within 24h returns the original response body and status
3. `test_ac3_new_idempotencykeys_postgres_table_migration_010_key_pk` -- New idempotency_keys Postgres table (migration 010): key (PK)
4. `test_ac4_projectid` -- project_id
5. `test_ac5_responsehash` -- response_hash
6. `test_ac6_createdat` -- created_at
7. `test_ac7_ttl_sweep_runs_as_part_existing_gc` -- TTL sweep runs as part of existing GC
8. `test_ac8_featureflagged_off_by_default_via_tappsbrainidempotency1_first_release` -- Feature-flagged OFF by default via TAPPS_BRAIN_IDEMPOTENCY=1 for first release
9. `test_ac9_mcp_equivalent_paramsmetaidempotencykey_identical_semantics` -- MCP equivalent — params._meta.idempotency_key has identical semantics

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- Store hashed response body; on replay re-serve the hash decoded (or refetch entry)
- Key is (project_id
- key) — keys do not collide across tenants

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

- STORY-070.2
- STORY-070.3

<!-- docsmcp:end:dependencies -->

<!-- docsmcp:start:invest -->
## INVEST Checklist

- [ ] **I**ndependent -- Can be developed and delivered independently
- [ ] **N**egotiable -- Details can be refined during implementation
- [x] **V**aluable -- Delivers value to a user or the system
- [x] **E**stimable -- Team can estimate the effort
- [x] **S**mall -- Completable within one sprint/iteration
- [x] **T**estable -- Has clear criteria to verify completion

<!-- docsmcp:end:invest -->
