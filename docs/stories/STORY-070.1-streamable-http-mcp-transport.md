# Story 70.1 -- Streamable-HTTP MCP transport

<!-- docsmcp:start:user-story -->

> **As a** tapps-brain operator, **I want** to run a single brain container that many MCP clients connect to over HTTP, **so that** AgentForge workers and remote agents can share one brain instance without subprocess fan-out

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 5 | **Size:** M | **Status:** Done

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that the MCP server is reachable over a network by many clients concurrently. Today FastMCP runs stdio-only, which is fundamentally one-subprocess-per-client and blocks the "shared service" deployment model.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Describe how **Streamable-HTTP MCP transport** will enable **tapps-brain operator** to **to run a single brain container that many MCP clients connect to over HTTP**...

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `src/tapps_brain/mcp_server.py`
- `docker/Dockerfile.http`
- `docs/guides/mcp-transports.md`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [ ] Implement streamable-http mcp transport (`src/tapps_brain/mcp_server.py`)
- [ ] Write unit tests
- [ ] Update documentation

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] tapps-brain-mcp accepts --transport streamable-http with --host/--port flags
- [ ] stdio transport remains default and unchanged
- [ ] The same FastMCP tool registrations serve both transports with zero duplication
- [ ] Bearer auth via Authorization header gated by TAPPS_BRAIN_HTTP_AUTH_TOKEN
- [ ] params._meta passthrough (project_id / agent_id / traceparent) works identically on both transports
- [ ] Integration test: 8 concurrent Python MCP clients hit one server and all succeed
- [ ] Dockerfile.http exposes the MCP port alongside the HTTP adapter port
- [ ] Docs updated in docs/guides/mcp-transports.md

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [ ] Streamable-HTTP MCP transport code reviewed and approved
- [ ] Tests passing (unit + integration)
- [ ] Documentation updated
- [ ] No regressions introduced

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. Stdio smoke — existing test suite passes unchanged
2. Streamable-HTTP smoke — spawn server
3. connect with Python MCP client
4. call memory_recall
5. Concurrency — 8 parallel clients
6. all recall calls succeed within P99 latency budget
7. Auth — unauthenticated request returns 401 on HTTP transport; stdio unaffected

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- Verify mcp SDK >= 1.2 supports streamable-http; pin accordingly
- Reuse HttpAdapter's auth token env var — do not introduce a second auth mechanism
- Keep operator-tools gating (TAPPS_BRAIN_OPERATOR_TOOLS) working identically on both transports

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
