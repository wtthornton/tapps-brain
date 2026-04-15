# Story 66.3 -- GC archive Postgres table (migration 006)

<!-- docsmcp:start:user-story -->

> **As a** tapps-brain operator, **I want** garbage-collected memory entries to be archived to a Postgres table instead of an archive.jsonl file, **so that** archived rows are queryable through SQL and survive the deletion of the legacy on-disk store directory

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 5 | **Size:** M

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that the GC archive flow has a Postgres home. Stage 2 of EPIC-059 deleted the SQLite store and along with it the .tapps-brain/memory/archive.jsonl file that gc.py used to write archived rows to. test_gc_live_increments_archive_bytes still asserts that running maintenance gc archives at least one row and reports the byte size — under Postgres there is nowhere for those rows to go.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Add migration 006_gc_archive.sql introducing a gc_archive table keyed by (project_id, agent_id, archived_at, key) with a JSONB payload column holding the original entry. Update gc.py to INSERT into the table on archive instead of writing JSONL. Update CLI maintenance gc and the StoreHealthReport gc_archive_bytes counter to query the table size. Update store.py archive accounting to use SUM(octet_length(payload::text)) when reporting bytes.

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `src/tapps_brain/migrations/private/006_gc_archive.sql`
- `src/tapps_brain/gc.py`
- `src/tapps_brain/store.py`
- `src/tapps_brain/postgres_private.py`
- `src/tapps_brain/cli.py`
- `tests/unit/test_memory_store.py`
- `tests/unit/test_gc.py`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [x] Write migration 006_gc_archive.sql defining gc_archive table with project_id/agent_id/archived_at/key/payload/byte_count columns and a (project_id, agent_id, archived_at DESC) index (`src/tapps_brain/migrations/private/006_gc_archive.sql`)
- [x] Add archive_entry / list_archive / total_archive_bytes methods to PostgresPrivateBackend (`src/tapps_brain/postgres_private.py`)
- [x] Update gc.py to call backend.archive_entry instead of writing JSONL (`src/tapps_brain/gc.py`)
- [x] Update MemoryStore gc_archive_bytes_total counter to read from total_archive_bytes (`src/tapps_brain/store.py`)
- [x] Update CLI maintenance gc to read archive size from the table (`src/tapps_brain/cli.py`)
- [x] Verify test_gc_live_increments_archive_bytes passes (`tests/unit/test_memory_store.py`)

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [x] migration 006_gc_archive.sql applies cleanly to a fresh Postgres
- [x] gc_archive table is created with the expected columns and index
- [x] PostgresPrivateBackend exposes archive_entry / list_archive / total_archive_bytes methods
- [x] gc.py writes to gc_archive instead of archive.jsonl
- [x] no archive.jsonl file is created at any point during a GC run
- [x] test_gc_live_increments_archive_bytes passes
- [x] CLI maintenance gc output reports archive byte size correctly
- [x] no regression in test_gc unit tests

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [x] All tasks completed
- [x] GC archive Postgres table (migration 006) code reviewed and approved
- [x] Tests passing (unit + integration)
- [x] Documentation updated
- [x] No regressions introduced

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_migration_006gcarchivesql_applies_cleanly_fresh_postgres` -- migration 006_gc_archive.sql applies cleanly to a fresh Postgres
2. `test_ac2_gcarchive_table_created_expected_columns_index` -- gc_archive table is created with the expected columns and index
3. `test_ac3_postgresprivatebackend_exposes_archiveentry_listarchive` -- PostgresPrivateBackend exposes archive_entry / list_archive / total_archive_bytes methods
4. `test_ac4_gcpy_writes_gcarchive_instead_archivejsonl` -- gc.py writes to gc_archive instead of archive.jsonl
5. `test_ac5_no_archivejsonl_file_created_at_any_point_during_gc_run` -- no archive.jsonl file is created at any point during a GC run
6. `test_ac6_testgcliveincrementsarchivebytes_passes` -- test_gc_live_increments_archive_bytes passes
7. `test_ac7_cli_maintenance_gc_output_reports_archive_byte_size_correctly` -- CLI maintenance gc output reports archive byte size correctly
8. `test_ac8_no_regression_testgc_unit_tests` -- no regression in test_gc unit tests

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- payload column is JSONB so the archived row can be deserialized back into a MemoryEntry if needed for restore operations. byte_count is denormalised at insert time to avoid SUM(octet_length(...)) hot-path queries. Consider an optional gc_archive_ttl_days knob in a future story but out of scope here.

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

- EPIC-066 STORY-066.1 (audit infrastructure pattern)
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
