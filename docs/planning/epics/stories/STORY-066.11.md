# Story 66.11 -- Postgres backup and restore runbook

<!-- docsmcp:start:user-story -->

> **As a** production operator, **I want** a documented backup and point-in-time recovery procedure for the Postgres database holding all tapps-brain durable state, **so that** disaster recovery for memories, hive, federation, audit_log, and diagnostics_history is a rehearsed procedure rather than an improvised one

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 5 | **Size:** M

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that the production-ready claim has a backup story behind it. With ADR-007 every durable byte lives in one Postgres database; losing it loses everything. The runbook is the difference between "use Postgres" and "use Postgres safely".

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Publish docs/guides/postgres-backup.md covering pg_dump for hot backups, base backup + WAL archiving for point-in-time recovery, restoring private/hive/federation schemas independently, and Hive failover to a streaming replica. Include example crontab entries and example pgBackRest config. Cross-link from docs/guides/hive-deployment.md and docs/operations/db-roles-runbook.md.

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `docs/guides/postgres-backup.md`
- `docs/guides/hive-deployment.md`
- `docs/operations/db-roles-runbook.md`
- `docs/operations/postgres-backup-runbook.md`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [x] Outline backup strategies (logical pg_dump, physical base backup + WAL, pgBackRest) (`docs/guides/postgres-backup.md`)
- [x] Document pg_dump --schema-only and --data-only patterns for selective restore (`docs/guides/postgres-backup.md`)
- [x] Document base backup + WAL archiving for point-in-time recovery (`docs/guides/postgres-backup.md`)
- [x] Document Hive failover to a streaming replica with a sample primary_conninfo (`docs/guides/postgres-backup.md`)
- [x] Add example crontab entries and pgBackRest stanza config (`docs/guides/postgres-backup.md`)
- [x] Cross-link from hive-deployment.md and db-roles-runbook.md (`docs/guides/hive-deployment.md`)
- [x] Add a separate ops-facing runbook page (`docs/operations/postgres-backup-runbook.md`)

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [x] docs/guides/postgres-backup.md exists and covers logical / physical / PITR strategies
- [x] schema-independent restore documented for private / hive / federation
- [x] Hive replica failover documented with sample config
- [x] crontab and pgBackRest examples included
- [x] docs/operations/postgres-backup-runbook.md exists for ops on-call
- [x] cross-links from hive-deployment.md and db-roles-runbook.md
- [x] docs-mcp docs_check_links passes on both new files

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [x] All tasks completed
- [x] Postgres backup and restore runbook code reviewed and approved
- [x] Tests passing (unit + integration)
- [x] Documentation updated
- [x] No regressions introduced

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_docsguidespostgresbackupmd_exists_covers_logical_physical_pitr` -- docs/guides/postgres-backup.md exists and covers logical / physical / PITR strategies
2. `test_ac2_schemaindependent_restore_documented_private_hive_federation` -- schema-independent restore documented for private / hive / federation
3. `test_ac3_hive_replica_failover_documented_sample_config` -- Hive replica failover documented with sample config
4. `test_ac4_crontab_pgbackrest_examples_included` -- crontab and pgBackRest examples included
5. `test_ac5_docsoperationspostgresbackuprunbookmd_exists_ops_oncall` -- docs/operations/postgres-backup-runbook.md exists for ops on-call
6. `test_ac6_crosslinks_from_hivedeploymentmd_dbrolesrunbookmd` -- cross-links from hive-deployment.md and db-roles-runbook.md
7. `test_ac7_docsmcp_docschecklinks_passes_on_both_new_files` -- docs-mcp docs_check_links passes on both new files

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- Recommend pgBackRest as the production default; pg_dump only as the dev/test option. The (project_id
- agent_id) tenant key on every private table means partial restores are possible — useful when one customer needs a roll-back without affecting others. Test the runbook against the Docker compose Postgres before publishing.

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

- List stories or external dependencies that must complete first...

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
