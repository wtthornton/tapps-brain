# Story 70.11 -- Official TappsBrainClient (sync + async)

<!-- docsmcp:start:user-story -->

> **As a** integrator, **I want** one official Python client that speaks HTTP or MCP, **so that** consuming tapps-brain is a one-line import regardless of deployment topology

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 8 | **Size:** L | **Status:** Proposed

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that AgentForge's port from embedded AgentBrain to remote tapps-brain is a near-drop-in swap. Without an official client, every consumer writes and maintains its own HTTP/MCP adapter — duplicating effort and drifting from the server.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Describe how **Official TappsBrainClient (sync + async)** will enable **integrator** to **one official Python client that speaks HTTP or MCP**...

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `src/tapps_brain/client/__init__.py`
- `src/tapps_brain/client/http_backend.py`
- `src/tapps_brain/client/mcp_backend.py`
- `src/tapps_brain/client/protocol.py`
- `docs/guides/client.md`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [ ] Implement official tappsbrainclient (sync + async) (`src/tapps_brain/client/__init__.py`)
- [ ] Write unit tests
- [ ] Update documentation

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] tapps_brain.client.TappsBrainClient (sync) and AsyncTappsBrainClient with method parity against AgentBrain
- [ ] URL scheme selects transport: http(s):// → HTTP adapter; mcp+stdio:// → subprocess; mcp+http:// → streamable-HTTP MCP
- [ ] One Protocol (BrainClientProtocol) implemented by HTTPBackend
- [ ] MCPStdioBackend
- [ ] MCPHttpBackend
- [ ] Pooled httpx.AsyncClient for HTTP; bounded subprocess pool for stdio
- [ ] Per-call identity params (agent_id
- [ ] scope
- [ ] group) accepted and propagated
- [ ] Errors parsed into taxonomy exceptions (BrainDegraded
- [ ] BrainRateLimited
- [ ] ProjectNotRegistered
- [ ] etc.)
- [ ] Idempotency key automatically generated if not passed for retries
- [ ] Bundled typed models (from OpenAPI) so callers get completion on responses
- [ ] Docs + quick-start example in docs/guides/client.md
- [ ] Published as part of the tapps-brain wheel — no separate package

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [ ] Official TappsBrainClient (sync + async) code reviewed and approved
- [ ] Tests passing (unit + integration)
- [ ] Documentation updated
- [ ] No regressions introduced

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. Unit: each backend against a mock server
2. Integration: real HTTP adapter + real stdio subprocess + real streamable-HTTP MCP
3. Client-side retry honors Retry-After on 429/503
4. Unregistered project raises ProjectNotRegistered not generic HTTPError

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- Design target: < 500 LOC for the client layer by leaning on httpx and the mcp SDK
- Connection pooling and retries handled by httpx for HTTP backend
- For MCP stdio
- maintain a small subprocess pool keyed by (project_id
- env) with idle timeout
- Include a minimal health-check method that maps to /ready

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

- STORY-070.1
- STORY-070.3
- STORY-070.4
- STORY-070.10

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
