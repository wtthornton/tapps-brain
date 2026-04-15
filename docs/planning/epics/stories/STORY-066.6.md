# Story 66.6 -- CI workflow with ephemeral Postgres service container

<!-- docsmcp:start:user-story -->

> **As a** CI maintainer, **I want** the GitHub Actions test workflow to spin up pgvector/pg17 as a service container and run the full pytest suite against it, **so that** EPIC-066 stories can land safely without manual local Docker runs and the EPIC-059 STORY-059.8 acceptance criterion finally closes

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 5 | **Size:** M

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that ADR-007 has a green CI signal. After stage 2 the local Docker compose path works (make brain-up + TAPPS_BRAIN_DATABASE_URL) but CI has no Postgres service container, so the unit suite cannot run end-to-end in CI. Without this story every PR after stage 2 ships untested.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Update .github/workflows/test.yml (or create one if missing) to declare a pgvector/pgvector:pg17 service container with the same credentials as docker-compose.yml, set TAPPS_BRAIN_DATABASE_URL to postgresql://tapps:tapps@postgres:5432/tapps_dev, run apply_private_migrations + apply_hive_migrations + apply_federation_migrations in a setup step, then invoke uv run pytest tests/unit and tests/integration. Wall-clock budget under 15 minutes. Document the same steps in AGENTS.md so contributors can reproduce locally.

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `.github/workflows/test.yml`
- `AGENTS.md`
- `Makefile`
- `docker-compose.yml`
- `scripts/apply_all_migrations.py`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [x] Inventory current .github/workflows/* and identify the test job (`.github/workflows/test.yml`)
- [x] Add pgvector/pgvector:pg17 service container with health check and credentials matching docker-compose.yml (`.github/workflows/test.yml`)
- [x] Set TAPPS_BRAIN_DATABASE_URL env var on the test job (`.github/workflows/test.yml`)
- [x] Add a setup step that runs apply_private_migrations + apply_hive_migrations + apply_federation_migrations (`.github/workflows/test.yml`)
- [x] Write scripts/apply_all_migrations.py that the workflow setup step invokes (`scripts/apply_all_migrations.py`)
- [x] Update AGENTS.md with the local equivalent (make brain-up + apply migrations + pytest) (`AGENTS.md`)
- [x] Add a make brain-test target chaining brain-up, apply migrations, and pytest (`Makefile`)
- [x] Verify CI workflow runs full unit + integration suite under 15 minutes (`.github/workflows/test.yml`)

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [x] .github/workflows/test.yml declares pgvector/pgvector:pg17 service container
- [x] TAPPS_BRAIN_DATABASE_URL env var set on the test job
- [x] migrations applied before pytest
- [x] full unit suite passes in CI
- [x] full integration suite passes in CI
- [x] wall-clock for the test job under 15 minutes
- [x] AGENTS.md documents the equivalent local commands
- [x] make brain-test target works end-to-end on a clean clone
- [x] EPIC-059 STORY-059.8 acceptance criteria all checked off

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [x] All tasks completed
- [x] CI workflow with ephemeral Postgres service container code reviewed and approved
- [x] Tests passing (unit + integration)
- [x] Documentation updated
- [x] No regressions introduced

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_githubworkflowstestyml_declares_pgvectorpgvectorpg17_service_container` -- .github/workflows/test.yml declares pgvector/pgvector:pg17 service container
2. `test_ac2_tappsbraindatabaseurl_env_var_set_on_test_job` -- TAPPS_BRAIN_DATABASE_URL env var set on the test job
3. `test_ac3_migrations_applied_before_pytest` -- migrations applied before pytest
4. `test_ac4_full_unit_suite_passes_ci` -- full unit suite passes in CI
5. `test_ac5_full_integration_suite_passes_ci` -- full integration suite passes in CI
6. `test_ac6_wallclock_test_job_under_15_minutes` -- wall-clock for the test job under 15 minutes
7. `test_ac7_agentsmd_documents_equivalent_local_commands` -- AGENTS.md documents the equivalent local commands
8. `test_ac8_make_braintest_target_works_endtoend_on_clean_clone` -- make brain-test target works end-to-end on a clean clone
9. `test_ac9_epic059_story0598_acceptance_criteria_all_checked_off` -- EPIC-059 STORY-059.8 acceptance criteria all checked off

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- GitHub Actions service containers expose hostnames matching the service name (postgres in this case)
- not localhost. Use the credentials from docker-compose.yml verbatim (tapps/tapps/tapps_dev). Health-check the service with pg_isready before running migrations. If the existing CI uses uv
- the same uv commands work on Actions runners with no additional setup. Plan for sentence-transformers model download cache via actions/cache so the test job does not re-download BAAI/bge-small-en-v1.5 every run.

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

- EPIC-066 STORY-066.1
- 066.2
- 066.3
- 066.4 (test failures must be down to zero before CI can be green)

<!-- docsmcp:end:dependencies -->

<!-- docsmcp:start:invest -->
## INVEST Checklist

- [x] **I**ndependent -- Can be developed and delivered independently
- [x] **N**egotiable -- Details can be refined during implementation
- [x] **V**aluable -- Delivers value to a user or the system
- [x] **E**stimable -- Team can estimate the effort
- [x] **S**mall -- Completable within one sprint/iteration
- [x] **T**estable -- Has clear criteria to verify completion

<!-- docsmcp:end:invest -->
