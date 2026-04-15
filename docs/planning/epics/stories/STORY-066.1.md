# Story 66.1 -- Consolidation merge audit emission

<!-- docsmcp:start:user-story -->

> **As a** tapps-brain operator, **I want** consolidation merges and undos to leave audit trail entries in the audit_log table, **so that** I can reconstruct what the consolidation engine did to my memory store after the fact, the same way save and delete events are already audited

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 3 | **Size:** M

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that the consolidation merge / undo / periodic-scan paths emit audit_log rows the same way save() and delete() do. Stage 2 of EPIC-059 wired audit emission into store.save() and store.delete() but the consolidation flows in auto_consolidation.py have their own write paths and were not updated. Five tests in test_memory_auto_consolidation.py currently fail because they assert on a consolidation audit trail that no longer exists.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Identify every consolidation write path in auto_consolidation.py (consolidate_and_save, undo_merge, run_periodic_scan) and add explicit self._persistence.append_audit calls describing the action, the consolidated key, and the source keys. Use action names that match the existing tests: "consolidation_merge", "consolidation_merge_undo", "periodic_consolidation_scan". The audit_log table already exists (migration 005) so no schema work is needed.

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `src/tapps_brain/auto_consolidation.py`
- `src/tapps_brain/store.py`
- `tests/unit/test_memory_auto_consolidation.py`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [x] Audit auto_consolidation.py for every persistence.save / persistence.delete call that should also emit audit (`src/tapps_brain/auto_consolidation.py`)
- [x] Add append_audit('consolidation_merge', key=consolidated_key, extra={source_keys, similarity}) after merge save (`src/tapps_brain/auto_consolidation.py`)
- [x] Add append_audit('consolidation_merge_undo', key=consolidated_key, extra={restored_keys}) in undo_merge (`src/tapps_brain/auto_consolidation.py`)
- [x] Add append_audit('periodic_consolidation_scan', key='', extra={scanned, merged, skipped}) in run_periodic_scan (`src/tapps_brain/auto_consolidation.py`)
- [x] Verify five failing tests now pass (`tests/unit/test_memory_auto_consolidation.py`)

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [x] test_consolidation_on_save_writes_audit_trail passes against ephemeral Postgres
- [x] test_undo_restores_sources_and_deletes_consolidated passes
- [x] test_undo_rejects_wrong_contradiction_reason passes
- [x] test_second_undo_fails_after_success passes
- [x] test_periodic_scan_writes_audit_when_groups_merged passes
- [x] audit_log table contains consolidation_merge / consolidation_merge_undo / periodic_consolidation_scan event_types after a consolidation flow runs
- [x] no behaviour change visible to non-audit tests

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [x] All tasks completed
- [x] Consolidation merge audit emission code reviewed and approved
- [x] Tests passing (unit + integration)
- [x] Documentation updated
- [x] No regressions introduced

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_testconsolidationonsavewritesaudittrail_passes_against_ephemeral` -- test_consolidation_on_save_writes_audit_trail passes against ephemeral Postgres
2. `test_ac2_testundorestoressourcesanddeletesconsolidated_passes` -- test_undo_restores_sources_and_deletes_consolidated passes
3. `test_ac3_testundorejectswrongcontradictionreason_passes` -- test_undo_rejects_wrong_contradiction_reason passes
4. `test_ac4_testsecondundofailsaftersuccess_passes` -- test_second_undo_fails_after_success passes
5. `test_ac5_testperiodicscanwritesauditwhengroupsmerged_passes` -- test_periodic_scan_writes_audit_when_groups_merged passes
6. `test_ac6_auditlog_table_contains_consolidationmerge_consolidationmergeundo` -- audit_log table contains consolidation_merge / consolidation_merge_undo / periodic_consolidation_scan event_types after a consolidation flow runs
7. `test_ac7_no_behaviour_change_visible_nonaudit_tests` -- no behaviour change visible to non-audit tests

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- append_audit is best-effort and swallows exceptions internally so it cannot break the consolidation hot path. Use json.dumps-safe values in extra. Do not call append_audit inside transactions held by save() — postgres_private.save already commits before returning.

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

- EPIC-059 STORY-059.5 (PostgresPrivateBackend)
- EPIC-066 epic acceptance criteria

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
