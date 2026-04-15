# Story 70.2 -- Transport-agnostic service layer

<!-- docsmcp:start:user-story -->

> **As a** tapps-brain maintainer, **I want** memory operation logic factored into one transport-agnostic module, **so that** HTTP routes and MCP tools share one implementation instead of duplicating logic

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 5 | **Size:** M | **Status:** Done

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that adding the HTTP memory CRUD in STORY-070.3 does not copy-paste MCP tool logic. Without this refactor we end up with two divergent code paths.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Describe how **Transport-agnostic service layer** will enable **tapps-brain maintainer** to **memory operation logic factored into one transport-agnostic module**...

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `src/tapps_brain/service.py`
- `src/tapps_brain/mcp_server.py`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [ ] Implement transport-agnostic service layer (`src/tapps_brain/service.py`)
- [ ] Write unit tests
- [ ] Update documentation

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] New module tapps_brain/service.py with typed methods mirroring MCP tool signatures
- [ ] Returns typed dataclasses or Pydantic models
- [ ] not dict[str
- [ ] Any]
- [ ] MCP tool handlers become thin wrappers that call service methods and serialize results
- [ ] Zero behavior change — existing MCP integration tests pass unchanged
- [ ] Service methods take project_id + agent_id + scope + group as explicit parameters (not from env)
- [ ] Unit tests cover service methods directly without MCP harness

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [ ] Transport-agnostic service layer code reviewed and approved
- [ ] Tests passing (unit + integration)
- [ ] Documentation updated
- [ ] No regressions introduced

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. Existing MCP test suite passes unchanged
2. New unit tests call service.recall() / service.save() directly without an MCP client

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- Pure refactor — do not change any public API
- Service methods must be sync; async parity handled in STORY-070.10
- This is the gate for STORY-070.3 (HTTP CRUD) and STORY-070.7 (per-call identity)

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

- None

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
