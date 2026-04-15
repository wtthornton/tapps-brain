# Story 70.6 -- Bulk operations

<!-- docsmcp:start:user-story -->

> **As a** AgentForge learning loop, **I want** to save or recall many entries in one network round-trip, **so that** throughput doesn't collapse under per-item network latency

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 5 | **Size:** M | **Status:** Proposed

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that high-frequency writes (learning loop, session capture) and warm-cache hydration (recall_many) scale when the brain is remote. Per-item calls are 10-100× slower than batched calls over the network.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Describe how **Bulk operations** will enable **AgentForge learning loop** to **to save or recall many entries in one network round-trip**...

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `src/tapps_brain/service.py`
- `src/tapps_brain/http_adapter.py`
- `src/tapps_brain/mcp_server.py`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [ ] Implement bulk operations (`src/tapps_brain/service.py`)
- [ ] Write unit tests
- [ ] Update documentation

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] POST /v1/remember:batch with {entries: [...]
- [ ] max 100}
- [ ] GET /v1/recall:batch with {queries: [...]
- [ ] max 50}
- [ ] POST /v1/reinforce:batch
- [ ] Single Postgres transaction per batch; partial failure returns per-item status array
- [ ] MCP tools memory_save_many / memory_recall_many / memory_reinforce_many
- [ ] Batch size limits configurable via TAPPS_BRAIN_MAX_BATCH_SIZE (default 100 writes / 50 reads)
- [ ] OTel span per batch with child spans per item
- [ ] Benchmark: 100-entry save batch ≤ 3× single-entry save latency over local network

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [ ] Bulk operations code reviewed and approved
- [ ] Tests passing (unit + integration)
- [ ] Documentation updated
- [ ] No regressions introduced

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_post_v1rememberbatch_entries` -- POST /v1/remember:batch with {entries: [...]
2. `test_ac2_max_100` -- max 100}
3. `test_ac3_get_v1recallbatch_queries` -- GET /v1/recall:batch with {queries: [...]
4. `test_ac4_max_50` -- max 50}
5. `test_ac5_post_v1reinforcebatch` -- POST /v1/reinforce:batch
6. `test_ac6_single_postgres_transaction_per_batch_partial_failure_returns_peritem` -- Single Postgres transaction per batch; partial failure returns per-item status array
7. `test_ac7_mcp_tools_memorysavemany_memoryrecallmany_memoryreinforcemany` -- MCP tools memory_save_many / memory_recall_many / memory_reinforce_many
8. `test_ac8_batch_size_limits_configurable_via_tappsbrainmaxbatchsize_default_100` -- Batch size limits configurable via TAPPS_BRAIN_MAX_BATCH_SIZE (default 100 writes / 50 reads)
9. `test_ac9_otel_span_per_batch_child_spans_per_item` -- OTel span per batch with child spans per item
10. `test_ac10_benchmark_100entry_save_batch_3_singleentry_save_latency_over_local` -- Benchmark: 100-entry save batch ≤ 3× single-entry save latency over local network

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- Use Postgres COPY or multi-row INSERT for save_many
- recall_many returns a list-of-lists preserving query order
- Per-item error shape follows STORY-070.4 taxonomy

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
