# Story 66.10 -- pg_tde operator runbook

<!-- docsmcp:start:user-story -->

> **As a** security-conscious operator, **I want** a documented runbook for enabling Percona pg_tde 2.1.2 at-rest encryption on the tapps-brain Postgres deployment, **so that** at-rest encryption is a configured outcome rather than a TODO in the security review, with a clear fallback path for cloud-provider TDE

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 5 | **Size:** M

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that the at-rest encryption story promised by ADR-007 (pg_tde replaces SQLCipher) is delivered as a runbook operators can actually execute. The ADR mentions pg_tde 2.1.2 and Vault/OpenBao integration but currently has no installation, key-rotation, or fallback documentation.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Publish docs/guides/postgres-tde.md covering pg_tde 2.1.2 install on Percona Distribution for PostgreSQL 17, Vault and OpenBao key provider configuration, key rotation procedure, and a fallback table mapping cloud-provider TDE equivalents (AWS RDS, Google CloudSQL, Azure Database for PostgreSQL). Cross-link from ADR-007 and the security/threat-model doc.

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `docs/guides/postgres-tde.md`
- `docs/planning/adr/ADR-007-postgres-only-no-sqlite.md`
- `docs/engineering/threat-model.md`
- `docs/guides/hive-deployment.md`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [ ] Outline the runbook: install, key provider, encrypt schemas, rotation, troubleshooting, cloud fallback (`docs/guides/postgres-tde.md`)
- [ ] Document Percona Distribution for PostgreSQL 17 install steps (`docs/guides/postgres-tde.md`)
- [ ] Document Vault and OpenBao key provider setup with example config (`docs/guides/postgres-tde.md`)
- [ ] Document key rotation procedure with downtime expectations (`docs/guides/postgres-tde.md`)
- [ ] Add cloud-provider TDE fallback table (RDS, CloudSQL, Azure) (`docs/guides/postgres-tde.md`)
- [ ] Cross-link from ADR-007 and threat-model.md (`docs/planning/adr/ADR-007-postgres-only-no-sqlite.md`)
- [ ] Cross-link from hive-deployment.md (`docs/guides/hive-deployment.md`)

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] docs/guides/postgres-tde.md exists and covers install / key provider / rotation / troubleshooting / cloud fallback
- [ ] ADR-007 cross-links to the runbook
- [ ] docs/engineering/threat-model.md cross-links to the runbook
- [ ] docs/guides/hive-deployment.md cross-links to the runbook
- [ ] runbook validated against Percona pg_tde 2.1.2 release notes (released 2026-03-02)
- [ ] cloud fallback table covers AWS RDS
- [ ] Google CloudSQL
- [ ] and Azure Database for PostgreSQL
- [ ] docs-mcp docs_check_links passes on the new file

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [ ] All tasks completed
- [ ] pg_tde operator runbook code reviewed and approved
- [ ] Tests passing (unit + integration)
- [ ] Documentation updated
- [ ] No regressions introduced

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_docsguidespostgrestdemd_exists_covers_install_key_provider_rotation` -- docs/guides/postgres-tde.md exists and covers install / key provider / rotation / troubleshooting / cloud fallback
2. `test_ac2_adr007_crosslinks_runbook` -- ADR-007 cross-links to the runbook
3. `test_ac3_docsengineeringthreatmodelmd_crosslinks_runbook` -- docs/engineering/threat-model.md cross-links to the runbook
4. `test_ac4_docsguideshivedeploymentmd_crosslinks_runbook` -- docs/guides/hive-deployment.md cross-links to the runbook
5. `test_ac5_runbook_validated_against_percona_pgtde_212_release_notes_released` -- runbook validated against Percona pg_tde 2.1.2 release notes (released 2026-03-02)
6. `test_ac6_cloud_fallback_table_covers_aws_rds` -- cloud fallback table covers AWS RDS
7. `test_ac7_google_cloudsql` -- Google CloudSQL
8. `test_ac8_azure_database_postgresql` -- and Azure Database for PostgreSQL
9. `test_ac9_docsmcp_docschecklinks_passes_on_new_file` -- docs-mcp docs_check_links passes on the new file

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- pg_tde requires the Percona Distribution patches — vanilla Postgres 17 will not work. The runbook should be explicit that this is a different distribution choice from vanilla pgvector/pg17. Cloud TDE is generally simpler operationally but locks the deployment to one provider. Vault/OpenBao is the recommended key store per Percona 2.1.2 release notes.

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

- List stories or external dependencies that must complete first...

<!-- docsmcp:end:dependencies -->

<!-- docsmcp:start:invest -->
## INVEST Checklist

- [x] **I**ndependent -- Can be developed and delivered independently
- [ ] **N**egotiable -- Details can be refined during implementation
- [x] **V**aluable -- Delivers value to a user or the system
- [x] **E**stimable -- Team can estimate the effort
- [x] **S**mall -- Completable within one sprint/iteration
- [x] **T**estable -- Has clear criteria to verify completion

<!-- docsmcp:end:invest -->
