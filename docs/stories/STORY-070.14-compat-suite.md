# Story 70.14 -- Compatibility test suite

<!-- docsmcp:start:user-story -->

> **As a** tapps-brain maintainer, **I want** a CI suite that pins embedded AgentBrain behavior, **so that** remote-service work does not silently regress single-process library users

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 3 | **Size:** S | **Status:** In Progress

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that embedded users (including any 3.5.x consumer) are protected from behavioral drift introduced by the service-layer refactor and new transports.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Describe how **Compatibility test suite** will enable **tapps-brain maintainer** to **a CI suite that pins embedded AgentBrain behavior**...

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `tests/compat/test_embedded_3_5_parity.py`
- `.github/workflows/ci.yml`
- `CONTRIBUTING.md`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [ ] Write unit tests for happy path (`tests/compat/test_embedded_3_5_parity.py`)
- [ ] Write edge case tests
- [ ] Add integration test

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] tests/compat/test_embedded_3_5_parity.py covering AgentBrain public methods
- [ ] Runs against live Postgres in CI (already wired via EPIC-066)
- [ ] Asserts return shapes
- [ ] error types
- [ ] confidence scoring
- [ ] hybrid-RAG rank order unchanged
- [ ] CI job fails the PR if any pinned behavior shifts
- [ ] Documented policy in CONTRIBUTING.md: changes that touch these tests require an ADR note

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [ ] Compatibility test suite code reviewed and approved
- [ ] Tests passing (unit + integration)
- [ ] Documentation updated
- [ ] No regressions introduced

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_testscompattestembedded35paritypy_covering_agentbrain_public_methods` -- tests/compat/test_embedded_3_5_parity.py covering AgentBrain public methods
2. `test_ac2_runs_against_live_postgres_ci_already_wired_via_epic066` -- Runs against live Postgres in CI (already wired via EPIC-066)
3. `test_ac3_asserts_return_shapes` -- Asserts return shapes
4. `test_ac4_error_types` -- error types
5. `test_ac5_confidence_scoring` -- confidence scoring
6. `test_ac6_hybridrag_rank_order_unchanged` -- hybrid-RAG rank order unchanged
7. `test_ac7_ci_job_fails_pr_if_any_pinned_behavior_shifts` -- CI job fails the PR if any pinned behavior shifts
8. `test_ac8_documented_policy_contributingmd_changes_touch_these_tests_require_adr` -- Documented policy in CONTRIBUTING.md: changes that touch these tests require an ADR note

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- Document implementation hints, API contracts, data formats...

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
