# Story 67.1 -- Add Dockerfile.http and tapps-brain-http compose service

<!-- docsmcp:start:user-story -->

> **As a** operator deploying tapps-brain via Docker Compose, **I want** a tapps-brain-http container to run automatically as part of the hive stack, **so that** the HttpAdapter is reachable on port 8080 within the Docker network so nginx can proxy /snapshot to it

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 5 | **Size:** M

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that the acceptance criteria below are met and the feature is delivered. Refine this paragraph to state why this story exists and what it enables.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

The HttpAdapter (src/tapps_brain/http_adapter.py) exposes /health, /ready, /metrics, /snapshot, and /info on port 8080. It is never started by the current docker-compose.hive.yaml. nginx-visual.conf proxies /snapshot to tapps-brain-mcp:8080 — a hostname that does not exist on the compose network — so every /snapshot request 502s. This story adds docker/Dockerfile.http to build a minimal image that starts the HttpAdapter, and adds a tapps-brain-http service to docker-compose.hive.yaml with the hive DSN and private DSN wired as env vars, a /health probe, depends_on tapps-hive-db (healthy), and restart: unless-stopped.

See [Epic 67](../EPIC-067.md) for project context and shared definitions.

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `docker/Dockerfile.http`
- `docker/docker-compose.hive.yaml`
- `src/tapps_brain/cli.py`
- `Makefile`
- `tests/integration/test_http_adapter_compose.py`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [ ] Audit cli.py for an existing serve/http subcommand; if absent, add tapps-brain serve (or equivalent) that constructs HttpAdapter and calls adapter.start() then blocks (`src/tapps_brain/cli.py`)
- [ ] Create docker/Dockerfile.http: FROM python:3.13-slim, install wheel with [cli] extra, EXPOSE 8080, ENTRYPOINT ["tapps-brain", "serve"] (`docker/Dockerfile.http`)
- [ ] Add tapps-brain-http service to docker-compose.hive.yaml: build Dockerfile.http, port 8080 internal only, env TAPPS_BRAIN_HIVE_DSN and TAPPS_BRAIN_DATABASE_URL from secrets/env, healthcheck GET /health, depends_on tapps-hive-db healthy, restart: unless-stopped (`docker/docker-compose.hive.yaml`)
- [ ] Add TAPPS_BRAIN_HTTP_AUTH_TOKEN to docker/secrets/ and reference it as a Docker secret in the compose service (`docker/docker-compose.hive.yaml`)
- [ ] Update Makefile hive-build target to include building Dockerfile.http image (`Makefile`)
- [ ] Add integration test: boot tapps-brain-http via subprocess, assert /health returns 200 and /ready returns 200 when DB is available (`tests/integration/test_http_adapter_compose.py`)

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] docker compose -f docker/docker-compose.hive.yaml up tapps-brain-http starts without error and passes the /health probe within 30s
- [ ] GET http://localhost:8080/health returns 200 with JSON body containing status ok
- [ ] GET http://localhost:8080/ready returns 200 when tapps-hive-db is healthy and 503 when it is not
- [ ] TAPPS_BRAIN_HTTP_AUTH_TOKEN is set as a Docker secret and /info returns 401 without the token
- [ ] make hive-build builds the Dockerfile.http image successfully

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

Definition of Done per [Epic 67](../EPIC-067.md).

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_docker_compose_f_dockerdockercomposehiveyaml_up_tappsbrainhttp_starts` -- docker compose -f docker/docker-compose.hive.yaml up tapps-brain-http starts without error and passes the /health probe within 30s
2. `test_ac2_get_httplocalhost8080health_returns_200_json_body_containing_status_ok` -- GET http://localhost:8080/health returns 200 with JSON body containing status ok
3. `test_ac3_get_httplocalhost8080ready_returns_200_tappshivedb_healthy_503_not` -- GET http://localhost:8080/ready returns 200 when tapps-hive-db is healthy and 503 when it is not
4. `test_ac4_tappsbrainhttpauthtoken_set_as_docker_secret_info_returns_401_without` -- TAPPS_BRAIN_HTTP_AUTH_TOKEN is set as a Docker secret and /info returns 401 without the token
5. `test_ac5_make_hivebuild_builds_dockerfilehttp_image_successfully` -- make hive-build builds the Dockerfile.http image successfully

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- HttpAdapter constructor signature: HttpAdapter(host
- port
- dsn
- store
- auth_token) — dsn wires the DB connection for /ready; store is optional and needed for /snapshot
- Check cli.py for existing subcommands before adding serve — it may already have an http or serve subcommand from STORY-060.3
- The Dockerfile.http pattern mirrors Dockerfile.migrate: install wheel
- different ENTRYPOINT

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

- STORY-066 (wheel must be built and installable)
- tapps-hive-db service healthy

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
