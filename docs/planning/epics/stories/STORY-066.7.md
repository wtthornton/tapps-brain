# Story 66.7 -- Connection pool tuning and health JSON pool fields

<!-- docsmcp:start:user-story -->

> **As a** SRE running tapps-brain in production, **I want** to tune the psycopg connection pool via env vars and see pool saturation plus last applied migration version in /health JSON, **so that** I can size the deployment without editing source and detect pool exhaustion or stale schema from monitoring rather than from user pages

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 5 | **Size:** M

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that EPIC-059 STORY-059.7 acceptance criteria close. The psycopg_pool ConnectionPool already exists inside PostgresConnectionManager but its max_size, min_size, and connect_timeout are hard-coded. The /health JSON does not currently expose pool stats or migration version. Both gaps prevent operating tapps-brain at scale.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Add TAPPS_BRAIN_PG_POOL_MAX, TAPPS_BRAIN_PG_POOL_MIN, TAPPS_BRAIN_PG_POOL_CONNECT_TIMEOUT_SECONDS env vars read by PostgresConnectionManager. Document defaults (max=10, min=2, connect_timeout=10s). Add pool_saturation (busy / max_size as float), pool_idle (free connections), and last_migration_version fields to the StoreHealth pydantic model and populate them in run_health_check. Add malformed-DSN parse-time error with a clear message.

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `src/tapps_brain/postgres_connection.py`
- `src/tapps_brain/health_check.py`
- `src/tapps_brain/postgres_migrations.py`
- `tests/unit/test_postgres_connection.py`
- `tests/unit/test_health_check.py`
- `docs/guides/postgres-dsn.md`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [ ] Read TAPPS_BRAIN_PG_POOL_MAX/MIN/CONNECT_TIMEOUT_SECONDS env vars in PostgresConnectionManager.__init__ (`src/tapps_brain/postgres_connection.py`)
- [ ] Add get_pool_stats() method exposing busy / idle / max_size from psycopg_pool.ConnectionPool.get_stats() (`src/tapps_brain/postgres_connection.py`)
- [ ] Add pool_saturation, pool_idle, last_migration_version fields to StoreHealth pydantic model (`src/tapps_brain/health_check.py`)
- [ ] Populate pool fields in run_health_check via cm.get_pool_stats() (`src/tapps_brain/health_check.py`)
- [ ] Populate last_migration_version via get_private_schema_status(dsn) (`src/tapps_brain/postgres_migrations.py`)
- [ ] Add malformed-DSN parse error with descriptive message (`src/tapps_brain/postgres_connection.py`)
- [ ] Unit tests for env-var parsing, get_pool_stats, malformed-DSN error (`tests/unit/test_postgres_connection.py`)
- [ ] Update docs/guides/postgres-dsn.md with the env vars and defaults (`docs/guides/postgres-dsn.md`)

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] PostgresConnectionManager honours TAPPS_BRAIN_PG_POOL_MAX/MIN/CONNECT_TIMEOUT_SECONDS env vars with documented defaults (10/2/10s)
- [ ] get_pool_stats returns busy/idle/max_size from the live pool
- [ ] /health JSON includes pool_saturation
- [ ] pool_idle
- [ ] and last_migration_version under store
- [ ] malformed DSN raises ValueError with a clear message at PostgresConnectionManager construction time
- [ ] docs/guides/postgres-dsn.md documents every env var and the defaults table
- [ ] unit tests cover all four code paths
- [ ] EPIC-059 STORY-059.7 acceptance criteria all checked off

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [ ] All tasks completed
- [ ] Connection pool tuning and health JSON pool fields code reviewed and approved
- [ ] Tests passing (unit + integration)
- [ ] Documentation updated
- [ ] No regressions introduced

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_postgresconnectionmanager_honours` -- PostgresConnectionManager honours TAPPS_BRAIN_PG_POOL_MAX/MIN/CONNECT_TIMEOUT_SECONDS env vars with documented defaults (10/2/10s)
2. `test_ac2_getpoolstats_returns_busyidlemaxsize_from_live_pool` -- get_pool_stats returns busy/idle/max_size from the live pool
3. `test_ac3_health_json_includes_poolsaturation` -- /health JSON includes pool_saturation
4. `test_ac4_poolidle` -- pool_idle
5. `test_ac5_lastmigrationversion_under_store` -- and last_migration_version under store
6. `test_ac6_malformed_dsn_raises_valueerror_clear_message_at` -- malformed DSN raises ValueError with a clear message at PostgresConnectionManager construction time
7. `test_ac7_docsguidespostgresdsnmd_documents_every_env_var_defaults_table` -- docs/guides/postgres-dsn.md documents every env var and the defaults table
8. `test_ac8_unit_tests_cover_all_four_code_paths` -- unit tests cover all four code paths
9. `test_ac9_epic059_story0597_acceptance_criteria_all_checked_off` -- EPIC-059 STORY-059.7 acceptance criteria all checked off

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- psycopg_pool.ConnectionPool.get_stats() returns a dict with pool_size
- pool_available
- pool_max
- pool_min
- requests_waiting. pool_saturation should be (pool_size - pool_available) / pool_max so dashboards see a 0..1 number. Refuse to start if pool_max < 1 or pool_min > pool_max with a clear error.

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

- EPIC-059 STORY-059.5

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
