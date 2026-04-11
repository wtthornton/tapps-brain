# Story 66.9 -- Behavioural parity doc and load smoke benchmark

<!-- docsmcp:start:user-story -->

> **As a** tapps-brain maintainer, **I want** a documented list of every intentional behavioural delta vs the v2 SQLite path plus a load smoke benchmark for 50 concurrent agents against one Postgres, **so that** downstream users know what changed and we can defend the production-ready claim with a measured p95 latency budget

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 8 | **Size:** L

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that EPIC-059 STORY-059.6 acceptance criteria close. Greenfield allows breaking changes but they have to be documented and performance has to be bounded. Without this story we cannot defend "production ready" against operators asking "what's different from v2 and how does it scale".

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Update docs/engineering/v3-behavioral-parity.md to enumerate every intentional delta from the v2 SQLite path (audit emission timing, valid_at semantics, archive flow location, dimension constants, FTS ranking notes). Add tests/benchmarks/load_smoke_postgres.py simulating 50 concurrent agents writing and recalling against one Postgres for 60 seconds. Record p95 latency for save, recall, hive_search. Mark the benchmark requires_postgres so unit suite stays Docker-free. Document target latency budget or "informational only" if the SLO is still pre-GA.

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `docs/engineering/v3-behavioral-parity.md`
- `tests/benchmarks/load_smoke_postgres.py`
- `tests/benchmarks/conftest.py`
- `scripts/load_smoke.py`
- `AGENTS.md`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [ ] Audit existing v3-behavioral-parity.md against actual stage 2 changes (`docs/engineering/v3-behavioral-parity.md`)
- [ ] Enumerate audit timing delta, valid_at semantics, archive flow, dimensions, FTS ranking (`docs/engineering/v3-behavioral-parity.md`)
- [ ] Write tests/benchmarks/load_smoke_postgres.py with 50 concurrent agents using threading (`tests/benchmarks/load_smoke_postgres.py`)
- [ ] Record p95 latency for save, recall, hive_search across the workload (`tests/benchmarks/load_smoke_postgres.py`)
- [ ] Mark with @pytest.mark.requires_postgres and @pytest.mark.benchmark (`tests/benchmarks/load_smoke_postgres.py`)
- [ ] Document the latency budget (or informational status) in the parity doc (`docs/engineering/v3-behavioral-parity.md`)
- [ ] Add a make benchmark-postgres target (`AGENTS.md`)

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] docs/engineering/v3-behavioral-parity.md enumerates every intentional v3 vs v2 delta with code references
- [ ] tests/benchmarks/load_smoke_postgres.py runs 50 concurrent agents for 60 seconds against one Postgres
- [ ] p95 latency recorded for save / recall / hive_search and stored as benchmark output
- [ ] benchmark marked requires_postgres so it does not run in the unit suite
- [ ] documented latency budget or explicit "informational only" status
- [ ] AGENTS.md documents how to run the benchmark
- [ ] EPIC-059 STORY-059.6 acceptance criteria all checked off

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [ ] All tasks completed
- [ ] Behavioural parity doc and load smoke benchmark code reviewed and approved
- [ ] Tests passing (unit + integration)
- [ ] Documentation updated
- [ ] No regressions introduced

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_docsengineeringv3behavioralparitymd_enumerates_every_intentional_v3_vs` -- docs/engineering/v3-behavioral-parity.md enumerates every intentional v3 vs v2 delta with code references
2. `test_ac2_testsbenchmarksloadsmokepostgrespy_runs_50_concurrent_agents_60_seconds` -- tests/benchmarks/load_smoke_postgres.py runs 50 concurrent agents for 60 seconds against one Postgres
3. `test_ac3_p95_latency_recorded_save_recall_hivesearch_stored_as_benchmark_output` -- p95 latency recorded for save / recall / hive_search and stored as benchmark output
4. `test_ac4_benchmark_marked_requirespostgres_so_does_not_run_unit_suite` -- benchmark marked requires_postgres so it does not run in the unit suite
5. `test_ac5_documented_latency_budget_or_explicit_informational_only_status` -- documented latency budget or explicit "informational only" status
6. `test_ac6_agentsmd_documents_how_run_benchmark` -- AGENTS.md documents how to run the benchmark
7. `test_ac7_epic059_story0596_acceptance_criteria_all_checked_off` -- EPIC-059 STORY-059.6 acceptance criteria all checked off

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- Use threading not asyncio — the core code is synchronous by design. Each "agent" is a thread that holds its own MemoryStore instance pointed at the same DSN. Run the benchmark for a fixed wall-clock duration not a fixed operation count so the report is comparable across hardware. Capture pool_saturation from /health every second so spikes are visible.

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

- EPIC-066 STORY-066.7 (pool tuning so the benchmark can size the pool correctly)

<!-- docsmcp:end:dependencies -->

<!-- docsmcp:start:invest -->
## INVEST Checklist

- [ ] **I**ndependent -- Can be developed and delivered independently
- [ ] **N**egotiable -- Details can be refined during implementation
- [x] **V**aluable -- Delivers value to a user or the system
- [x] **E**stimable -- Team can estimate the effort
- [ ] **S**mall -- Completable within one sprint/iteration
- [x] **T**estable -- Has clear criteria to verify completion

<!-- docsmcp:end:invest -->
