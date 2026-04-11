# Story 66.2 -- Bi-temporal as_of filter on PostgresPrivateBackend.search

<!-- docsmcp:start:user-story -->

> **As a** tapps-brain user, **I want** store.search(query, as_of=ts) to return the version of an entry that was valid at the given timestamp, **so that** temporal queries work the same way under Postgres as they did under the v2 SQLite path and historical recall does not silently return only the latest version

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 5 | **Size:** M

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that the v2 SQLite bi-temporal semantics survive into the Postgres-only path. PostgresPrivateBackend.search() currently filters only by tsvector match, memory_group, and time_field; the valid_at / invalid_at / superseded_by columns are stored but not queried. test_search_as_of_returns_old_version exposes this gap by superseding an entry and asserting that store.search(as_of=before_supersede) returns the old version.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Extend PostgresPrivateBackend.search() with an as_of parameter (ISO-8601 string or None). When set, add WHERE clauses (valid_at IS NULL OR valid_at <= as_of) AND (invalid_at IS NULL OR invalid_at > as_of) to the existing tsvector match. Propagate the parameter through MemoryStore.search(). Add an integration test that creates an entry, supersedes it, and verifies as_of recall returns the old version.

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `src/tapps_brain/postgres_private.py`
- `src/tapps_brain/_protocols.py`
- `src/tapps_brain/store.py`
- `tests/unit/test_memory_store.py`
- `tests/integration/test_temporal_integration.py`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [ ] Add as_of: str | None parameter to PrivateBackend.search() protocol signature (`src/tapps_brain/_protocols.py`)
- [ ] Add as_of parameter to PostgresPrivateBackend.search() and append the (valid_at <= as_of) AND (invalid_at > as_of) WHERE clauses (`src/tapps_brain/postgres_private.py`)
- [ ] Propagate as_of through MemoryStore.search() to self._persistence.search() (`src/tapps_brain/store.py`)
- [ ] Verify test_search_as_of_returns_old_version passes (`tests/unit/test_memory_store.py`)
- [ ] Add temporal integration test covering supersede + as_of recall (`tests/integration/test_temporal_integration.py`)

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] PostgresPrivateBackend.search accepts as_of=str|None and applies bi-temporal predicates
- [ ] MemoryStore.search forwards as_of to the backend
- [ ] test_search_as_of_returns_old_version passes against ephemeral Postgres
- [ ] test_search_excludes_superseded passes
- [ ] an integration test confirms supersede + as_of returns the old version
- [ ] parameter is documented in PrivateBackend protocol docstring with reference to migration 001 valid_at/invalid_at columns

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [ ] All tasks completed
- [ ] Bi-temporal as_of filter on PostgresPrivateBackend.search code reviewed and approved
- [ ] Tests passing (unit + integration)
- [ ] Documentation updated
- [ ] No regressions introduced

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_postgresprivatebackendsearch_accepts_asofstrnone_applies_bitemporal` -- PostgresPrivateBackend.search accepts as_of=str|None and applies bi-temporal predicates
2. `test_ac2_memorystoresearch_forwards_asof_backend` -- MemoryStore.search forwards as_of to the backend
3. `test_ac3_testsearchasofreturnsoldversion_passes_against_ephemeral_postgres` -- test_search_as_of_returns_old_version passes against ephemeral Postgres
4. `test_ac4_testsearchexcludessuperseded_passes` -- test_search_excludes_superseded passes
5. `test_ac5_integration_test_confirms_supersede_asof_returns_old_version` -- an integration test confirms supersede + as_of returns the old version
6. `test_ac6_parameter_documented_privatebackend_protocol_docstring_reference` -- parameter is documented in PrivateBackend protocol docstring with reference to migration 001 valid_at/invalid_at columns

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- SQL injection safety: as_of must be passed as a parameterised %s placeholder
- never string-concatenated. Use TIMESTAMPTZ-compatible cast in the WHERE clause via %s::timestamptz. NULL handling matters — entries without valid_at/invalid_at should be visible at all times. The existing _VALID_TIME_FIELDS guard does not apply to as_of since it filters on row-level columns
- not a parameterised time_field.

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
