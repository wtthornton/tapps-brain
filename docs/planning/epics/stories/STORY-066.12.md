# Story 66.12 -- Engineering docs drift sweep

<!-- docsmcp:start:user-story -->

> **As a** documentation maintainer, **I want** every reference to SQLite, sqlite-vec, SQLCipher, MemoryPersistence, and the legacy SQLite-named methods removed from docs/engineering and docs/guides, **so that** future readers do not encounter stale guidance that contradicts the Postgres-only architecture

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 5 | **Size:** M

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that the documentation surface matches the source-level rip-out. Stage 2 updated the high-traffic docs (CLAUDE.md, README.md, ADR-007, EPIC-059) but docs/engineering/* and docs/guides/* still hold references to SQLite as a supported path. docs-mcp docs_check_drift over the entire tree returns 552 unfiltered items; the SQLite-named subset is currently small but the broader engineering surface needs a sweep.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Run docs-mcp docs_check_drift over docs/engineering and docs/guides scoped to all SQLite-related public names (MemoryPersistence, SqliteAgentRegistryBackend, sqlite_vec_knn_search, sqlite_vec_row_count, sqlcipher_enabled, connect_sqlite, FTS5, memory.db, archive.jsonl, encryption_migrate). Fix every stale reference. Re-run drift; target drift_score >= 0.95 over the engineering surface and zero hits when filtered by the SQLite name list.

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `docs/engineering/system-architecture.md`
- `docs/engineering/data-stores-and-schema.md`
- `docs/engineering/call-flows.md`
- `docs/engineering/code-inventory-and-doc-gaps.md`
- `docs/engineering/features-and-technologies.md`
- `docs/engineering/optional-features-matrix.md`
- `docs/engineering/v3-behavioral-parity.md`
- `docs/engineering/threat-model.md`
- `docs/guides/getting-started.md`
- `docs/guides/observability.md`
- `docs/guides/sqlite-to-postgres-meeting-notes.md`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [x] Run docs_check_drift over docs/engineering with SQLite name list (`docs/engineering`)
- [x] Run docs_check_drift over docs/guides with SQLite name list (`docs/guides`)
- [x] Update docs/engineering/system-architecture.md to remove per-agent memory.db references (`docs/engineering/system-architecture.md`)
- [x] Update docs/engineering/data-stores-and-schema.md to describe migrations 001-006 instead of v1-v17 SQLite schemas (`docs/engineering/data-stores-and-schema.md`)
- [x] Update docs/engineering/v3-behavioral-parity.md FeedbackStore row to reflect Postgres feedback_events table (`docs/engineering/v3-behavioral-parity.md`)
- [x] Update docs/engineering/threat-model.md to reflect pg_tde instead of SQLCipher (`docs/engineering/threat-model.md`)
- [x] Archive or delete docs/planning/sqlite-to-postgres-meeting-notes.md if it has served its purpose (`docs/planning/sqlite-to-postgres-meeting-notes.md`)
- [x] Re-run docs_check_drift and confirm zero SQLite name hits (`docs/engineering`)

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [x] docs_check_drift over docs/engineering with the SQLite name list returns zero items
- [x] docs_check_drift over docs/guides with the SQLite name list returns zero items
- [x] docs/engineering/system-architecture.md describes the (project_id
- [x] agent_id) tenant key model
- [x] docs/engineering/data-stores-and-schema.md describes private/hive/federation Postgres schemas with migration version refs
- [x] docs/engineering/v3-behavioral-parity.md FeedbackStore row updated to reflect Postgres backend
- [x] docs/engineering/threat-model.md updated to reference pg_tde
- [x] docs/planning/sqlite-to-postgres-meeting-notes.md archived to docs/planning/archive/ or deleted
- [x] docs-mcp docs_check_links passes on every changed file

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [x] All tasks completed
- [x] Engineering docs drift sweep code reviewed and approved
- [x] Tests passing (unit + integration)
- [x] Documentation updated
- [x] No regressions introduced

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_docscheckdrift_over_docsengineering_sqlite_name_list_returns_zero_items` -- docs_check_drift over docs/engineering with the SQLite name list returns zero items
2. `test_ac2_docscheckdrift_over_docsguides_sqlite_name_list_returns_zero_items` -- docs_check_drift over docs/guides with the SQLite name list returns zero items
3. `test_ac3_docsengineeringsystemarchitecturemd_describes_projectid` -- docs/engineering/system-architecture.md describes the (project_id
4. `test_ac4_agentid_tenant_key_model` -- agent_id) tenant key model
5. `test_ac5_docsengineeringdatastoresandschemamd_describes_privatehivefederation` -- docs/engineering/data-stores-and-schema.md describes private/hive/federation Postgres schemas with migration version refs
6. `test_ac6_docsengineeringv3behavioralparitymd_feedbackstore_row_updated_reflect` -- docs/engineering/v3-behavioral-parity.md FeedbackStore row updated to reflect Postgres backend
7. `test_ac7_docsengineeringthreatmodelmd_updated_reference_pgtde` -- docs/engineering/threat-model.md updated to reference pg_tde
8. `test_ac8_docsplanningsqlitetopostgresmeetingnotesmd_archived_docsplanningarchive` -- docs/planning/sqlite-to-postgres-meeting-notes.md archived to docs/planning/archive/ or deleted
9. `test_ac9_docsmcp_docschecklinks_passes_on_every_changed_file` -- docs-mcp docs_check_links passes on every changed file

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- The docs are not load-bearing for the runtime so this story is low-risk. Be careful to preserve historical context where the prose narrates the migration journey — those references to SQLite should be kept in the past tense rather than deleted
- since they explain how we got here.

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

- EPIC-066 STORY-066.10 (pg_tde runbook to cross-link from threat model)

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
