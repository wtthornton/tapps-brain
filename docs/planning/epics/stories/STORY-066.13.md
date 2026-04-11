# Story 66.13 -- Postgres integration tests replacing deleted SQLite-coupled tests

<!-- docsmcp:start:user-story -->

> **As a** tapps-brain maintainer, **I want** Postgres-backed integration tests that recreate the behaviour coverage of the 14 SQLite-coupled test files deleted in stage 2, **so that** the rip-out does not leave a permanent coverage hole and we can prove that the v3 behavioural surface is at least as well covered as v2

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 8 | **Size:** L

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that test coverage is restored against Postgres for the behaviours that the deleted SQLite tests used to verify. Stage 2 deleted test_memory_persistence, test_persistence_sqlite_vec, test_sqlite_vec_index, test_sqlite_vec_try_load, test_sqlcipher_util, test_sqlcipher_wiring, test_sqlite_corruption, test_memory_embeddings_persistence, test_feedback, test_store_feedback, test_session_index, test_agent_identity, test_memory_foundation_integration, test_session_index_integration. Some of those tests had no v3 equivalent (SQLCipher tests) and some did (feedback, session_index, agent_identity). The latter need to be rebuilt against Postgres.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

For each deleted test file with a v3 equivalent, write a new tests/integration/ test that exercises the same behaviour against ephemeral Postgres. Use the requires_postgres pytest marker so the unit suite stays Docker-free. Coverage targets: PostgresPrivateBackend CRUD, FeedbackStore round-trip, SessionIndex round-trip, agent identity isolation via (project_id, agent_id) keys, embedding storage and recall via pgvector. Skip the SQLCipher and sqlite-vec extension tests entirely — the underlying technology is gone.

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `tests/integration/test_postgres_private_backend.py`
- `tests/integration/test_feedback_postgres.py`
- `tests/integration/test_session_index_postgres.py`
- `tests/integration/test_agent_identity_postgres.py`
- `tests/integration/test_pgvector_embeddings.py`
- `tests/conftest.py`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [ ] Add a requires_postgres pytest marker registration (`tests/conftest.py`)
- [ ] Write tests/integration/test_postgres_private_backend.py covering CRUD round-trip (`tests/integration/test_postgres_private_backend.py`)
- [ ] Write tests/integration/test_feedback_postgres.py covering FeedbackStore record + query (`tests/integration/test_feedback_postgres.py`)
- [ ] Write tests/integration/test_session_index_postgres.py covering SessionIndex save_chunks + search (`tests/integration/test_session_index_postgres.py`)
- [ ] Write tests/integration/test_agent_identity_postgres.py covering (project_id, agent_id) isolation (`tests/integration/test_agent_identity_postgres.py`)
- [ ] Write tests/integration/test_pgvector_embeddings.py covering embedding storage and knn_search (`tests/integration/test_pgvector_embeddings.py`)
- [ ] Verify all new tests pass against ephemeral Postgres (`tests/integration`)
- [ ] Document requires_postgres marker in AGENTS.md (`AGENTS.md`)

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] tests/integration/test_postgres_private_backend.py covers save / load_all / delete / search round-trips
- [ ] tests/integration/test_feedback_postgres.py covers FeedbackStore record / query / strict-mode rejection
- [ ] tests/integration/test_session_index_postgres.py covers save_chunks / search / delete_expired
- [ ] tests/integration/test_agent_identity_postgres.py covers (project_id
- [ ] agent_id) row isolation across multiple agents
- [ ] tests/integration/test_pgvector_embeddings.py covers embedding write + knn_search recall
- [ ] all new tests marked requires_postgres
- [ ] all new tests pass in CI under STORY-066.6 ephemeral Postgres workflow
- [ ] no flakiness over 5 consecutive runs

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [ ] All tasks completed
- [ ] Postgres integration tests replacing deleted SQLite-coupled tests code reviewed and approved
- [ ] Tests passing (unit + integration)
- [ ] Documentation updated
- [ ] No regressions introduced

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_testsintegrationtestpostgresprivatebackendpy_covers_save_loadall_delete` -- tests/integration/test_postgres_private_backend.py covers save / load_all / delete / search round-trips
2. `test_ac2_testsintegrationtestfeedbackpostgrespy_covers_feedbackstore_record` -- tests/integration/test_feedback_postgres.py covers FeedbackStore record / query / strict-mode rejection
3. `test_ac3_testsintegrationtestsessionindexpostgrespy_covers_savechunks_search` -- tests/integration/test_session_index_postgres.py covers save_chunks / search / delete_expired
4. `test_ac4_testsintegrationtestagentidentitypostgrespy_covers_projectid` -- tests/integration/test_agent_identity_postgres.py covers (project_id
5. `test_ac5_agentid_row_isolation_across_multiple_agents` -- agent_id) row isolation across multiple agents
6. `test_ac6_testsintegrationtestpgvectorembeddingspy_covers_embedding_write` -- tests/integration/test_pgvector_embeddings.py covers embedding write + knn_search recall
7. `test_ac7_all_new_tests_marked_requirespostgres` -- all new tests marked requires_postgres
8. `test_ac8_all_new_tests_pass_ci_under_story0666_ephemeral_postgres_workflow` -- all new tests pass in CI under STORY-066.6 ephemeral Postgres workflow
9. `test_ac9_no_flakiness_over_5_consecutive_runs` -- no flakiness over 5 consecutive runs

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- Each test should set its own (project_id
- agent_id) so parallel test execution does not collide on shared rows. Use a uuid prefix per test session. Tear down with DELETE FROM ... WHERE project_id = %s in fixture finalize. Reuse the InMemoryPrivateBackend conftest fixture only as a fallback for unit-style tests; integration tests must instantiate real PostgresPrivateBackend.

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

- EPIC-066 STORY-066.6 (CI Postgres needed for green-green verification)

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
