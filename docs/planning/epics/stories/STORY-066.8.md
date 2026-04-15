# Story 66.8 -- Auto-migrate on startup gate

<!-- docsmcp:start:user-story -->

> **As a** tapps-brain operator deploying a new version, **I want** MemoryStore to optionally apply pending Postgres migrations at startup when TAPPS_BRAIN_AUTO_MIGRATE=1 is set, **so that** single-host deployments do not need a separate migration step but multi-host deployments are explicitly opt-in to avoid downgrade footguns

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 3 | **Size:** S

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that EPIC-059 STORY-059.3 acceptance criteria close. Currently the operator must run apply_private_migrations manually before MemoryStore.__init__ can succeed against an empty database. For local dev and single-host deployments that is friction. For multi-host deployments auto-migration would be a footgun (a stale binary could re-apply a forward migration on a newer schema). Gate the behaviour behind an explicit env var and refuse to auto-migrate if the current DB schema version is greater than what the bundled migrations cover.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Add a TAPPS_BRAIN_AUTO_MIGRATE env var (default 0). When set to 1, MemoryStore.__init__ runs apply_private_migrations(dsn) before constructing PostgresPrivateBackend. Detect "DB ahead of bundled migrations" by comparing current_version vs max(bundled_versions); raise a clear MigrationDowngradeError when the DB is newer. Log every applied migration at INFO. Document the env var in CLAUDE.md and AGENTS.md.

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `src/tapps_brain/store.py`
- `src/tapps_brain/postgres_migrations.py`
- `src/tapps_brain/agent_brain.py`
- `tests/unit/test_postgres_migrations.py`
- `CLAUDE.md`
- `AGENTS.md`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [x] Read TAPPS_BRAIN_AUTO_MIGRATE env var in MemoryStore.__init__ (`src/tapps_brain/store.py`)
- [x] Call apply_private_migrations(dsn) when the gate is set (`src/tapps_brain/store.py`)
- [x] Add MigrationDowngradeError exception type to postgres_migrations.py (`src/tapps_brain/postgres_migrations.py`)
- [x] Detect current DB version > max bundled version and raise MigrationDowngradeError (`src/tapps_brain/postgres_migrations.py`)
- [x] Wire AgentBrain to honour TAPPS_BRAIN_AUTO_MIGRATE (`src/tapps_brain/agent_brain.py`)
- [x] Unit tests for the gate, the downgrade refusal, and the no-op when DB is up to date (`tests/unit/test_postgres_migrations.py`)
- [x] Document TAPPS_BRAIN_AUTO_MIGRATE in CLAUDE.md env var table (`CLAUDE.md`)
- [x] Document the same in AGENTS.md (`AGENTS.md`)

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [x] MemoryStore honours TAPPS_BRAIN_AUTO_MIGRATE=1 by running pending migrations before opening the backend
- [x] default behaviour (env var unset or 0) is unchanged
- [x] MigrationDowngradeError raised when DB version exceeds max bundled version
- [x] every applied migration logged at INFO with version and filename
- [x] env var documented in CLAUDE.md env var table and AGENTS.md
- [x] unit tests cover gate-on / gate-off / downgrade-refused / clean-DB / partial-migration paths

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [x] All tasks completed
- [x] Auto-migrate on startup gate code reviewed and approved
- [x] Tests passing (unit + integration)
- [x] Documentation updated
- [x] No regressions introduced

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_memorystore_honours_tappsbrainautomigrate1_by_running_pending` -- MemoryStore honours TAPPS_BRAIN_AUTO_MIGRATE=1 by running pending migrations before opening the backend
2. `test_ac2_default_behaviour_env_var_unset_or_0_unchanged` -- default behaviour (env var unset or 0) is unchanged
3. `test_ac3_migrationdowngradeerror_raised_db_version_exceeds_max_bundled_version` -- MigrationDowngradeError raised when DB version exceeds max bundled version
4. `test_ac4_every_applied_migration_logged_at_info_version_filename` -- every applied migration logged at INFO with version and filename
5. `test_ac5_env_var_documented_claudemd_env_var_table_agentsmd` -- env var documented in CLAUDE.md env var table and AGENTS.md
6. `test_ac6_unit_tests_cover_gateon_gateoff_downgraderefused_cleandb` -- unit tests cover gate-on / gate-off / downgrade-refused / clean-DB / partial-migration paths

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- Use apply_private_migrations(dsn
- dry_run=False) directly. The migration runner already records versions in private_schema_version. Concurrent multi-host startup races are still a risk even with this gate — document that production deployments should run a one-shot migration job before rolling out new binaries.

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

- EPIC-059 STORY-059.4

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
