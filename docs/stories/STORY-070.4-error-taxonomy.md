# Story 70.4 -- Error taxonomy + retry-ability semantics

<!-- docsmcp:start:user-story -->

> **As a** client implementor, **I want** stable error codes that distinguish retry-safe from retry-never, **so that** client resilience logic is trivial and not string-matching prose

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 3 | **Size:** S | **Status:** Proposed

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that downstream circuit-breakers and retry policies can be written once against a documented taxonomy instead of ad-hoc status parsing.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Describe how **Error taxonomy + retry-ability semantics** will enable **client implementor** to **stable error codes that distinguish retry-safe from retry-never**...

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `src/tapps_brain/errors.py`
- `src/tapps_brain/http_adapter.py`
- `src/tapps_brain/mcp_server.py`
- `docs/guides/errors.md`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [ ] Implement error taxonomy + retry-ability semantics (`src/tapps_brain/errors.py`)
- [ ] Write unit tests
- [ ] Update documentation

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] Documented taxonomy: brain_degraded=503 retry-safe
- [ ] brain_rate_limited=429 retry-with-backoff
- [ ] project_not_registered=403 retry-never
- [ ] invalid_request=400 retry-never
- [ ] idempotency_conflict=409 retry-never
- [ ] not_found=404 retry-never
- [ ] internal_error=500 retry-safe-once
- [ ] All HTTP responses carry {error: code
- [ ] message: str
- [ ] retry_after?: int
- [ ] project_id?: str}
- [ ] All MCP JSON-RPC errors carry {code: int
- [ ] message: str
- [ ] data: {error: code
- [ ] ...}}
- [ ] EPIC-069 existing 403/-32002 responses migrated to new taxonomy without breaking shape
- [ ] Taxonomy documented in docs/guides/errors.md with a table of code → HTTP status → JSON-RPC code → retry policy
- [ ] Unit tests assert every exception type maps to its documented code

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [ ] Error taxonomy + retry-ability semantics code reviewed and approved
- [ ] Tests passing (unit + integration)
- [ ] Documentation updated
- [ ] No regressions introduced

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_documented_taxonomy_braindegraded503_retrysafe` -- Documented taxonomy: brain_degraded=503 retry-safe
2. `test_ac2_brainratelimited429_retrywithbackoff` -- brain_rate_limited=429 retry-with-backoff
3. `test_ac3_projectnotregistered403_retrynever` -- project_not_registered=403 retry-never
4. `test_ac4_invalidrequest400_retrynever` -- invalid_request=400 retry-never
5. `test_ac5_idempotencyconflict409_retrynever` -- idempotency_conflict=409 retry-never
6. `test_ac6_notfound404_retrynever` -- not_found=404 retry-never
7. `test_ac7_internalerror500_retrysafeonce` -- internal_error=500 retry-safe-once
8. `test_ac8_all_http_responses_carry_error_code` -- All HTTP responses carry {error: code
9. `test_ac9_message_str` -- message: str
10. `test_ac10_retryafter_int` -- retry_after?: int
11. `test_ac11_projectid_str` -- project_id?: str}
12. `test_ac12_all_mcp_jsonrpc_errors_carry_code_int` -- All MCP JSON-RPC errors carry {code: int
13. `test_ac13_message_str` -- message: str
14. `test_ac14_data_error_code` -- data: {error: code
15. `test_ac15_story_acceptance` -- ...}}
16. `test_ac16_epic069_existing_40332002_responses_migrated_new_taxonomy_without` -- EPIC-069 existing 403/-32002 responses migrated to new taxonomy without breaking shape
17. `test_ac17_taxonomy_documented_docsguideserrorsmd_table_code_http_status_jsonrpc` -- Taxonomy documented in docs/guides/errors.md with a table of code → HTTP status → JSON-RPC code → retry policy
18. `test_ac18_unit_tests_assert_every_exception_type_maps_documented_code` -- Unit tests assert every exception type maps to its documented code

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- Define error codes in tapps_brain/errors.py as a single enum
- Use RFC 7807 Problem Details shape as HTTP body where possible
- Retry-After header set for 429 and 503

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

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
