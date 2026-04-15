# Story 70.15 -- Docker + docs — one binary, both transports

<!-- docsmcp:start:user-story -->

> **As a** operator, **I want** one container that speaks both HTTP and MCP-streamable-HTTP, **so that** deployment is a single service, not two

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 3 | **Size:** S | **Status:** In Progress

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that deploying "tapps-brain as a service" is actually one docker service, not a fleet. Operators shouldn't need two containers to cover two transports of the same system.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Describe how **Docker + docs — one binary, both transports** will enable **operator** to **one container that speaks both HTTP and MCP-streamable-HTTP**...

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `src/tapps_brain/cli.py`
- `docker/Dockerfile.http`
- `docker/docker-compose.hive.yaml`
- `docs/guides/deployment.md`
- `docs/guides/migration-3.5-to-3.6.md`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [ ] Implement docker + docs — one binary, both transports (`src/tapps_brain/cli.py`)
- [ ] Write unit tests
- [ ] Update documentation

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] tapps-brain serve starts HTTP adapter + streamable-HTTP MCP on distinct ports in one process
- [ ] Config via TAPPS_BRAIN_HTTP_PORT and TAPPS_BRAIN_MCP_HTTP_PORT
- [ ] Graceful shutdown stops both
- [ ] docker/docker-compose.hive.yaml updated — single tapps-brain service replaces tapps-brain-http
- [ ] docs/guides/deployment.md shows the shared-service pattern end-to-end with an AgentForge client snippet and an AGENT.md snippet
- [ ] Healthcheck aggregates both transports — container unhealthy if either is down
- [ ] Migration guide for operators currently running 3.5.x with only HTTP

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [ ] Docker + docs — one binary, both transports code reviewed and approved
- [ ] Tests passing (unit + integration)
- [ ] Documentation updated
- [ ] No regressions introduced

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_tappsbrain_serve_starts_http_adapter_streamablehttp_mcp_on_distinct` -- tapps-brain serve starts HTTP adapter + streamable-HTTP MCP on distinct ports in one process
2. `test_ac2_config_via_tappsbrainhttpport_tappsbrainmcphttpport` -- Config via TAPPS_BRAIN_HTTP_PORT and TAPPS_BRAIN_MCP_HTTP_PORT
3. `test_ac3_graceful_shutdown_stops_both` -- Graceful shutdown stops both
4. `test_ac4_dockerdockercomposehiveyaml_updated_single_tappsbrain_service_replaces` -- docker/docker-compose.hive.yaml updated — single tapps-brain service replaces tapps-brain-http
5. `test_ac5_docsguidesdeploymentmd_shows_sharedservice_pattern_endtoend_agentforge` -- docs/guides/deployment.md shows the shared-service pattern end-to-end with an AgentForge client snippet and an AGENT.md snippet
6. `test_ac6_healthcheck_aggregates_both_transports_container_unhealthy_if_either` -- Healthcheck aggregates both transports — container unhealthy if either is down
7. `test_ac7_migration_guide_operators_currently_running_35x_only_http` -- Migration guide for operators currently running 3.5.x with only HTTP

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- Document implementation hints, API contracts, data formats...

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

- STORY-070.1
- STORY-070.3
- STORY-070.9

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
