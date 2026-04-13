# Story 67.3 -- Default-credential guard in make hive-deploy

<!-- docsmcp:start:user-story -->

> **As a** operator deploying the hive stack for the first time, **I want** the deploy tooling to refuse to proceed if I have not changed the default database password, **so that** a trivially weak credential cannot accidentally reach production

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 2 | **Size:** S

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that the acceptance criteria below are met and the feature is delivered. Refine this paragraph to state why this story exists and what it enables.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

docker/secrets/tapps_hive_password.txt currently contains the literal string 'tapps' — the same insecure default documented in the quick-start. Nothing stops an operator from shipping this to production. This story adds a pre-flight check to the hive-deploy Makefile target that reads the password file and aborts with a clear error if the value equals the default. It also updates docker/README.md with a 'Before you deploy' checklist and adds a .gitignore rule to prevent the secrets file from being committed.

See [Epic 67](../EPIC-067.md) for project context and shared definitions.

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `Makefile`
- `docker/README.md`
- `.gitignore`
- `docker/secrets/tapps_hive_password.txt.example`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [ ] Add a check-hive-secrets Makefile target that greps docker/secrets/tapps_hive_password.txt for the literal value 'tapps' and exits 1 with an actionable error message if found (`Makefile`)
- [ ] Make hive-deploy depend on check-hive-secrets so the guard runs before any docker compose commands (`Makefile`)
- [ ] Add docker/secrets/ to .gitignore (or verify it is already excluded) (`.gitignore`)
- [ ] Add a 'Before you deploy' checklist section to docker/README.md listing: change tapps_hive_password.txt, set TAPPS_BRAIN_HTTP_AUTH_TOKEN, configure TLS (`docker/README.md`)
- [ ] Add docker/secrets/tapps_hive_password.txt.example with a placeholder comment explaining how to generate a strong password (`docker/secrets/tapps_hive_password.txt.example`)

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] make hive-deploy prints an error and exits non-zero when docker/secrets/tapps_hive_password.txt contains 'tapps'
- [ ] make hive-deploy proceeds normally when the password file contains any other value
- [ ] docker/secrets/ is listed in .gitignore so the actual secrets file cannot be committed
- [ ] docker/README.md contains a 'Before you deploy' section with at minimum: change the DB password
- [ ] set the auth token
- [ ] configure TLS

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

Definition of Done per [Epic 67](../EPIC-067.md).

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_make_hivedeploy_prints_error_exits_nonzero` -- make hive-deploy prints an error and exits non-zero when docker/secrets/tapps_hive_password.txt contains 'tapps'
2. `test_ac2_make_hivedeploy_proceeds_normally_password_file_contains_any_other` -- make hive-deploy proceeds normally when the password file contains any other value
3. `test_ac3_dockersecrets_listed_gitignore_so_actual_secrets_file_cannot_committed` -- docker/secrets/ is listed in .gitignore so the actual secrets file cannot be committed
4. `test_ac4_dockerreadmemd_contains_before_you_deploy_section_at_minimum_change_db` -- docker/README.md contains a 'Before you deploy' section with at minimum: change the DB password
5. `test_ac5_set_auth_token` -- set the auth token
6. `test_ac6_configure_tls` -- configure TLS

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- The check is a simple shell one-liner in the Makefile: grep -qxF 'tapps' docker/secrets/tapps_hive_password.txt and abort — no Python needed
- Do not delete the existing tapps_hive_password.txt (it is needed for local dev); just prevent it from reaching a production deploy
- Consider also checking that TAPPS_BRAIN_HTTP_AUTH_TOKEN is non-empty in the same pre-flight target

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
