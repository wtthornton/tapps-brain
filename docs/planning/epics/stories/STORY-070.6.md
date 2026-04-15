# Story 70.6 -- Update Docker image and compose for unified HTTP/MCP surface

<!-- docsmcp:start:user-story -->

> **As a** tapps-brain operator, **I want** one container image to serve both /mcp (agents) and /admin/* (ops) from a single Uvicorn worker, **so that** deployment is simple and remote agents have a single endpoint per tenant

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 3 | **Size:** S

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that the deployed image matches the new architecture: one ASGI process, both surfaces, unchanged Postgres dependencies, and no regression for local stdio users.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Describe how **Update Docker image and compose for unified HTTP/MCP surface** will enable **tapps-brain operator** to **one container image to serve both /mcp (agents) and /admin/* (ops) from a single Uvicorn worker**...

See [Epic 70](../EPIC-070.md) for project context and shared definitions.

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `docker/Dockerfile.http`
- `docker/docker-compose.hive.yaml`
- `scripts/smoke_test_docker.sh`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [x] Update Dockerfile.http CMD/ENTRYPOINT to uvicorn (`docker/Dockerfile.http`)
- [x] Verify docker-compose.hive.yaml port mappings and env (`docker/docker-compose.hive.yaml`)
- [x] Smoke test: docker-compose up then curl /health and initialize /mcp (`scripts/smoke_test_docker.sh`)

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] Dockerfile.http runs uvicorn with the FastAPI app factory
- [ ] docker-compose.hive.yaml exposes the container on ports 8080 (data) and 8088 (admin) from the same process
- [ ] Dockerfile.migrate unchanged
- [ ] .mcp.json continues to work for local stdio agents (docker run tapps-brain-mcp)
- [ ] Image boots in under 5 seconds and /health returns 200 within 2 seconds of container start

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

Definition of Done per [Epic 70](../EPIC-070.md).

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_dockerfilehttp_runs_uvicorn_fastapi_app_factory` -- Dockerfile.http runs uvicorn with the FastAPI app factory
2. `test_ac2_dockercomposehiveyaml_exposes_container_on_ports_8080_data_8088_admin` -- docker-compose.hive.yaml exposes the container on ports 8080 (data) and 8088 (admin) from the same process
3. `test_ac3_dockerfilemigrate_unchanged` -- Dockerfile.migrate unchanged
4. `test_ac4_mcpjson_continues_work_local_stdio_agents_docker_run_tappsbrainmcp` -- .mcp.json continues to work for local stdio agents (docker run tapps-brain-mcp)
5. `test_ac5_image_boots_under_5_seconds_health_returns_200_within_2_seconds` -- Image boots in under 5 seconds and /health returns 200 within 2 seconds of container start

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- Document implementation hints, API contracts, data formats...

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
