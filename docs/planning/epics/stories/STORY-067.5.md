# Story 67.5 -- make hive-smoke end-to-end stack smoke test

<!-- docsmcp:start:user-story -->

> **As a** developer or CI pipeline validating the hive stack, **I want** a single make target that boots the full compose stack and asserts all endpoints respond correctly, **so that** regressions in the Docker deployment are caught automatically before they reach operators

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 3 | **Size:** S

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that the acceptance criteria below are met and the feature is delivered. Refine this paragraph to state why this story exists and what it enables.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

There is currently no automated test that verifies the Docker hive stack works end-to-end. An operator can run make hive-deploy and get a broken stack with no indication of failure because all containers show 'Up'. This story adds a hive-smoke Makefile target and a companion scripts/hive_smoke.sh that: starts the full compose stack, waits for health probes to pass (with a timeout), curls /health /ready /snapshot on tapps-brain-http and the /snapshot proxy on tapps-visual, asserts HTTP 200 responses and non-placeholder JSON content, then tears the stack down. The same script is wired into a GitHub Actions workflow so it runs on every PR that touches docker/.

See [Epic 67](../EPIC-067.md) for project context and shared definitions.

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `scripts/hive_smoke.sh`
- `Makefile`
- `.github/workflows/hive-smoke.yml`
- `docker/README.md`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [ ] Create scripts/hive_smoke.sh: compose up, wait-for-healthy loop (max 60s), curl assertions on /health /ready /snapshot, assert generated_at != 1970-01-01, compose down --volumes on exit (`scripts/hive_smoke.sh`)
- [ ] Add hive-smoke target to Makefile that runs scripts/hive_smoke.sh (`Makefile`)
- [ ] Add .github/workflows/hive-smoke.yml: trigger on PR changes to docker/** or Makefile, runs on ubuntu-latest with Docker Compose available, calls make hive-smoke (`.github/workflows/hive-smoke.yml`)
- [ ] Document make hive-smoke in docker/README.md under the make targets table (`docker/README.md`)

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] make hive-smoke boots the full stack
- [ ] passes all curl assertions
- [ ] and tears down cleanly with exit 0
- [ ] make hive-smoke exits non-zero and prints a clear failure message if any endpoint returns a non-200 status or placeholder content
- [ ] The GitHub Actions workflow triggers on PRs touching docker/** and reports pass/fail on the PR
- [ ] Smoke test completes within 3 minutes wall-clock on a standard GitHub Actions runner
- [ ] make hive-smoke is documented in docker/README.md

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

Definition of Done per [Epic 67](../EPIC-067.md).

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_make_hivesmoke_boots_full_stack` -- make hive-smoke boots the full stack
2. `test_ac2_passes_all_curl_assertions` -- passes all curl assertions
3. `test_ac3_tears_down_cleanly_exit_0` -- and tears down cleanly with exit 0
4. `test_ac4_make_hivesmoke_exits_nonzero_prints_clear_failure_message_if_any` -- make hive-smoke exits non-zero and prints a clear failure message if any endpoint returns a non-200 status or placeholder content
5. `test_ac5_github_actions_workflow_triggers_on_prs_touching_docker_reports` -- The GitHub Actions workflow triggers on PRs touching docker/** and reports pass/fail on the PR
6. `test_ac6_smoke_test_completes_within_3_minutes_wallclock_on_standard_github` -- Smoke test completes within 3 minutes wall-clock on a standard GitHub Actions runner
7. `test_ac7_make_hivesmoke_documented_dockerreadmemd` -- make hive-smoke is documented in docker/README.md

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- Use docker compose -f docker/docker-compose.hive.yaml for all compose commands in the script — not the root docker-compose.yml (which is the dev/test-only DB)
- The wait-for-healthy loop should poll /health on tapps-brain-http directly (not through nginx) to decouple probe from proxy
- Use a test-specific password (not 'tapps') when running the smoke test — either export TAPPS_HIVE_PASSWORD=smoke-test-password or write a fixture secrets file
- The GitHub Actions workflow needs to set TAPPS_HIVE_PASSWORD to a non-default value to pass the credential guard added in STORY-067.3

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

- STORY-067.1 (tapps-brain-http service)
- STORY-067.2 (nginx upstream fix)
- STORY-067.3 (credential guard must be bypassable with env var in CI)

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
