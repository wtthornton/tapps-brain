# Story 70.8 -- Per-tenant auth tokens

<!-- docsmcp:start:user-story -->

> **As a** multi-tenant brain operator, **I want** auth tokens that resolve to a single project_id, **so that** a leaked token cannot reach another tenant's memory

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 5 | **Size:** M | **Status:** Proposed

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that the shared-service deployment can be actually shared. Today TAPPS_BRAIN_HTTP_AUTH_TOKEN is a single global secret — anyone with it can read any project.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Describe how **Per-tenant auth tokens** will enable **multi-tenant brain operator** to **auth tokens that resolve to a single project_id**...

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `src/tapps_brain/auth.py`
- `src/tapps_brain/project_registry.py`
- `src/tapps_brain/cli.py`
- `migrations/011_project_tokens.sql`
- `docs/guides/auth.md`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [ ] Implement auth flow (`src/tapps_brain/auth.py`)
- [ ] Add token generation/validation
- [ ] Add session management

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] project_profiles gains hashed_token column (migration 011)
- [ ] Auth middleware tries per-tenant tokens first; falls back to global token if TAPPS_BRAIN_PER_TENANT_AUTH not set
- [ ] Resolver: Authorization bearer → hash → project_id; sets tenant resolver result implicitly
- [ ] CLI: tapps-brain project rotate-token <slug> prints new raw token once
- [ ] CLI: tapps-brain project revoke-token <slug>
- [ ] Feature flag TAPPS_BRAIN_PER_TENANT_AUTH=1 (default off for 3.5.x compat)
- [ ] Integration test: token A cannot recall project B entries (returns 403)
- [ ] Docs: docs/guides/auth.md with threat model + rotation runbook

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [ ] Per-tenant auth tokens code reviewed and approved
- [ ] Tests passing (unit + integration)
- [ ] Documentation updated
- [ ] No regressions introduced

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_projectprofiles_gains_hashedtoken_column_migration_011` -- project_profiles gains hashed_token column (migration 011)
2. `test_ac2_auth_middleware_tries_pertenant_tokens_first_falls_back_global_token_if` -- Auth middleware tries per-tenant tokens first; falls back to global token if TAPPS_BRAIN_PER_TENANT_AUTH not set
3. `test_ac3_resolver_authorization_bearer_hash_projectid_sets_tenant_resolver` -- Resolver: Authorization bearer → hash → project_id; sets tenant resolver result implicitly
4. `test_ac4_cli_tappsbrain_project_rotatetoken_slug_prints_new_raw_token_once` -- CLI: tapps-brain project rotate-token <slug> prints new raw token once
5. `test_ac5_cli_tappsbrain_project_revoketoken_slug` -- CLI: tapps-brain project revoke-token <slug>
6. `test_ac6_feature_flag_tappsbrainpertenantauth1_default_off_35x_compat` -- Feature flag TAPPS_BRAIN_PER_TENANT_AUTH=1 (default off for 3.5.x compat)
7. `test_ac7_integration_test_token_cannot_recall_project_b_entries_returns_403` -- Integration test: token A cannot recall project B entries (returns 403)
8. `test_ac8_docs_docsguidesauthmd_threat_model_rotation_runbook` -- Docs: docs/guides/auth.md with threat model + rotation runbook

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- Hash tokens with argon2id (matches typical modern practice)
- Raw token printed once at creation; never retrievable
- Global token path remains for single-tenant deployments — do not remove

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

- STORY-070.3

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
