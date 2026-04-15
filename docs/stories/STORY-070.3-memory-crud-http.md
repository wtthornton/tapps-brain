# Story 70.3 -- Memory CRUD on HttpAdapter

<!-- docsmcp:start:user-story -->

> **As a** HTTP-first client, **I want** memory CRUD endpoints on the tapps-brain HTTP adapter, **so that** consumers that don't speak MCP (cURL, non-Python services, browser dashboards) can read and write memory

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 8 | **Size:** L | **Status:** Done

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that the dockerized tapps-brain HTTP service actually exposes memory — today it only has /health, /ready, /metrics, /snapshot, /admin/projects. No way to remember/recall without MCP.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Describe how **Memory CRUD on HttpAdapter** will enable **HTTP-first client** to **memory CRUD endpoints on the tapps-brain HTTP adapter**...

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `src/tapps_brain/http_adapter.py`
- `src/tapps_brain/service.py`
- `docs/guides/http-api.md`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [ ] Implement memory crud on httpadapter (`src/tapps_brain/http_adapter.py`)
- [ ] Write unit tests
- [ ] Update documentation

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] POST /v1/remember — body: key
- [ ] value
- [ ] tier
- [ ] source
- [ ] agent_scope
- [ ] memory_group
- [ ] confidence; returns entry id
- [ ] GET /v1/recall?q=...&max_results=...&min_confidence=... returns ranked entries
- [ ] POST /v1/reinforce with {key
- [ ] confidence_boost}
- [ ] DELETE /v1/entries/{id}
- [ ] POST /v1/hive/search with {message
- [ ] namespaces
- [ ] max_results}
- [ ] POST /v1/relay/export and /v1/relay/import
- [ ] All endpoints reuse EPIC-069 project_resolver (_meta body > X-Tapps-Project header > TAPPS_BRAIN_PROJECT)
- [ ] All endpoints call into service layer (STORY-070.2) — zero duplication
- [ ] Bearer auth identical to /admin/projects
- [ ] OpenAPI 3.1 spec auto-generated and served at /openapi.json (extends existing)
- [ ] Integration tests with a real client hitting a live adapter

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [ ] Memory CRUD on HttpAdapter code reviewed and approved
- [ ] Tests passing (unit + integration)
- [ ] Documentation updated
- [ ] No regressions introduced

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. Happy path per endpoint
2. Project resolver — _meta vs header vs env precedence
3. Unregistered project returns structured 403
4. Invalid body returns structured 400
5. Large value rejected with 413

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- Do not introduce FastAPI — the adapter already uses stdlib http.server; keep it lean OR introduce a well-scoped httpserver library if routing complexity warrants
- Structured errors follow taxonomy from STORY-070.4
- Bodies are JSON; camelCase on the wire
- snake_case internally (consistent with existing adapter)

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

- STORY-070.2

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
