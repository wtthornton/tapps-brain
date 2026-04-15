# Story 70.10 -- Native async parity

<!-- docsmcp:start:user-story -->

> **As a** FastAPI-based consumer, **I want** native async methods for every brain operation, **so that** I don't have to wrap sync calls in asyncio.to_thread and tie up threadpool workers

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 5 | **Size:** M | **Status:** In Progress

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that AgentForge's FastAPI event loop is never blocked. Today BrainBridge wraps every sync AgentBrain call in asyncio.to_thread; under load this exhausts the default threadpool.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Describe how **Native async parity** will enable **FastAPI-based consumer** to **native async methods for every brain operation**...

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `src/tapps_brain/aio.py`
- `src/tapps_brain/service.py`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [ ] Implement native async parity (`src/tapps_brain/aio.py`)
- [ ] Write unit tests
- [ ] Update documentation

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] AsyncMemoryStore covers every sync MemoryStore public method (save
- [ ] recall
- [ ] reinforce
- [ ] hive_search
- [ ] relay_export
- [ ] relay_import
- [ ] consolidate
- [ ] gc_run
- [ ] delete
- [ ] search)
- [ ] Uses psycopg AsyncConnection pool internally
- [ ] Async variants of OTel instrumentation (spans handle async context correctly)
- [ ] Benchmark: 100-concurrent async recalls ≤ 2× single recall latency (confirms no serialization)
- [ ] Backward-compat: sync MemoryStore unchanged
- [ ] Unit tests cover async paths with pytest-asyncio

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [ ] Native async parity code reviewed and approved
- [ ] Tests passing (unit + integration)
- [ ] Documentation updated
- [ ] No regressions introduced

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_asyncmemorystore_covers_every_sync_memorystore_public_method_save` -- AsyncMemoryStore covers every sync MemoryStore public method (save
2. `test_ac2_recall` -- recall
3. `test_ac3_reinforce` -- reinforce
4. `test_ac4_hivesearch` -- hive_search
5. `test_ac5_relayexport` -- relay_export
6. `test_ac6_relayimport` -- relay_import
7. `test_ac7_consolidate` -- consolidate
8. `test_ac8_gcrun` -- gc_run
9. `test_ac9_delete` -- delete
10. `test_ac10_search` -- search)
11. `test_ac11_uses_psycopg_asyncconnection_pool_internally` -- Uses psycopg AsyncConnection pool internally
12. `test_ac12_async_variants_otel_instrumentation_spans_handle_async_context` -- Async variants of OTel instrumentation (spans handle async context correctly)
13. `test_ac13_benchmark_100concurrent_async_recalls_2_single_recall_latency_confirms` -- Benchmark: 100-concurrent async recalls ≤ 2× single recall latency (confirms no serialization)
14. `test_ac14_backwardcompat_sync_memorystore_unchanged` -- Backward-compat: sync MemoryStore unchanged
15. `test_ac15_unit_tests_cover_async_paths_pytestasyncio` -- Unit tests cover async paths with pytest-asyncio

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- psycopg3 async is already a transitive dep — reuse
- Handle the BM25 + embedding scoring as CPU-bound — offload via asyncio.to_thread inside the async method so the event loop stays free

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

- STORY-070.2

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
