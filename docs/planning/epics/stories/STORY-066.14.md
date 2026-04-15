# Story 66.14 -- Final test failure sweep — 90 to zero

<!-- docsmcp:start:user-story -->

> **As a** release manager, **I want** every remaining unit and integration test failure resolved against ephemeral Postgres, **so that** the 3.4.0 release tag can be cut on a green CI run with no skipped or expected failures

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 5 | **Size:** M

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that EPIC-066 finishes the test pass count from 2475 / 90 fail / 74 skip to 2565+ / 0 fail / 74 skip (or fewer skips after STORY-066.13 adds Postgres integration tests). After stories 066.1 through 066.4 land most failures will be fixed, but a long tail of single-test issues will remain. Each one gets a fix, a documented skip with a tracked follow-up, or a deletion if the behaviour was intentionally removed under ADR-007.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

After stories 066.1 through 066.4 land, re-run uv run pytest tests/unit and tests/integration against the ephemeral Postgres CI service. For each remaining failure, classify as fix / skip / delete. Document the classification in the PR. Land surgical fixes one at a time. Target: zero failures and zero unjustified skips before tagging 3.4.0.

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `tests/unit`
- `tests/integration`
- `src/tapps_brain`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [x] Re-run full test suite after STORY-066.1-066.4 land (`tests`)
- [x] Classify each remaining failure as fix / skip / delete with rationale (`tests`)
- [x] Land surgical fixes for the fix bucket (`src/tapps_brain`)
- [x] Add @pytest.mark.skip with tracked-issue reason for the skip bucket (`tests`)
- [x] Delete tests in the delete bucket with a PR comment explaining the ADR-007 removal (`tests`)
- [x] Re-run suite to confirm zero failures (`tests`)
- [x] Update CHANGELOG.md with the resolved test count delta (`CHANGELOG.md`)

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [x] uv run pytest tests/unit returns zero failures against ephemeral Postgres
- [x] uv run pytest tests/integration returns zero failures
- [x] every remaining @pytest.mark.skip references a tracked GitHub issue
- [x] no unjustified skips
- [x] CHANGELOG.md documents the test count delta from EPIC-066
- [x] release-ready.sh full gate passes
- [x] ready to tag 3.4.0

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [x] All tasks completed
- [x] Final test failure sweep — 90 to zero code reviewed and approved
- [x] Tests passing (unit + integration)
- [x] Documentation updated
- [x] No regressions introduced

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_uv_run_pytest_testsunit_returns_zero_failures_against_ephemeral` -- uv run pytest tests/unit returns zero failures against ephemeral Postgres
2. `test_ac2_uv_run_pytest_testsintegration_returns_zero_failures` -- uv run pytest tests/integration returns zero failures
3. `test_ac3_every_remaining_pytestmarkskip_references_tracked_github_issue` -- every remaining @pytest.mark.skip references a tracked GitHub issue
4. `test_ac4_no_unjustified_skips` -- no unjustified skips
5. `test_ac5_changelogmd_documents_test_count_delta_from_epic066` -- CHANGELOG.md documents the test count delta from EPIC-066
6. `test_ac6_releasereadysh_full_gate_passes` -- release-ready.sh full gate passes
7. `test_ac7_ready_tag_340` -- ready to tag 3.4.0

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- This story should land last in the epic. Its work is reactive — the actual surgery depends on what the earlier stories leave behind. Budget time for surprises in cross-platform behaviours (Postgres on Linux vs macOS
- psycopg pool quirks under high concurrency).

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

- EPIC-066 STORY-066.1
- 066.2
- 066.3
- 066.4
- 066.6
- 066.13

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
